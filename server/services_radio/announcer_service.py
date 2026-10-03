import asyncio
import time
import random
import numpy as np
from typing import Dict, Optional, List, Tuple
from collections import deque
from services import log_service
from services import usage_tracking
from services.task_utils import spawn
from services_radio import crossfade_plan
from services_radio.dj_content_bank import content_bank
from services_radio.sting_service import midtrack_max_len
from config.settings import settings

FILL_DEFAULT_MS = 3000
FILL_LATE_MS = 8000
SPACE_START_PCT = 0.1
SPACE_END_PCT = 0.85
VOCAL_PAD_S = 0.4
LYRIC_ALIGNMENT_MIN = 0.3
LYRIC_SPAN_MIN = 0.3
SPACE_LOUDNESS_PCT = 40


class AnnouncerService:
    MIN_SAFE_ZONE_DURATION = settings.ANNOUNCER_MIN_SAFE_ZONE_DURATION
    MIN_COOLDOWN_BETWEEN_ANNOUNCEMENTS = settings.ANNOUNCER_COOLDOWN_SECONDS
    TRIGGER_PROBABILITY = settings.ANNOUNCER_TRIGGER_PROBABILITY
    TRIGGER_EARLY_MS = settings.ANNOUNCER_TRIGGER_EARLY_MS
    PENDING_THRESHOLD_MS = settings.ANNOUNCER_PENDING_THRESHOLD_MS
    MAX_GPT_GENERATION_SAMPLES = 10
    CROSSFADE_DEDUCTION_RATIO = 0.5
    MAX_TRANSITION_CACHE_PER_SESSION = 64
    IDLE_EVICTION_INTERVAL_S = 600

    def __init__(
            self,
            playback_service,
            dj_prompt_service,
            tts_queue_manager,
            sio,
            orchestrator
    ):
        self.playback_service = playback_service
        self.dj_prompt_service = dj_prompt_service
        self.tts_queue_manager = tts_queue_manager
        self.sio = sio
        self.orchestrator = orchestrator

        self.session_tasks: Dict[str, List[asyncio.Task]] = {}
        self.last_announcement_time: Dict[str, float] = {}
        self.transition_cache: Dict[str, Dict[Tuple[str, str], Optional[Dict]]] = {}
        self.analyzed_pair: Dict[str, Tuple[str, str]] = {}
        self.scheduled_announcements: Dict[str, Dict] = {}
        self.last_countdown_log: Dict[str, float] = {}
        self.gpt_generation_times = deque(maxlen=self.MAX_GPT_GENERATION_SAMPLES)
        self.avg_gpt_generation_time = 3.0

        self.active_announcements: Dict[str, str] = {}
        self._last_activity: Dict[str, float] = {}
        self._eviction_task: Optional[asyncio.Task] = None

        log_service.announcer("🎙️ AnnouncerService initialized")

    async def start(self):
        if self._eviction_task is None or self._eviction_task.done():
            self._eviction_task = spawn(self._evict_idle_sessions_loop(), name="announcer-idle-eviction")
        log_service.announcer("🎙️ AnnouncerService started - will monitor playback for announcement opportunities")

    async def stop(self):
        log_service.announcer("🎙️ AnnouncerService stopping - cancelling all scheduled announcements")
        if self._eviction_task is not None and not self._eviction_task.done():
            self._eviction_task.cancel()
        for session_id in list(self.session_tasks.keys()):
            await self._cleanup_session(session_id)

    def _tracked_session_ids(self) -> set:
        return (
            set(self.session_tasks) | set(self.last_announcement_time) | set(self.transition_cache)
            | set(self.analyzed_pair) | set(self.scheduled_announcements) | set(self.last_countdown_log)
            | set(self.active_announcements) | set(self._last_activity)
        )

    async def _evict_idle_sessions_loop(self):
        while True:
            await asyncio.sleep(self.IDLE_EVICTION_INTERVAL_S)
            try:
                from service_registry import services
                websocket_service = services.websocket_service
                is_connected = websocket_service.has_session if websocket_service else (lambda _sid: False)
                await self.evict_idle_sessions(is_connected, settings.PLAYBACK_SESSION_IDLE_TIMEOUT_S)
            except Exception as e:
                log_service.error(f"[Announcer] Idle session eviction failed: {e}")

    async def evict_idle_sessions(self, is_connected, max_idle_s: float) -> int:
        now = time.time()
        evicted = 0
        for session_id in self._tracked_session_ids():
            if is_connected(session_id):
                self._last_activity[session_id] = now
                continue
            if now - self._last_activity.get(session_id, now) < max_idle_s:
                self._last_activity.setdefault(session_id, now)
                continue
            await self._cleanup_session(session_id)
            evicted += 1
        if evicted:
            log_service.announcer(f"🎙️ Evicted {evicted} idle announcer sessions ({len(self.session_tasks)} remaining)")
        return evicted

    def monitor_session(self, session_id: str):
        self._last_activity[session_id] = time.time()
        if session_id in self.session_tasks:
            return
        log_service.announcer(f"🎙️ Announcer monitoring session {session_id}")
        self.session_tasks[session_id] = []

    async def forget_session(self, session_id: str):
        await self._cleanup_session(session_id)

    async def _cleanup_session(self, session_id: str):
        if session_id in self.session_tasks:
            for task in self.session_tasks[session_id]:
                if not task.done():
                    task.cancel()
            self.session_tasks[session_id].clear()
            del self.session_tasks[session_id]

        if session_id in self.analyzed_pair:
            del self.analyzed_pair[session_id]
        if session_id in self.transition_cache:
            del self.transition_cache[session_id]
        if session_id in self.scheduled_announcements:
            del self.scheduled_announcements[session_id]
        if session_id in self.last_countdown_log:
            del self.last_countdown_log[session_id]
        if session_id in self.last_announcement_time:
            del self.last_announcement_time[session_id]
        if session_id in self.active_announcements:
            del self.active_announcements[session_id]
        self._last_activity.pop(session_id, None)
        content_bank.forget_session(session_id)

        log_service.announcer(f"🎙️ [{session_id}] Session cleaned up")

    async def on_playback_state_update(self, session_id: str, state: dict):
        usage_tracking.bind_session(session_id)
        self._last_activity[session_id] = time.time()
        try:
            current_track = state.get('current_track')
            queue = state.get('queue', [])
            current_index = state.get('current_index', 0)
            is_playing = state.get('is_playing', False)
            progress_ms = state.get('progress_ms', 0)
            last_skip_reason = state.get('last_skip_reason')

            if not current_track or current_track.get('id') == 'N/A':
                return

            current_track_id = current_track['id']

            if session_id in self.scheduled_announcements:
                sched = self.scheduled_announcements[session_id]
                scheduled_track_id = sched['track_id']

                if scheduled_track_id == current_track_id:
                    time_until_trigger = sched['trigger_time_ms'] - progress_ms
                    if time_until_trigger > 0 and (time.time() - self.last_countdown_log.get(session_id, 0) > 10):
                        log_service.announcer(
                            f"🎙️ [{session_id[:8]}] ⏱️ Waiting for trigger... ({time_until_trigger / 1000:.1f}s)")
                        self.last_countdown_log[session_id] = time.time()
                elif scheduled_track_id != current_track_id:
                    if last_skip_reason == 'auto_crossfade':
                        log_service.announcer(
                            f"🎙️ [{session_id[:8]}] ✅ Auto-crossfade completed, announcement continues")

                        if session_id in self.active_announcements:
                            log_service.announcer(f"🎙️ [{session_id[:8]}] 🎤 Announcement in progress during crossfade")

                        del self.scheduled_announcements[session_id]
                    else:
                        log_service.announcer(
                            f"🎙️ [{session_id[:8]}] ⏭️ User skip detected ({last_skip_reason}), cancelling announcement")
                        if session_id in self.session_tasks:
                            for task in self.session_tasks[session_id]:
                                if not task.done():
                                    task.cancel()
                            self.session_tasks[session_id] = []
                        del self.scheduled_announcements[session_id]

            next_track_id = None
            if current_index < len(queue) - 1:
                next_track = queue[current_index + 1]
                next_track_id = next_track.get('id')

            current_pair = (current_track_id, next_track_id) if next_track_id else None
            previous_pair = self.analyzed_pair.get(session_id)

            if current_pair != previous_pair:
                if session_id in self.active_announcements:
                    log_service.announcer(
                        f"🎙️ [{session_id[:8]}] Pair changed but announcement active, not cancelling tasks")
                else:
                    if session_id in self.session_tasks:
                        for task in self.session_tasks[session_id]:
                            if not task.done():
                                task.cancel()
                        self.session_tasks[session_id].clear()

                    if session_id in self.scheduled_announcements:
                        del self.scheduled_announcements[session_id]

                self.session_tasks[session_id] = self.session_tasks.get(session_id, [])

                if current_pair and next_track_id:
                    self.analyzed_pair[session_id] = current_pair
                    self._prefetch_next_artist(session_id, next_track_id)

                    transition_window = await self._analyze_transition(
                        current_track_id,
                        next_track_id,
                        session_id
                    )

                    if is_playing and transition_window and 'start_ms' in transition_window:
                        await self._schedule_announcement_for_transition(
                            session_id,
                            current_track_id,
                            transition_window,
                            state
                        )
                    elif is_playing:
                        self._schedule_space(session_id, current_track_id,
                                             self._crossfade_window(transition_window, current_track), state)
                    if is_playing:
                        await self._schedule_song_spaces(session_id, current_track_id, state)

        except Exception as e:
            log_service.error(f"Announcer: Error in playback change handler: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

    def _existing_session_state(self, session_id: str):
        return self.playback_service.sessions.get(session_id)

    @staticmethod
    def _radio_break_holds(session_id: str) -> bool:
        from service_registry import services
        radio = services.radio_mode_service
        return bool(radio is not None and radio.blocks_announcer(session_id))

    def _prefetch_next_artist(self, session_id: str, next_track_id: str):
        if not settings.DJ_TRIVIA_PREFETCH_ENABLED:
            return
        try:
            playback_state = self._existing_session_state(session_id)
            catalog = getattr(playback_state, 'catalog', None)
            track = catalog.get_track(next_track_id) if catalog else None
            artist = ((track or {}).get('generation_params') or {}).get('artist_name')
            content_bank.prefetch_artist(getattr(self.dj_prompt_service, 'web_service', None), artist)
        except Exception as e:
            log_service.warning(f"Announcer: trivia prefetch skipped: {type(e).__name__}: {e}")

    async def _analyze_transition(
            self,
            current_track_id: str,
            next_track_id: str,
            session_id: str
    ) -> Optional[Dict]:
        cache_key = (current_track_id, next_track_id)

        if session_id in self.transition_cache:
            cached = self.transition_cache[session_id].get(cache_key)
            if cached is not None:
                if cached.get('crossfade_timing'):
                    await self._publish_crossfade(session_id, cache_key, cached['crossfade_timing'])
                return cached

        current_name = "Unknown"
        next_name = "Unknown"
        try:
            playback_state = self._existing_session_state(session_id)
            if playback_state is not None and playback_state.catalog:
                current_track = playback_state.catalog.get_track(current_track_id)
                next_track = playback_state.catalog.get_track(next_track_id)
                if current_track:
                    current_name = current_track.get('generation_params', {}).get('title', 'Unknown')[:25]
                if next_track:
                    next_name = next_track.get('generation_params', {}).get('title', 'Unknown')[:25]
        except Exception:
            pass

        try:
            current_features = await self.orchestrator.features.load_features(current_track_id)
            next_features = await self.orchestrator.features.load_features(next_track_id)

            if not current_features or not next_features:
                self._cache_transition(session_id, cache_key, None)
                return None

            current_lyrics = await self.orchestrator.lyrics.load_timestamps(current_track_id)
            next_lyrics = await self.orchestrator.lyrics.load_timestamps(next_track_id)
            crossfade_timing = crossfade_plan.plan(current_features, next_features, current_lyrics, next_lyrics)

            if crossfade_timing:
                await self._publish_crossfade(session_id, cache_key, crossfade_timing)

            current_duration = current_features.get('duration', 0) * 1000

            outro_segments = await self._get_quiet_segments(
                current_features, current_lyrics, start_pct=0.85, end_pct=1.0
            )
            intro_segments = await self._get_quiet_segments(
                next_features, next_lyrics, start_pct=0.0, end_pct=0.15, offset_ms=current_duration
            )

            all_segments = outro_segments + intro_segments
            best_window = self._find_best_silence_window(all_segments, current_duration)

            result_window = None
            if best_window:
                crossfade_duration_ms = crossfade_timing['duration_ms'] if crossfade_timing else 2000
                crossfade_deduction = int(crossfade_duration_ms * self.CROSSFADE_DEDUCTION_RATIO)
                adjusted_duration = max(best_window['duration_ms'] - crossfade_deduction, self.MIN_SAFE_ZONE_DURATION)

                result_window = {
                    'dj_speaking_window': best_window,
                    'crossfade_timing': crossfade_timing,
                    'start_ms': best_window['start_ms'],
                    'end_ms': best_window['end_ms'],
                    'duration_ms': adjusted_duration,
                    'raw_duration_ms': best_window['duration_ms'],
                    'crossfade_deduction_ms': crossfade_deduction
                }

                try:
                    playback_state = self._existing_session_state(session_id)
                    if playback_state is not None:
                        playback_state.set_announcer_timing(current_track_id, next_track_id, result_window)
                except Exception as e:
                    log_service.warning(f"Failed to push announcer timing: {e}")

            self._log_transition_report(session_id, current_name, next_name, crossfade_timing, result_window)

            to_cache = result_window if result_window else {'crossfade_timing': crossfade_timing,
                                                            'track_ms': current_duration}
            self._cache_transition(session_id, cache_key, to_cache)
            return to_cache

        except Exception as e:
            log_service.error(f"Announcer: Analysis failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            self._cache_transition(session_id, cache_key, None)
            return None

    async def _publish_crossfade(self, session_id: str, pair: Tuple[str, str], timing: Dict):
        playback_state = self._existing_session_state(session_id)
        if playback_state is None or playback_state.crossfade_timing_cache.get(pair) == timing:
            return
        playback_state.set_crossfade_timing(pair[0], pair[1], timing)
        queue, index = playback_state.queue, playback_state.current_index
        if index + 1 >= len(queue) or (queue[index].get('id'), queue[index + 1].get('id')) != pair:
            return
        from service_registry import services
        try:
            await services.websocket_service.broadcast_playback_state(session_id, playback_state.get_state())
        except Exception as e:
            log_service.warning(f"Announcer: could not send the crossfade plan: {type(e).__name__}: {e}")

    def _log_transition_report(self, session_id, current, next_t, xfade, window):
        short_id = session_id[:8]

        xfade_str = "None"
        if xfade:
            start_s = xfade['optimal_start_ms'] / 1000
            dur_s = xfade['duration_ms'] / 1000
            out_s = xfade.get('fade_out_ms', xfade['duration_ms']) / 1000
            in_s = xfade.get('fade_in_ms', xfade['duration_ms']) / 1000
            xfade_str = (f"Start: {start_s:.1f}s | Overlap: {dur_s:.1f}s (out {out_s:.1f}s, in {in_s:.1f}s) | "
                         f"{xfade.get('reason', 'unknown')}")

        win_str = "None"
        if window:
            w_start = window['start_ms'] / 1000
            w_end = window['end_ms'] / 1000
            w_dur = window['duration_ms'] / 1000
            raw_dur = window.get('raw_duration_ms', window['duration_ms']) / 1000
            deduction = window.get('crossfade_deduction_ms', 0) / 1000
            gpt_target = int(window['duration_ms'])
            win_str = f"{w_start:.1f}s → {w_end:.1f}s | Raw: {raw_dur:.1f}s - XFade: {deduction:.1f}s = {w_dur:.1f}s | GPT Target: {gpt_target}ms"

        log_msg = (
            f"\n🎙️ [{short_id}] Transition Analysis Report\n"
            f"   ├─ 🎵 Tracks: \"{current}\" ➡️ \"{next_t}\"\n"
            f"   ├─ 🎚️ Crossfade: {xfade_str}\n"
            f"   └─ 🗣️ Announcer: {win_str}"
        )
        log_service.announcer(log_msg)

    @staticmethod
    async def _get_quiet_segments(
            audio_features: Dict,
            lyric_data: Optional[Dict],
            start_pct: float = 0.0,
            end_pct: float = 1.0,
            offset_ms: float = 0.0,
            loudness_pct: float = 30
    ) -> List[Dict]:
        duration = audio_features.get('duration', 0)
        loudness_segments = audio_features.get('loudness_segments', [])
        start_time = duration * start_pct
        end_time = duration * end_pct

        relevant_segments = [s for s in loudness_segments if start_time <= s['start'] < end_time]
        if not relevant_segments:
            return []

        all_loudness = [s['loudness'] for s in relevant_segments]
        if not all_loudness:
            return []

        loudness_threshold = np.percentile(all_loudness, loudness_pct)
        min_segment_duration = 1.0
        quiet_segments = []
        current_quiet_start = None

        for seg in relevant_segments:
            if seg['loudness'] < loudness_threshold:
                if current_quiet_start is None:
                    current_quiet_start = seg['start']
            else:
                if current_quiet_start is not None:
                    duration_s = seg['start'] - current_quiet_start
                    if duration_s >= min_segment_duration:
                        quiet_segments.append({
                            'start_s': current_quiet_start, 'end_s': seg['start'], 'duration_s': duration_s
                        })
                    current_quiet_start = None

        if current_quiet_start is not None:
            duration_s = end_time - current_quiet_start
            if duration_s >= min_segment_duration:
                quiet_segments.append({
                    'start_s': current_quiet_start, 'end_s': end_time, 'duration_s': duration_s
                })

        if lyric_data and lyric_data.get('lyrics'):
            lyric_free_segments = []
            for seg in quiet_segments:
                has_lyrics = any(
                    seg['start_s'] <= line.get('start', 0) < seg['end_s']
                    for line in lyric_data['lyrics']
                )
                if not has_lyrics:
                    lyric_free_segments.append(seg)
            quiet_segments = lyric_free_segments

        return [
            {
                'start_ms': (s['start_s'] * 1000) + offset_ms,
                'end_ms': (s['end_s'] * 1000) + offset_ms,
                'duration_ms': s['duration_s'] * 1000
            }
            for s in quiet_segments
        ]

    @staticmethod
    def _find_best_silence_window(segments: List[Dict], track_boundary_ms: float) -> Optional[Dict]:
        if not segments:
            return None
        sorted_segments = sorted(segments, key=lambda s: s['start_ms'])
        merge_threshold_ms = 1000
        merged = []
        current = sorted_segments[0].copy()

        for seg in sorted_segments[1:]:
            if seg['start_ms'] - current['end_ms'] <= merge_threshold_ms:
                current['end_ms'] = seg['end_ms']
                current['duration_ms'] = current['end_ms'] - current['start_ms']
            else:
                merged.append(current)
                current = seg.copy()
        merged.append(current)

        min_duration_ms = 3000
        suitable = [m for m in merged if m['duration_ms'] >= min_duration_ms]

        if not suitable:
            return None

        boundary_windows = [w for w in suitable if w['start_ms'] < track_boundary_ms < w['end_ms']]
        if boundary_windows:
            return max(boundary_windows, key=lambda w: w['duration_ms'])
        else:
            return max(suitable, key=lambda w: w['duration_ms'])

    def _cache_transition(self, session_id: str, cache_key: Tuple[str, str], window: Optional[Dict]):
        session_cache = self.transition_cache.setdefault(session_id, {})
        session_cache.pop(cache_key, None)
        session_cache[cache_key] = window
        while len(session_cache) > self.MAX_TRANSITION_CACHE_PER_SESSION:
            del session_cache[next(iter(session_cache))]

    @staticmethod
    def _sting_service():
        from service_registry import services
        return services.sting_service

    @staticmethod
    def _state_user_id(session_id: str, state: dict) -> Optional[int]:
        user_id = state.get('user_id')
        if user_id is None:
            try:
                user_id = int(session_id)
            except ValueError:
                user_id = None
        return user_id

    @staticmethod
    def _crossfade_window(analysis: Optional[Dict], current_track: Dict) -> Dict:
        analysis = analysis or {}
        timing = analysis.get('crossfade_timing') or {}
        track_ms = analysis.get('track_ms') or ((current_track.get('track_info') or {}).get('duration') or 0)
        duration_ms = max(timing.get('duration_ms') or FILL_DEFAULT_MS, settings.STINGS_MIN_WINDOW_S * 1000)
        start_ms = timing.get('optimal_start_ms') or max(0, track_ms - duration_ms)
        return {'start_ms': start_ms, 'end_ms': start_ms + duration_ms, 'duration_ms': duration_ms}

    def _still_at_boundary(self, session_id: str, track_id: str) -> bool:
        session_state = self._existing_session_state(session_id)
        if session_state is None or not session_state.is_playing:
            return False
        if (session_state.current_track or {}).get('id') == track_id:
            return True
        return session_state.last_skip_reason == 'auto_crossfade' and \
            session_state.get_simulated_progress() < FILL_LATE_MS

    async def _fill_now(self, session_id: str, current_track_id: str, transition_window: Dict, state: dict,
                        why: str):
        stings = self._sting_service()
        if stings is None or not self._still_at_boundary(session_id, current_track_id):
            return
        user_id = self._state_user_id(session_id, state)
        window_s = transition_window['duration_ms'] / 1000.0
        kind = await stings.offer_space(session_id, user_id, window_s, midtrack=False,
                                        label=f"song change (the hosts' line {why})")
        render = await stings.build(session_id, user_id, kind, window_s) if kind else None
        if render is not None and self._still_at_boundary(session_id, current_track_id):
            await stings.play(session_id, user_id, render)

    def _break_lined_up(self, session_id: str) -> bool:
        from service_registry import services
        radio = services.radio_mode_service
        return bool(radio is not None and radio.break_lined_up(session_id))

    def _schedule_space(self, session_id: str, track_id: str, window: Dict, state: dict):
        task = asyncio.create_task(self._run_space(session_id, track_id, window, state, midtrack=False,
                                                   label="song change"))
        self.session_tasks.setdefault(session_id, []).append(task)

    @staticmethod
    def _merge_spaces(segments: List[Dict]) -> List[Dict]:
        merged: List[Dict] = []
        for seg in sorted(segments, key=lambda s: s['start_ms']):
            if merged and seg['start_ms'] <= merged[-1]['end_ms']:
                merged[-1]['end_ms'] = max(merged[-1]['end_ms'], seg['end_ms'])
                merged[-1]['duration_ms'] = merged[-1]['end_ms'] - merged[-1]['start_ms']
            else:
                merged.append(dict(seg))
        return merged

    async def _schedule_song_spaces(self, session_id: str, track_id: str, state: dict):
        if not (settings.STINGS_ENABLED and settings.STINGS_MIDTRACK_ENABLED):
            return
        try:
            features = await self.orchestrator.features.load_features(track_id)
            if not features:
                return
            lyrics = await self.orchestrator.lyrics.load_timestamps(track_id)
            vocal_spans = self._vocal_spans(lyrics, features.get('duration', 0))
            if vocal_spans is None and not self._is_instrumental(session_id, track_id):
                return
            quiet = await self._get_quiet_segments(features, None, start_pct=SPACE_START_PCT, end_pct=SPACE_END_PCT,
                                                   loudness_pct=SPACE_LOUDNESS_PCT)
            earliest = state.get('progress_ms', 0) + settings.STINGS_BUILD_LEAD_MS + 2000
            spaces = [s for s in self._minus_vocals(self._merge_spaces(quiet), vocal_spans or [])
                      if s['duration_ms'] >= settings.STINGS_MIDTRACK_MIN_WINDOW_S * 1000 and s['start_ms'] > earliest]
            if spaces:
                task = asyncio.create_task(self._walk_spaces(session_id, track_id, spaces, state))
                self.session_tasks.setdefault(session_id, []).append(task)
        except Exception as e:
            log_service.warning(f"Announcer: mapping the song's quiet spaces failed: {type(e).__name__}: {e}")

    @staticmethod
    def _vocal_spans(lyric_data: Optional[Dict], duration_s: float) -> Optional[List[Tuple[float, float]]]:
        lines = [l for l in (lyric_data or {}).get('lyrics') or [] if isinstance(l.get('start'), (int, float))]
        if not lines or not duration_s:
            return None
        if (lyric_data.get('alignment_score') or 0) < LYRIC_ALIGNMENT_MIN \
                or max(l.get('end') or l['start'] for l in lines) < duration_s * LYRIC_SPAN_MIN:
            return None
        return [((l['start'] - VOCAL_PAD_S) * 1000, ((l.get('end') or l['start']) + VOCAL_PAD_S) * 1000) for l in lines]

    @staticmethod
    def _minus_vocals(spaces: List[Dict], vocal_spans: List[Tuple[float, float]]) -> List[Dict]:
        pieces = []
        for space in spaces:
            parts = [(space['start_ms'], space['end_ms'])]
            for v_start, v_end in vocal_spans:
                parts = [p for a, b in parts
                         for p in ((a, min(b, v_start)), (max(a, v_end), b)) if p[1] > p[0]]
            pieces += [{'start_ms': a, 'end_ms': b, 'duration_ms': b - a} for a, b in parts]
        return pieces

    def _is_instrumental(self, session_id: str, track_id: str) -> bool:
        from services.catalog_vocals import vocals_of
        playback_state = self._existing_session_state(session_id)
        track = playback_state.catalog.get_track(track_id) if playback_state is not None and playback_state.catalog \
            else None
        return bool(track) and vocals_of(track) == "instrumental"

    async def _walk_spaces(self, session_id: str, track_id: str, spaces: List[Dict], state: dict):
        for space in spaces:
            await self._run_space(session_id, track_id, space, state, midtrack=True,
                                  label=f"quiet stretch at {log_service.clock(space['start_ms'])}")

    async def _run_space(self, session_id: str, track_id: str, window: Dict, state: dict, midtrack: bool,
                         label: str) -> bool:
        usage_tracking.bind_session(session_id)
        stings = self._sting_service()
        if stings is None:
            return False
        user_id = self._state_user_id(session_id, state)
        lead_ms = settings.STINGS_BUILD_LEAD_MS
        window_s = window['duration_ms'] / 1000.0
        if midtrack:
            trigger_ms = window['start_ms'] + 500
            space_s = min(midtrack_max_len(window_s), window_s - 1.0)
        else:
            trigger_ms = window['start_ms'] - settings.STINGS_TRIGGER_EARLY_MS
            space_s = window_s
        try:
            if not await self._wait_for_trigger(session_id, track_id, trigger_ms - lead_ms, not midtrack):
                return False
            if not midtrack and self._break_lined_up(session_id):
                log_service.playback(f"{log_service.who(session_id)}: {label} -> the Radio Mode break")
                return False
            if midtrack and await self._offer_review(session_id, track_id, window, trigger_ms, user_id):
                return True
            kind = await stings.offer_space(session_id, user_id, space_s, midtrack, label, lead_s=lead_ms / 1000.0)
            if not kind:
                return False
            if not midtrack:
                self.active_announcements[session_id] = track_id
            render = await stings.build(session_id, user_id, kind, space_s, midtrack=midtrack,
                                        lead_s=lead_ms / 1000.0)
            if render is None:
                log_service.playback(f"{log_service.who(session_id)}: {label} -> {kind} could not be built")
                return False
            if not await self._wait_for_trigger(session_id, track_id, trigger_ms, not midtrack):
                return False
            return await stings.play(session_id, user_id, render, midtrack=midtrack)
        except Exception as e:
            log_service.error(f"Announcer: {label} failed: {type(e).__name__}: {e}")
            return False
        finally:
            if not midtrack:
                self._cleanup_scheduled(session_id)

    async def _offer_review(self, session_id: str, track_id: str, window: Dict, trigger_ms: float,
                            user_id: Optional[int]) -> bool:
        stings = self._sting_service()
        need_ms = 2000 + settings.REVIEW_STINGS_DUCK_S * 1000
        if window['duration_ms'] < need_ms or not stings.review_sting_possible(session_id, track_id) \
                or stings.station_quiet_s(session_id, user_id) < settings.STATION_QUIET_TARGET_S:
            return False
        max_len_s = min(settings.REVIEW_STINGS_MAX_LEN_S, window['duration_ms'] / 1000.0 - 0.5)
        render = await stings.build_review(session_id, user_id, track_id, max_len_s)
        if render is None or not await self._wait_for_trigger(session_id, track_id, trigger_ms, False):
            return False
        played = await stings.play_review(session_id, user_id, render)
        if played:
            log_service.playback(f"{log_service.who(session_id)}: quiet stretch at "
                                 f"{log_service.clock(window['start_ms'])} -> a listener review")
        return played

    async def _wait_for_trigger(self, session_id: str, current_track_id: str, trigger_time_ms: float,
                                follow_crossfade: bool = True) -> bool:
        last_log_time = 0
        while True:
            session_state = self._existing_session_state(session_id)
            if not session_state:
                log_service.announcer(f"🎙️ [{session_id[:8]}] ❌ Session state is None, cancelling")
                return False
            current_progress = session_state.get_simulated_progress()
            current_track = (session_state.current_track or {}).get('id')
            is_playing = session_state.is_playing
            last_skip_reason = session_state.last_skip_reason

            if current_track != current_track_id:
                if last_skip_reason == 'auto_crossfade' and follow_crossfade:
                    log_service.announcer(
                        f"🎙️ [{session_id[:8]}] 🔄 Auto-crossfade completed, announcement continues")
                    return True
                log_service.announcer(
                    f"🎙️ [{session_id[:8]}] ❌ Track changed (user action) during wait, cancelling")
                return False

            if not is_playing:
                await asyncio.sleep(1.0)
                continue

            time_until_trigger = trigger_time_ms - current_progress

            current_time = time.time()
            if current_time - last_log_time >= 10.0:
                log_service.announcer(
                    f"🎙️ [{session_id[:8]}] ⏳ Countdown: {time_until_trigger / 1000:.1f}s "
                    f"(progress: {current_progress / 1000:.1f}s / trigger: {trigger_time_ms / 1000:.1f}s)")
                last_log_time = current_time

            if time_until_trigger <= 0:
                return True
            elif time_until_trigger > 10000:
                await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(min(1.0, time_until_trigger / 1000.0))

    async def _schedule_announcement_for_transition(
            self, session_id: str, current_track_id: str, transition_window: Dict, state: dict
    ):
        last_announcement = self.last_announcement_time.get(session_id, 0)
        time_since_last = time.time() - last_announcement

        if time_since_last < self.MIN_COOLDOWN_BETWEEN_ANNOUNCEMENTS:
            log_service.announcer(
                f"🎙️ [{session_id[:8]}] 🔴 Skipped: Cooldown ({int(time_since_last)}s < {self.MIN_COOLDOWN_BETWEEN_ANNOUNCEMENTS}s)")
            return

        if random.random() > self.TRIGGER_PROBABILITY:
            log_service.announcer(f"🎙️ [{session_id[:8]}] 🔴 Skipped: Probability Roll Failed")
            return

        current_progress_ms = state.get('progress_ms', 0)
        trigger_time_ms = transition_window['start_ms'] - self.TRIGGER_EARLY_MS
        wait_time_ms = trigger_time_ms - current_progress_ms

        if wait_time_ms < 0:
            log_service.playback(f"{log_service.who(session_id)}: song change - hosts' line skipped, the gap was "
                                 f"{abs(wait_time_ms) / 1000:.1f}s in the past when it was planned")
            return

        if wait_time_ms > self.PENDING_THRESHOLD_MS:
            log_service.announcer(f"🎙️ [{session_id[:8]}] 📅 Pending: {wait_time_ms / 1000:.0f}s away - starting countdown")
        else:
            log_service.announcer(f"🎙️ [{session_id[:8]}] 🟢 Scheduled: Trigger in {wait_time_ms / 1000:.1f}s")

        self.scheduled_announcements[session_id] = {
            'trigger_time_ms': trigger_time_ms,
            'track_id': current_track_id,
            'window': transition_window
        }

        task = asyncio.create_task(
            self._execute_transition_announcement(session_id, current_track_id, transition_window, state)
        )
        self.session_tasks[session_id].append(task)

    async def _execute_transition_announcement(
            self, session_id: str, current_track_id: str, transition_window: Dict, state: dict
    ):
        usage_tracking.bind_session(session_id)
        try:
            trigger_time_ms = transition_window['start_ms'] - self.TRIGGER_EARLY_MS
            if not await self._wait_for_trigger(session_id, current_track_id, trigger_time_ms):
                return

            if self._radio_break_holds(session_id):
                log_service.announcer(f"🎙️ [{session_id[:8]}] 📻 Skipped: Radio Mode talk break at this boundary")
                self._cleanup_scheduled(session_id)
                return

            self.active_announcements[session_id] = current_track_id

            gpt_start = time.time()
            transition_duration_ms = int(transition_window['duration_ms'])
            user_id = state.get('user_id')
            if user_id is None:
                try:
                    user_id = int(session_id)
                except ValueError:
                    user_id = None

            session_dict = {"session_id": session_id, "user_id": user_id}

            try:
                announcement_text = await asyncio.wait_for(
                    self.dj_prompt_service.gpt_dj_announcements(transition_duration_ms, session_dict),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                log_service.error(f"🎙️ [{session_id}] GPT Timeout")
                self._cleanup_scheduled(session_id)
                await self._fill_now(session_id, current_track_id, transition_window, state, "timed out")
                return

            gpt_elapsed = time.time() - gpt_start
            self._update_gpt_timing(gpt_elapsed)

            if not announcement_text:
                log_service.warning(f"🎙️ [{session_id}] GPT returned empty text")
                self._cleanup_scheduled(session_id)
                await self._fill_now(session_id, current_track_id, transition_window, state, "came back empty")
                return

            await self.tts_queue_manager.add_tts_request(
                text=announcement_text,
                user_id=user_id or 0,
                tts_type="announcer",
                is_broadcast=True,
                is_temp_user=(user_id is None),
                session_id=session_id
            )

            self.last_announcement_time[session_id] = time.time()
            content_bank.record_airing(session_id, announcement_text)
            target_ms = int(transition_window['duration_ms'])
            actual_chars = len(announcement_text)
            log_service.announcer(
                f"🎙️ [{session_id[:8]}] ✓ Announcement fired | "
                f"Target: {target_ms}ms | GPT: {gpt_elapsed:.2f}s | Chars: {actual_chars}"
            )

            await self.sio.emit('conversation_update', {
                'bot_response': announcement_text,
                'message_type': 'announcer'
            }, room=session_id)

            self._cleanup_scheduled(session_id)

        except asyncio.CancelledError:
            log_service.announcer(f"🎙️ [{session_id}] Task cancelled")
            self._cleanup_scheduled(session_id)
        except Exception as e:
            log_service.error(f"Announcer Error: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            self._cleanup_scheduled(session_id)
            await self._fill_now(session_id, current_track_id, transition_window, state, "failed")

    def _cleanup_scheduled(self, session_id):
        if session_id in self.scheduled_announcements:
            del self.scheduled_announcements[session_id]
        if session_id in self.last_countdown_log:
            del self.last_countdown_log[session_id]
        if session_id in self.active_announcements:
            del self.active_announcements[session_id]

    def _update_gpt_timing(self, elapsed_time: float):
        self.gpt_generation_times.append(elapsed_time)
        if len(self.gpt_generation_times) > 0:
            self.avg_gpt_generation_time = sum(self.gpt_generation_times) / len(self.gpt_generation_times)
import asyncio
import time
import random
import numpy as np
from typing import Dict, Optional, List, Tuple
from collections import deque
from services import log_service
from config.settings import settings

class AnnouncerService:
    MIN_SAFE_ZONE_DURATION = settings.ANNOUNCER_MIN_SAFE_ZONE_DURATION
    MIN_COOLDOWN_BETWEEN_ANNOUNCEMENTS = settings.ANNOUNCER_COOLDOWN_SECONDS
    TRIGGER_PROBABILITY = settings.ANNOUNCER_TRIGGER_PROBABILITY
    TRIGGER_EARLY_MS = settings.ANNOUNCER_TRIGGER_EARLY_MS
    PENDING_THRESHOLD_MS = settings.ANNOUNCER_PENDING_THRESHOLD_MS
    MAX_GPT_GENERATION_SAMPLES = 10
    CROSSFADE_DEDUCTION_RATIO = 0.5

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

        log_service.announcer("🎙️ AnnouncerService initialized")

    @staticmethod
    async def start():
        log_service.announcer("🎙️ AnnouncerService started - will monitor playback for announcement opportunities")

    async def stop(self):
        log_service.announcer("🎙️ AnnouncerService stopping - cancelling all scheduled announcements")
        for session_id in list(self.session_tasks.keys()):
            await self._cleanup_session(session_id)

    def monitor_session(self, session_id: str):
        if session_id in self.session_tasks:
            return
        log_service.announcer(f"🎙️ Announcer monitoring session {session_id}")
        self.session_tasks[session_id] = []

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

        log_service.announcer(f"🎙️ [{session_id}] Session cleaned up")

    async def on_playback_state_update(self, session_id: str, state: dict):
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

                    if sched.get('pending') and 0 < time_until_trigger <= self.PENDING_THRESHOLD_MS:
                        next_track_id = None
                        if current_index < len(queue) - 1:
                            next_track = queue[current_index + 1]
                            next_track_id = next_track.get('id')

                        if next_track_id:
                            del self.scheduled_announcements[session_id]
                            await self._schedule_announcement_for_transition(
                                session_id,
                                current_track_id,
                                sched['window'],
                                state
                            )
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

                    transition_window = await self._analyze_transition(
                        current_track_id,
                        next_track_id,
                        session_id
                    )

                    if transition_window and is_playing and 'start_ms' in transition_window:
                        await self._schedule_announcement_for_transition(
                            session_id,
                            current_track_id,
                            transition_window,
                            state
                        )

        except Exception as e:
            log_service.error(f"Announcer: Error in playback change handler: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

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
                if 'crossfade_timing' in cached:
                    try:
                        playback_state = self.playback_service.get_session_state(session_id)
                        playback_state.set_crossfade_timing(current_track_id, next_track_id, cached['crossfade_timing'])
                    except Exception:
                        pass
                return cached

        current_name = "Unknown"
        next_name = "Unknown"
        try:
            playback_state = self.playback_service.get_session_state(session_id)
            if playback_state.catalog:
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

            crossfade_timing = self._calculate_smart_crossfade(current_features, next_features)

            if crossfade_timing:
                try:
                    playback_state = self.playback_service.get_session_state(session_id)
                    playback_state.set_crossfade_timing(current_track_id, next_track_id, crossfade_timing)
                except Exception as e:
                    log_service.warning(f"Failed to push crossfade timing: {e}")

            current_duration = current_features.get('duration', 0) * 1000
            current_lyrics = await self.orchestrator.lyrics.load_timestamps(current_track_id)
            next_lyrics = await self.orchestrator.lyrics.load_timestamps(next_track_id)

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
                    playback_state = self.playback_service.get_session_state(session_id)
                    playback_state.set_announcer_timing(current_track_id, next_track_id, result_window)
                except Exception as e:
                    log_service.warning(f"Failed to push announcer timing: {e}")

            self._log_transition_report(session_id, current_name, next_name, crossfade_timing, result_window)

            to_cache = result_window if result_window else {'crossfade_timing': crossfade_timing}
            self._cache_transition(session_id, cache_key, to_cache)
            return result_window

        except Exception as e:
            log_service.error(f"Announcer: Analysis failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            self._cache_transition(session_id, cache_key, None)
            return None

    def _log_transition_report(self, session_id, current, next_t, xfade, window):
        short_id = session_id[:8]

        xfade_str = "None"
        if xfade:
            start_s = xfade['optimal_start_ms'] / 1000
            dur_s = xfade['duration_ms'] / 1000
            overlap = xfade.get('reason', 'unknown')
            xfade_str = f"Start: {start_s:.1f}s | Dur: {dur_s:.1f}s | Mode: {overlap}"

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
    def _calculate_smart_crossfade(current_features: Dict, next_features: Dict) -> Optional[Dict]:
        SILENCE_THRESHOLD_DB = -45.0
        STANDARD_OVERLAP_MS = 2000

        def find_sound_boundary(segments, from_end=False):
            if not segments:
                return None
            iterator = reversed(segments) if from_end else segments
            for seg in iterator:
                if seg.get('loudness', -60) > SILENCE_THRESHOLD_DB:
                    if from_end:
                        return (seg['start'] + seg['duration']) * 1000
                    else:
                        return seg['start'] * 1000
            return None

        curr_segments = current_features.get('loudness_segments', [])
        curr_duration_ms = current_features.get('duration', 0) * 1000

        last_sound_ms = find_sound_boundary(curr_segments, from_end=True)
        if last_sound_ms is None:
            last_sound_ms = curr_duration_ms

        next_segments = next_features.get('loudness_segments', [])
        first_sound_ms = find_sound_boundary(next_segments, from_end=False)
        if first_sound_ms is None:
            first_sound_ms = 0

        optimal_start_ms = last_sound_ms - STANDARD_OVERLAP_MS - first_sound_ms
        optimal_start_ms = max(0, min(optimal_start_ms, curr_duration_ms - 1000))

        return {
            'optimal_start_ms': optimal_start_ms,
            'duration_ms': STANDARD_OVERLAP_MS,
            'confidence': 'high',
            'reason': 'Smart (Energy)'
        }

    @staticmethod
    async def _get_quiet_segments(
            audio_features: Dict,
            lyric_data: Optional[Dict],
            start_pct: float = 0.0,
            end_pct: float = 1.0,
            offset_ms: float = 0.0
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

        loudness_threshold = np.percentile(all_loudness, 30)
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
        if session_id not in self.transition_cache:
            self.transition_cache[session_id] = {}
        self.transition_cache[session_id][cache_key] = window

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
            log_service.announcer(f"🎙️ [{session_id[:8]}] 🔴 Skipped: Window Missed (Passed by {abs(wait_time_ms)}ms)")
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
        try:
            trigger_time_ms = transition_window['start_ms'] - self.TRIGGER_EARLY_MS
            last_log_time = 0

            while True:
                session_state = self.playback_service.get_session_state(session_id)
                if not session_state:
                    log_service.announcer(f"🎙️ [{session_id[:8]}] ❌ Session state is None, cancelling")
                    return
                current_state = session_state.get_state()
                current_progress = current_state.get('progress_ms', 0)
                current_track = current_state.get('current_track', {}).get('id')
                is_playing = current_state.get('is_playing', False)
                last_skip_reason = current_state.get('last_skip_reason')

                if current_track != current_track_id:
                    if last_skip_reason == 'auto_crossfade':
                        log_service.announcer(
                            f"🎙️ [{session_id[:8]}] 🔄 Auto-crossfade completed, announcement continues")
                        break
                    else:
                        log_service.announcer(
                            f"🎙️ [{session_id[:8]}] ❌ Track changed (user action) during wait, cancelling")
                        return

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
                    break
                elif time_until_trigger > 10000:
                    await asyncio.sleep(5.0)
                else:
                    await asyncio.sleep(min(1.0, time_until_trigger / 1000.0))

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
                return

            gpt_elapsed = time.time() - gpt_start
            self._update_gpt_timing(gpt_elapsed)

            if not announcement_text:
                log_service.warning(f"🎙️ [{session_id}] GPT returned empty text")
                self._cleanup_scheduled(session_id)
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
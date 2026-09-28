import asyncio
import io
import random
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Dict, Optional, Tuple

import soundfile as sf
from pydub import AudioSegment

from config.settings import settings
from services import log_service
from services import usage_tracking
from services_radio import radio_schedule, station_ids, sting_schedule, sting_types, talking_clock
from services_radio.station_voice import StationClipStore, StationRenderer
from services_radio.sting_library import StingLibrary, sting_library
from services_radio.tts_queue_manager import PrerenderedClip

TTS_TYPE = "sting"
LOCATION_TTL_S = 600.0
MAX_SESSIONS = 5000
PROCESSED_CACHE_MAX = 128
LEAD_IN_MAX_S = 3.2




def midtrack_max_len(quiet_window_s: float) -> float:
    return max(settings.STINGS_MIDTRACK_MAX_LEN_S, min(settings.STINGS_MIDTRACK_VOICE_MAX_LEN_S, quiet_window_s - 1.0))

class _SessionStings:
    def __init__(self, now: float):
        self.state = sting_schedule.StingState(started_at=now, last_seen=now)
        self.tz_name: Optional[str] = None
        self.city: Optional[str] = None
        self.location_at = 0.0


class StingService:
    def __init__(self, tts_queue_manager=None, tts_generation_service=None, audio_processing_service=None,
                 playback_service=None, conversation_service=None, async_session_maker=None,
                 store: Optional[StationClipStore] = None, library: Optional[StingLibrary] = None,
                 clock: Callable[[], float] = time.time, rng: Optional[random.Random] = None):
        self.tts_queue_manager = tts_queue_manager
        self.audio_processing_service = audio_processing_service
        self.playback_service = playback_service
        self.conversation_service = conversation_service
        self.async_session_maker = async_session_maker
        self.store = store or StationClipStore()
        self.renderer = StationRenderer(self.store, tts_generation_service, clock=clock,
                                        verifier=self._verify_numbers if settings.STINGS_VERIFY_NUMBERS else None)
        self.library = library or sting_library
        self.clock = clock
        self.rng = rng or random.Random()
        self.sessions: "OrderedDict[str, _SessionStings]" = OrderedDict()
        self._processed: "OrderedDict[str, AudioSegment]" = OrderedDict()
        self._city_requests: deque = deque()
        self._lock = threading.Lock()
        self.plays: Dict[str, int] = {}

    async def start(self):
        if not settings.STINGS_ENABLED:
            log_service.announcer("[STINGS] Stings & time checks disabled (STINGS_ENABLED=false)")
            return
        await asyncio.to_thread(self.store.reload)
        await asyncio.to_thread(self.library.scan_sfx)
        if self.audio_processing_service is not None:
            try:
                await asyncio.to_thread(self.audio_processing_service.process_station,
                                        AudioSegment.silent(duration=300, frame_rate=settings.TTS_SAMPLE_RATE))
            except Exception as e:
                log_service.warning(f"[STINGS] Station voice chain warm-up failed: {type(e).__name__}: {e}")
        if settings.STINGS_PRERENDER_ENABLED:
            queued = self.queue_library_renders()
            self.renderer.start(settings.STINGS_PRERENDER_DELAY_S)
            log_service.announcer(f"[STINGS] Station voice: {len(self.store.clips())} parts cached, "
                                  f"{queued} queued for low-priority rendering")

    async def stop(self):
        await self.renderer.stop()

    def queue_library_renders(self) -> int:
        queued = 0
        for text in talking_clock.all_part_texts():
            queued += self.renderer.request(text, "clock")
        for text in station_ids.generic_texts():
            queued += self.renderer.request(text, "station_id")
        return queued

    async def _verify_numbers(self, text: str, samples, rate: int) -> Optional[bool]:
        if not talking_clock.spoken_numbers(text) and "clock" not in text.lower():
            return None
        try:
            from services.whisper_dual_service import whisper_dual_service
            if not whisper_dual_service.models_loaded:
                return None
            buffer = io.BytesIO()
            sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
            heard = await whisper_dual_service.transcribe_fast(buffer.getvalue())
        except Exception as e:
            log_service.warning(f"[STINGS] Number check unavailable: {type(e).__name__}: {e}")
            return None
        if heard is None:
            return None
        ok = talking_clock.numbers_match(text, heard)
        if not ok:
            log_service.warning(f"[STINGS] Clock part '{text}' was heard as '{heard}' - re-rendering")
        return ok

    def _session(self, session_id: str) -> _SessionStings:
        now = self.clock()
        entry = self.sessions.get(session_id)
        if entry is None:
            entry = _SessionStings(now)
            self.sessions[session_id] = entry
        else:
            self.sessions.move_to_end(session_id)
        entry.state.last_seen = now
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return entry

    def forget_session(self, session_id: str):
        self.sessions.pop(session_id, None)
        self.library.forget_session(session_id)

    def voice_cached(self, text: str) -> bool:
        return self.store.has(text)

    def any_voice_cached(self, category: str) -> bool:
        return bool(self.store.clips(category))

    def _process(self, key: str, raw: AudioSegment) -> AudioSegment:
        with self._lock:
            cached = self._processed.get(key)
            if cached is not None:
                self._processed.move_to_end(key)
                return cached
        processed = self.audio_processing_service.process_station(raw)
        with self._lock:
            self._processed[key] = processed
            while len(self._processed) > PROCESSED_CACHE_MAX:
                self._processed.popitem(last=False)
        return processed

    def _request_city_render(self, text: str, city: str) -> bool:
        now = self.clock()
        while self._city_requests and now - self._city_requests[0] > 3600:
            self._city_requests.popleft()
        if len(self._city_requests) >= settings.STINGS_CITY_RENDERS_PER_HOUR:
            return False
        if self.renderer.request(text, "station_id", city=city):
            self._city_requests.append(now)
            return True
        return False

    def station_voice(self, text: str, category: str, city: Optional[str] = None, max_ms: Optional[int] = None,
                      allow_near: bool = False) -> Optional[Tuple[AudioSegment, str]]:
        clip = self.store.get(text)
        if clip is None:
            if city:
                self._request_city_render(text, city)
            else:
                self.renderer.request(text, category)
            if not allow_near:
                return None
            current = set(station_ids.generic_texts()) | (set(station_ids.city_texts(city)) if city else set())
            best = station_ids.best_cached_take(
                text, [(c.text, c.city) for c in self.store.clips(category) if c.text in current], city)
            clip = self.store.get(best) if best else None
            if clip is None:
                return None
        raw = self.store.audio(clip)
        if raw is None:
            return None
        processed = self._process(clip.key, raw)
        if max_ms is not None and len(processed) > max_ms:
            return None
        return processed, clip.text

    def clock_ready(self, hour24: int, minute: int) -> bool:
        needed = talking_clock.core_texts(hour24, minute)
        missing = self.store.missing(needed)
        intros = [t for t in talking_clock.intro_texts() if self.store.has(t)]
        for text in missing + ([] if intros else list(talking_clock.intro_texts()[:1])):
            self.renderer.request(text, "clock", urgent=True)
        return not missing and bool(intros)

    def clock_voice(self, hour24: int, minute: int, rng: random.Random):
        reading = talking_clock.choose_reading(hour24, minute, self.store.has, rng)
        if reading is None:
            self.clock_ready(hour24, minute)
            return None
        parts = []
        for text, gap in reading.parts:
            clip = self.store.get(text)
            audio = self.store.audio(clip) if clip is not None else None
            if audio is None:
                return None
            parts.append((audio, gap))
        stitched = talking_clock.stitch(parts)
        return self.audio_processing_service.process_station(stitched), reading

    async def _location(self, session_id: str, user_id: Optional[int], entry: _SessionStings):
        now = self.clock()
        if now - entry.location_at < LOCATION_TTL_S and entry.location_at:
            return entry.tz_name, entry.city
        user = None
        if user_id:
            try:
                from services.user_data_cache_service import user_data_cache
                user = await user_data_cache.get_user(user_id)
            except Exception as e:
                log_service.warning(f"[STINGS] Loading listener {user_id} failed: {type(e).__name__}: {e}")
        try:
            from services_radio.context_service import listener_location
            location = await listener_location(user, session_id, geocode=False)
            entry.tz_name = location.timezone
            entry.city = location.city or None
        except Exception as e:
            log_service.warning(f"[STINGS] Listener location unavailable: {type(e).__name__}: {e}")
        entry.location_at = now
        return entry.tz_name, entry.city

    def _radio(self):
        from service_registry import services
        return services.radio_mode_service

    def _gate(self, session_id: str, user_id: Optional[int], for_break: bool = False) -> sting_schedule.Gate:
        radio = self._radio()
        radio_sess = radio.sessions.get(session_id) if radio is not None else None
        radio_mode = bool(radio_sess is not None and radio_sess.prefs.enabled and settings.RADIO_MODE_ENABLED)
        pref = radio.stings_pref(session_id) if radio is not None else True
        blocked = bool(radio is not None and not for_break and radio.blocks_announcer(session_id))
        conversing = False
        if self.conversation_service is not None:
            try:
                conversing = (self.clock() - self.conversation_service.last_turn_at(session_id)
                              < settings.STINGS_CONVERSATION_QUIET_S) or \
                    self.conversation_service.turn_in_progress(session_id)
            except Exception:
                conversing = False
        busy = bool(self.tts_queue_manager is not None and not for_break
                    and self.tts_queue_manager.session_busy(session_id, user_id or 0))
        state = self.playback_service.sessions.get(session_id) if self.playback_service is not None else None
        device_ok = for_break or bool(state is not None and state.active_device_id and state.active_device_online)
        return sting_schedule.Gate(enabled=device_ok, radio_mode=radio_mode, stings_pref=pref,
                                   radio_blocked=blocked, conversing=conversing, tts_busy=busy)

    def _local(self, tz_name: Optional[str], at: float):
        if not tz_name:
            return None
        return radio_schedule.local_now(tz_name, at)

    def _context(self, session_id: str, entry: _SessionStings, max_len_s: float, midtrack: bool,
                 at: Optional[float] = None) -> sting_types.StingContext:
        at = self.clock() if at is None else at
        return sting_types.StingContext(session_id=session_id, now=at, local=self._local(entry.tz_name, at),
                                        city=entry.city, max_len_s=max_len_s, midtrack=midtrack, rng=self.rng,
                                        recent_ids=entry.state.recent_ids)

    async def plan_between_tracks(self, session_id: str, user_id: Optional[int], window_s: float,
                                  trigger_in_s: float = 0.0) -> Optional[str]:
        if not settings.STINGS_ENABLED:
            return None
        entry = self._session(session_id)
        gate = self._gate(session_id, user_id)
        if not gate.allowed():
            entry.state.breaks_since_sting += 1
            return None
        await self._location(session_id, user_id, entry)
        at = self.clock() + max(0.0, trigger_in_s)
        ctx = self._context(session_id, entry, window_s, midtrack=False, at=at)
        disabled = settings.STINGS_TYPES_DISABLED
        ready = [c for c in sting_types.candidates(disabled) if sting_types.get(c[0]).ready(self, ctx)]
        time_ready = any(c[0] == sting_schedule.TIME_CHECK for c in ready)
        decision = sting_schedule.decide_between_tracks(
            entry.state, gate, window_s, at, ctx.local.minute if ctx.local else None, ready, time_ready, self.rng)
        if decision.kind is None:
            entry.state.breaks_since_sting += 1
        log_service.announcer(f"[STINGS] [{session_id[:8]}] window {window_s:.1f}s -> "
                              f"{decision.kind or 'announcer'} ({decision.reason})")
        return decision.kind

    def midtrack_possible(self, session_id: str) -> bool:
        radio = self._radio()
        radio_sess = radio.sessions.get(session_id) if radio is not None else None
        if radio_sess is None or not radio_sess.prefs.enabled or not radio.stings_pref(session_id):
            return False
        entry = self.sessions.get(session_id)
        if entry is None:
            return True
        now = self.clock()
        last = entry.state.last_midtrack_at
        return last is None or now - last >= settings.STINGS_MIDTRACK_MIN_INTERVAL_S

    async def plan_midtrack(self, session_id: str, user_id: Optional[int], quiet_window_s: float) -> Optional[str]:
        if not (settings.STINGS_ENABLED and settings.STINGS_MIDTRACK_ENABLED):
            return None
        entry = self._session(session_id)
        gate = self._gate(session_id, user_id)
        await self._location(session_id, user_id, entry)
        ctx = self._context(session_id, entry, midtrack_max_len(quiet_window_s), midtrack=True)
        ready = [c for c in sting_types.candidates(settings.STINGS_TYPES_DISABLED, midtrack=True)
                 if sting_types.get(c[0]).ready(self, ctx)]
        time_ready = any(c[0] == sting_schedule.TIME_CHECK for c in ready)
        decision = sting_schedule.decide_midtrack(entry.state, gate, quiet_window_s, self.clock(), ready, self.rng,
                                                  ctx.local.minute if ctx.local else None, time_ready)
        if decision.kind:
            log_service.announcer(f"[STINGS] [{session_id[:8]}] mid-track {decision.kind} in a "
                                  f"{quiet_window_s:.1f}s quiet passage")
        return decision.kind

    async def build(self, session_id: str, user_id: Optional[int], kind: str, max_len_s: float,
                    midtrack: bool = False, lead_s: float = 0.0) -> Optional[sting_types.StingRender]:
        sting_type = sting_types.get(kind)
        if sting_type is None:
            return None
        entry = self._session(session_id)
        await self._location(session_id, user_id, entry)
        ctx = self._context(session_id, entry, max_len_s, midtrack, at=self.clock() + max(0.0, lead_s))
        try:
            return await asyncio.to_thread(sting_type.build, self, ctx)
        except Exception as e:
            log_service.error(f"[STINGS] Building {kind} failed: {type(e).__name__}: {e}")
            return None

    async def play(self, session_id: str, user_id: Optional[int], render: sting_types.StingRender,
                   midtrack: bool = False) -> bool:
        gate = self._gate(session_id, user_id)
        if not gate.allowed():
            log_service.announcer(f"[STINGS] [{session_id[:8]}] {render.kind} dropped at air time (gate closed)")
            return False
        clip = PrerenderedClip(render.audio, render.marks, render.label)
        accepted = await self.tts_queue_manager.add_clip_request(
            clip, user_id=user_id or 0, tts_type=TTS_TYPE, is_temp_user=user_id is None, session_id=session_id)
        if not accepted:
            return False
        entry = self._session(session_id)
        entry.state.record(render.kind, self.clock(), midtrack=midtrack)
        self.plays[render.kind] = self.plays.get(render.kind, 0) + 1
        with usage_tracking.subject_scope(session_id=session_id, user_id=user_id):
            usage_tracking.record_gpu(f"stings.{render.kind}", 0.0, audio_seconds=len(render.audio) / 1000,
                                      model="orpheus-3b" if render.voice_s else "suno", cache_hit=True)
        log_service.announcer(f"[STINGS] [{session_id[:8]}] ON AIR {render.label} ({len(render.audio) / 1000:.1f}s)"
                              + (f": \"{render.text}\"" if render.text else ""))
        return True

    async def lead_in_for_break(self, session_id: str, user_id: Optional[int]) -> Optional[PrerenderedClip]:
        if not settings.STINGS_ENABLED or self.rng.random() >= settings.STINGS_BREAK_LEAD_IN_PROBABILITY:
            return None
        gate = self._gate(session_id, user_id, for_break=True)
        if not gate.allowed():
            return None
        kind = "station_id" if self.rng.random() < 0.6 else "logo_id"
        sting_type = sting_types.get(kind)
        if sting_type is None or kind in settings.STINGS_TYPES_DISABLED:
            return None
        render = await self.build(session_id, user_id, kind, LEAD_IN_MAX_S)
        if render is None:
            return None
        self._session(session_id).state.record(render.kind, self.clock())
        return PrerenderedClip(render.audio, render.marks, f"{render.label} (break lead-in)")

    def status(self) -> dict:
        return {
            "station_parts_cached": len(self.store.clips()),
            "clock_parts_missing": len(self.store.missing(talking_clock.all_part_texts())),
            "ids_missing": len(self.store.missing(station_ids.generic_texts())),
            "render_queue": self.renderer.pending(),
            "rendered_this_run": self.renderer.rendered,
            "render_gpu_s": round(self.renderer.gpu_seconds, 1),
            "stings": len(self.library.stings()),
            "sfx_beds": len(self.library.sfx()),
            "plays": dict(self.plays),
        }

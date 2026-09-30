import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from config.settings import settings
from services import log_service
from services import usage_tracking
from services import listener_timeline
from services.task_utils import spawn
from services_radio import radio_schedule as schedule
from services_radio import radio_segments
from services_radio import talk_clock
from services_radio import regional_knowledge as regional_kb
from services_radio import listener_location as location_resolver
from services_radio.dj_content_bank import content_bank, spoken_text
from services_radio.music_beds import music_beds

PUBLIC_STATUSES = ("rendering", "ready", "on_air")
TTS_TYPE = "radio_segment"
BUILD_TIMEOUT_S = 50.0
NO_CONTENT_BACKOFF_S = 300.0
FAILURE_BACKOFF_S = 180.0
SHARED_SCRIPTS_MAX = 200
STING_PREFS_MAX = 5000
MIN_SCRIPT_WORDS = 40
MAX_REPORTED_DURATION_S = 600.0


@dataclass
class BreakPlan:
    id: str
    kind: str
    label: str
    slot: schedule.Slot
    title: str = ""
    status: str = "writing"
    script: str = ""
    keys: list = field(default_factory=list)
    on_air_hooks: list = field(default_factory=list)
    bed: Optional[dict] = None
    created_at: float = 0.0
    render_at: float = 0.0
    render_attempts: int = 0
    failures: int = 0
    stream_id: str = ""
    ready_device_id: Optional[str] = None
    duration_s: float = 0.0
    started_at: float = 0.0
    device_id: Optional[str] = None
    words: int = 0
    shared: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "stream_id": self.stream_id,
            "kind": self.kind,
            "label": self.label,
            "title": self.title or self.label,
            "status": self.status,
            "bed": self.bed,
            "duration_s": round(self.duration_s, 2) if self.duration_s else None,
        }


@dataclass
class SessionRadio:
    session_id: str
    user_id: Optional[int]
    prefs: schedule.RadioPrefs
    enabled_at: float
    feature_anchor: float
    tz_name: Optional[str] = None
    tts_muted: bool = False
    last_break_at: Optional[float] = None
    served: dict = field(default_factory=dict)
    aired: dict = field(default_factory=dict)
    feature_turn: int = 0
    plan: Optional[BreakPlan] = None
    backoff_until: float = 0.0
    last_seen: float = 0.0
    published: Optional[tuple] = None
    writer: Optional[asyncio.Task] = None


class RadioModeService:
    def __init__(self, playback_service, dj_prompt_service, tts_queue_manager, websocket_service=None,
                 conversation_service=None, async_session_maker=None, catalog_service=None,
                 broadcast: Optional[Callable[[str, dict], Awaitable[None]]] = None,
                 clock: Callable[[], float] = time.time):
        self.playback_service = playback_service
        self.dj_prompt_service = dj_prompt_service
        self.tts_queue_manager = tts_queue_manager
        self.websocket_service = websocket_service
        self.conversation_service = conversation_service
        self.async_session_maker = async_session_maker
        self.catalog_service = catalog_service
        self.broadcast = broadcast
        self.clock = clock
        self.sessions: dict[str, SessionRadio] = {}
        self._shared_scripts: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
        self._shared_inflight: dict[str, asyncio.Future] = {}
        self._script_times: deque = deque()
        self._sting_prefs: "OrderedDict[str, tuple]" = OrderedDict()
        self._loop_task: Optional[asyncio.Task] = None
        self._tick_lock = asyncio.Lock()

    async def start(self):
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = spawn(self._loop(), name="radio-mode-scheduler")
        log_service.announcer("[RADIO] Radio Mode scheduler started")

    async def stop(self):
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
        for sess in list(self.sessions.values()):
            self._cancel_writer(sess)

    async def _loop(self):
        while True:
            await asyncio.sleep(settings.RADIO_TICK_S)
            try:
                await self.tick()
            except Exception as e:
                log_service.error(f"[RADIO] Scheduler tick failed: {type(e).__name__}: {e}")

    def _connected(self, session_id: str) -> bool:
        return bool(self.websocket_service and self.websocket_service.has_session(session_id))

    def _playback(self, session_id: str):
        return self.playback_service.sessions.get(session_id) if self.playback_service else None

    def _last_turn_at(self, session_id: str) -> float:
        if self.conversation_service is None:
            return 0.0
        return self.conversation_service.last_turn_at(session_id)

    def _conversing(self, sess: SessionRadio, now: float) -> bool:
        if self.conversation_service is None:
            return False
        if now - self._last_turn_at(sess.session_id) < settings.RADIO_CONVERSATION_QUIET_S:
            return True
        return self.conversation_service.turn_in_progress(sess.session_id)

    @staticmethod
    def _cancel_writer(sess: SessionRadio):
        writer = sess.writer
        sess.writer = None
        if writer is not None and not writer.done() and writer is not asyncio.current_task():
            writer.cancel()

    def prefs_for(self, session_id: str) -> Optional[dict]:
        sess = self.sessions.get(session_id)
        return sess.prefs.to_dict() if sess else None

    def stings_pref(self, session_id: str) -> bool:
        return self._sting_prefs.get(session_id, (True, True, "both"))[0]

    def reviews_pref(self, session_id: str) -> bool:
        return self._sting_prefs.get(session_id, (True, True, "both"))[1]

    def music_source(self, session_id: str) -> str:
        return self._sting_prefs.get(session_id, (True, True, "both"))[2]

    def _remember_sting_pref(self, session_id: str, stings: bool, reviews: bool = True, source: str = "both"):
        self._sting_prefs.pop(session_id, None)
        self._sting_prefs[session_id] = (stings, reviews, source)
        while len(self._sting_prefs) > STING_PREFS_MAX:
            self._sting_prefs.popitem(last=False)

    def _apply_prefs(self, session_id: str, user_id: Optional[int], prefs: schedule.RadioPrefs,
                     tz_name: Optional[str], tts_muted: bool) -> Optional[SessionRadio]:
        now = self.clock()
        self._remember_sting_pref(session_id, prefs.stings, prefs.reviews, prefs.music_source)
        sess = self.sessions.get(session_id)
        if sess is None:
            if not prefs.enabled:
                return None
            sess = SessionRadio(session_id=session_id, user_id=user_id, prefs=prefs, enabled_at=now,
                                feature_anchor=now, last_seen=now)
            self.sessions[session_id] = sess
            log_service.announcer(f"[RADIO] [{session_id[:8]}] Radio Mode on")
        else:
            if prefs.enabled and not sess.prefs.enabled:
                sess.enabled_at = now
                sess.feature_anchor = now
                log_service.announcer(f"[RADIO] [{session_id[:8]}] Radio Mode on")
            elif sess.prefs.enabled and not prefs.enabled:
                log_service.announcer(f"[RADIO] [{session_id[:8]}] Radio Mode off")
            sess.prefs = prefs
        sess.user_id = user_id
        sess.tz_name = tz_name or sess.tz_name
        sess.tts_muted = tts_muted
        sess.last_seen = now
        return sess

    async def _refresh_after_prefs(self, sess: Optional[SessionRadio]):
        if sess is None or sess.plan is None:
            return
        if not sess.prefs.enabled or sess.tts_muted:
            if sess.plan.status != "on_air":
                await self._drop(sess, "disabled", served=False)
            return
        segment = radio_segments.get_segment(sess.plan.kind)
        if segment is not None and not sess.prefs.allows(segment.pref) and sess.plan.status != "on_air":
            await self._drop(sess, "segment_disabled", served=False)

    async def set_guest_prefs(self, session_id: str, raw: Any) -> dict:
        prefs = schedule.normalize_prefs(raw)
        sess = self._apply_prefs(session_id, None, prefs, location_resolver.session_timezone(session_id), False)
        await self._refresh_after_prefs(sess)
        return prefs.to_dict()

    async def set_user_prefs(self, user_id: int, prefs_dict: dict, tz_name: Optional[str] = None,
                             tts_muted: bool = False) -> dict:
        prefs = schedule.normalize_prefs(prefs_dict)
        sess = self._apply_prefs(str(user_id), user_id, prefs, tz_name, tts_muted)
        await self._refresh_after_prefs(sess)
        return prefs.to_dict()

    def note_tts_muted(self, session_id: str, muted: bool):
        sess = self.sessions.get(session_id)
        if sess is not None:
            sess.tts_muted = muted

    async def on_connect(self, session_id: str, user_id: Optional[int]):
        sess = self.sessions.get(session_id)
        if sess is not None:
            sess.last_seen = self.clock()
        if not user_id or self.async_session_maker is None:
            return
        try:
            from database.models import User
            from services.preferences_service import preferences_service
            async with self.async_session_maker() as db:
                user = await db.get(User, user_id)
                prefs = await preferences_service.get_radio_settings(user_id, db)
            if user is None:
                return
            await self.set_user_prefs(user_id, prefs, getattr(user, "timezone", None),
                                      bool(getattr(user, "tts_muted", False)))
        except Exception as e:
            log_service.warning(f"[RADIO] Loading Radio Mode settings for user {user_id} failed: {type(e).__name__}: {e}")

    async def tick(self):
        async with self._tick_lock:
            now = self.clock()
            for session_id, sess in list(self.sessions.items()):
                if not self._connected(session_id):
                    if now - sess.last_seen > settings.RADIO_IDLE_EVICT_S:
                        self._cancel_writer(sess)
                        self.sessions.pop(session_id, None)
                    continue
                sess.last_seen = now
                try:
                    await self._tick_session(sess, now)
                except Exception as e:
                    log_service.error(f"[RADIO] [{session_id[:8]}] tick failed: {type(e).__name__}: {e}")

    def _eligible(self, sess: SessionRadio, state) -> bool:
        return bool(
            settings.RADIO_MODE_ENABLED and sess.prefs.enabled and not sess.tts_muted and state is not None
            and state.active_device_id and state.active_device_online and state.is_playing
            and state.current_track is not None
        )

    async def _tick_session(self, sess: SessionRadio, now: float):
        state = self._playback(sess.session_id)
        plan = sess.plan
        if plan is not None and plan.status == "on_air":
            if now - plan.started_at > settings.RADIO_ON_AIR_MAX_S:
                await self._finish(sess, plan, "on_air_timeout")
            elif self._last_turn_at(sess.session_id) > plan.started_at:
                await self._finish(sess, plan, "conversation")
            return
        if not self._eligible(sess, state):
            if plan is not None and (not sess.prefs.enabled or sess.tts_muted or not settings.RADIO_MODE_ENABLED):
                await self._drop(sess, "disabled", served=False)
            return

        duration_ms = (state.current_track.get("track_info") or {}).get("duration") or 0
        if duration_ms <= 0:
            return
        remaining_s = max(0.0, (duration_ms - state.get_simulated_progress()) / 1000.0)
        boundary_at = now + remaining_s

        if plan is not None:
            await self._advance(sess, state, plan, now, boundary_at)
            return

        if now < sess.backoff_until or remaining_s > settings.RADIO_PREPARE_LEAD_S or self._conversing(sess, now):
            return
        sess.served = schedule.prune_served(sess.served, now)
        disabled = settings.RADIO_SEGMENTS_DISABLED
        features = radio_segments.feature_kinds(sess.prefs, disabled)
        slot = schedule.choose_slot(radio_segments.clock_rules(disabled), sess.prefs, bool(features), sess.tz_name,
                                    boundary_at, sess.last_break_at, sess.feature_anchor, sess.served)
        if slot is None:
            return
        kinds = [slot.kind] if slot.clock else schedule.feature_rotation(
            [k for k in features if k != "for_you"], sess.feature_turn)
        if not slot.clock and "for_you" in features and radio_segments.for_you_due(sess.session_id):
            kinds = ["for_you"] + kinds
        if not kinds:
            return
        first = radio_segments.get_segment(kinds[0])
        plan = BreakPlan(id=uuid.uuid4().hex[:16], kind=kinds[0], label=first.label if first else kinds[0],
                         slot=slot, created_at=now)
        sess.plan = plan
        log_service.announcer(
            f"[RADIO] [{sess.session_id[:8]}] Break planned: {'/'.join(kinds)} ({slot.key}) at the end of the current "
            f"track, {remaining_s:.0f}s away")
        sess.writer = spawn(self._write(sess, plan, kinds, state), name=f"radio_write_{sess.session_id}")

    async def _advance(self, sess: SessionRadio, state, plan: BreakPlan, now: float, boundary_at: float):
        if plan.slot.clock and plan.slot.expires_at and boundary_at > plan.slot.expires_at:
            await self._drop(sess, "expired", served=True)
            return
        if plan.status in ("rendering", "ready") and self._last_turn_at(sess.session_id) > plan.render_at:
            log_service.announcer(f"[RADIO] [{sess.session_id[:8]}] Listener talked to the DJs - break deferred, "
                                  "re-rendering later")
            plan.status = "scripted"
            plan.ready_device_id = None
        if plan.status == "ready" and plan.ready_device_id != state.active_device_id:
            log_service.announcer(f"[RADIO] [{sess.session_id[:8]}] Active device changed - re-rendering the break")
            plan.status = "scripted"
            plan.ready_device_id = None
        if plan.status == "rendering" and now - plan.render_at > settings.RADIO_READY_TIMEOUT_S:
            plan.failures += 1
            if plan.failures >= settings.RADIO_MAX_RENDER_ATTEMPTS:
                await self._drop(sess, "render_timeout", served=True, backoff_s=FAILURE_BACKOFF_S)
                return
            plan.status = "scripted"
        if plan.status == "scripted" and not self._conversing(sess, now):
            await self._render(sess, plan, now)
        await self._publish(sess)

    async def _render(self, sess: SessionRadio, plan: BreakPlan, now: float):
        plan.render_attempts += 1
        plan.render_at = now
        plan.stream_id = f"{plan.id}-{plan.render_attempts}"
        plan.status = "rendering"
        plan.ready_device_id = None
        request = dict(
            text=plan.script,
            user_id=sess.user_id or 0,
            tts_type=TTS_TYPE,
            is_broadcast=True,
            is_temp_user=sess.user_id is None,
            session_id=sess.session_id,
            stream_id=plan.stream_id,
        )
        lead_in = await self._break_lead_in(sess)
        if lead_in is not None:
            request["lead_in"] = lead_in
        accepted = await self.tts_queue_manager.add_tts_request(**request)
        if accepted is False:
            sess.tts_muted = True
            await self._drop(sess, "dj_voice_muted", served=False)
            return
        log_service.announcer(f"[RADIO] [{sess.session_id[:8]}] Rendering {plan.label} break "
                              f"(attempt {plan.render_attempts}, {plan.words} words)")

    async def _break_lead_in(self, sess: SessionRadio):
        if not sess.prefs.stings:
            return None
        from service_registry import services
        stings = services.sting_service
        if stings is None:
            return None
        try:
            return await stings.lead_in_for_break(sess.session_id, sess.user_id)
        except Exception as e:
            log_service.warning(f"[RADIO] Break lead-in skipped: {type(e).__name__}: {e}")
            return None

    async def _context(self, sess: SessionRadio, state) -> radio_segments.SegmentContext:
        user = None
        if sess.user_id and self.async_session_maker is not None:
            from database.models import User
            async with self.async_session_maker() as db:
                user = await db.get(User, sess.user_id)
        if user is not None:
            sess.tts_muted = bool(getattr(user, "tts_muted", False))
        location = await location_resolver.resolve(user, sess.session_id)
        tz_name = (getattr(user, "timezone", None) if user is not None else None) or location.timezone \
            or sess.tz_name or content_bank.session_timezone(sess.session_id)
        sess.tz_name = tz_name
        now = self.clock()
        queue = list(getattr(state, "queue", []) or [])
        index = state.current_index if state is not None else 0

        def brief(offset: int) -> dict:
            position = index + offset
            if not (0 <= position < len(queue)):
                return {}
            track = queue[position] or {}
            params = track.get("generation_params") or {}
            tags = track.get("derived_tags") or {}
            return {
                "id": track.get("id"),
                "title": params.get("title") or (track.get("track_info") or {}).get("title") or "",
                "artist": (params.get("artist_name") or "").strip(),
                "genre": str(tags.get("primary_genre") or "").strip(),
                "style": params.get("style_canonical") or params.get("style") or "",
            }

        return radio_segments.SegmentContext(
            session_id=sess.session_id,
            user_id=sess.user_id,
            user=user,
            tz_name=tz_name,
            region=regional_kb.resolve_region(user, tz_name, location=location),
            now_local=schedule.local_now(tz_name, now),
            next_track=brief(1),
            upcoming_track=brief(2),
            current_track=brief(0),
            dj_service=self.dj_prompt_service,
            async_session_maker=self.async_session_maker,
            catalog_service=self.catalog_service,
            aired={key for key, at in sess.aired.items() if now - at < settings.RADIO_AIRED_MEMORY_S},
            location=location,
            prefs=sess.prefs,
        )

    def _shared_key(self, segment, content, ctx) -> str:
        bucket = ctx.now_local.strftime("%Y%m%d%H") + ("b" if ctx.now_local.minute >= 30 else "a")
        region = ctx.region.key if ctx.region else "none"
        raw = f"{segment.kind}|{region}|{bucket}|{content.fingerprint()}"
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _shared_get(self, key: str) -> Optional[str]:
        entry = self._shared_scripts.get(key)
        if not entry or self.clock() - entry[0] > settings.RADIO_SHARED_SCRIPT_TTL_S:
            return None
        return entry[1]

    def _shared_put(self, key: str, script: str):
        self._shared_scripts.pop(key, None)
        self._shared_scripts[key] = (self.clock(), script)
        while len(self._shared_scripts) > SHARED_SCRIPTS_MAX:
            self._shared_scripts.popitem(last=False)

    async def write_script(self, segment, content, ctx) -> tuple[Optional[str], bool]:
        shared_key = self._shared_key(segment, content, ctx) if content.shareable else None
        if not shared_key:
            return await self._generate_script(segment, content, ctx, None)
        cached = self._shared_get(shared_key)
        if cached:
            usage_tracking.record_cache_hit("radio_segment", feature=f"radio_mode.{segment.kind}")
            return cached, True
        pending = self._shared_inflight.get(shared_key)
        if pending is not None:
            script = await asyncio.shield(pending)
            if script:
                usage_tracking.record_cache_hit("radio_segment", feature=f"radio_mode.{segment.kind}")
                return script, True
            return None, False
        future = asyncio.get_running_loop().create_future()
        self._shared_inflight[shared_key] = future
        script = None
        try:
            script, _ = await self._generate_script(segment, content, ctx, shared_key)
            return script, False
        finally:
            self._shared_inflight.pop(shared_key, None)
            if not future.done():
                future.set_result(script)

    def _script_budget_left(self) -> bool:
        now = self.clock()
        while self._script_times and now - self._script_times[0] > 3600:
            self._script_times.popleft()
        if len(self._script_times) >= settings.RADIO_MAX_SCRIPTS_PER_HOUR:
            return False
        self._script_times.append(now)
        return True

    async def _generate_script(self, segment, content, ctx, shared_key: Optional[str]) -> tuple[Optional[str], bool]:
        if not self._script_budget_left():
            log_service.warning(f"[RADIO] Station-wide script budget reached "
                                f"({settings.RADIO_MAX_SCRIPTS_PER_HOUR}/h) - skipping {segment.kind}")
            return None, False
        spec = radio_segments.segment_prompt(segment, content, ctx, talk_clock.pace())
        if shared_key:
            spec["next_track"] = ""
        session_dict = {"session_id": ctx.session_id, "user_id": ctx.user_id}
        script = await asyncio.wait_for(
            self.dj_prompt_service.gpt_radio_segment(spec, content.facts_text(), session_dict,
                                                     shared=bool(shared_key)),
            settings.RADIO_WRITE_TIMEOUT_S)
        if not script:
            return None, False
        words = len(spoken_text(script).split())
        if words < max(MIN_SCRIPT_WORDS, spec["min_words"] // 2):
            log_service.warning(f"[RADIO] {segment.kind} script too short ({words} words) - skipped")
            return None, False
        if shared_key:
            self._shared_put(shared_key, script)
        return script, False

    async def _write(self, sess: SessionRadio, plan: BreakPlan, kinds: list, state):
        usage_tracking.bind_session(sess.session_id, sess.user_id)
        try:
            ctx = await self._context(sess, state)
            if sess.tts_muted:
                await self._drop(sess, "dj_voice_muted", served=False)
                return
            for position, kind in enumerate(kinds):
                segment = radio_segments.get_segment(kind)
                if segment is None or (ctx.is_guest and not segment.guest_allowed):
                    continue
                with usage_tracking.feature_scope(f"radio_mode.{kind}"):
                    try:
                        content = await asyncio.wait_for(segment.build(ctx), BUILD_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        log_service.warning(f"[RADIO] [{sess.session_id[:8]}] {kind} content timed out")
                        content = None
                    if not content or not content.facts:
                        log_service.announcer(f"[RADIO] [{sess.session_id[:8]}] Nothing to say for {kind}")
                        continue
                    script, shared = await self.write_script(segment, content, ctx)
                if not script:
                    continue
                if sess.plan is not plan:
                    return
                bed = music_beds.pick(kind, sess.session_id, content.moods)
                plan.kind = kind
                plan.label = segment.label
                plan.title = content.title or segment.label
                plan.script = script
                plan.keys = list(content.keys)
                plan.on_air_hooks = list(content.on_air)
                plan.words = len(spoken_text(script).split())
                plan.shared = shared
                plan.bed = bed.payload(settings.RADIO_BED_TARGET_LUFS) if bed else None
                plan.status = "scripted"
                if not plan.slot.clock:
                    sess.feature_turn += position + 1
                log_service.announcer(
                    f"[RADIO] [{sess.session_id[:8]}] {segment.label} script ready: {plan.words} words"
                    f"{' (shared)' if shared else ''}, bed {plan.bed['id'] if plan.bed else 'none'}")
                if not self._conversing(sess, self.clock()):
                    await self._render(sess, plan, self.clock())
                await self._publish(sess)
                return
            if sess.plan is plan:
                await self._drop(sess, "no_content", served=plan.slot.clock,
                                 backoff_s=0.0 if plan.slot.clock else NO_CONTENT_BACKOFF_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_service.error(f"[RADIO] [{sess.session_id[:8]}] Writing the break failed: {type(e).__name__}: {e}")
            if sess.plan is plan:
                await self._drop(sess, "write_failed", served=plan.slot.clock, backoff_s=FAILURE_BACKOFF_S)
        finally:
            if sess.writer is asyncio.current_task():
                sess.writer = None

    async def _publish(self, sess: SessionRadio, notify: bool = True):
        state = self._playback(sess.session_id)
        if state is None:
            return
        plan = sess.plan
        payload = plan.public() if plan is not None and plan.status in PUBLIC_STATUSES else None
        marker = (payload["id"], payload["status"], payload["stream_id"]) if payload else None
        if marker == sess.published:
            return
        sess.published = marker
        state.talk_break = payload
        if notify:
            await self.playback_service.broadcast_session_state(sess.session_id)

    async def _drop(self, sess: SessionRadio, reason: str, served: bool, backoff_s: float = 0.0,
                    notify: bool = True):
        plan = sess.plan
        if plan is None:
            return
        now = self.clock()
        sess.plan = None
        self._cancel_writer(sess)
        if served:
            sess.served[plan.slot.key] = now
        if backoff_s:
            sess.backoff_until = now + backoff_s
        log_service.announcer(f"[RADIO] [{sess.session_id[:8]}] {plan.label} break dropped ({reason})")
        await self._publish(sess, notify=notify)

    async def _finish(self, sess: SessionRadio, plan: BreakPlan, reason: str, notify: bool = True):
        if sess.plan is not plan:
            return
        now = self.clock()
        sess.plan = None
        sess.last_break_at = now
        sess.feature_anchor = now
        sess.served[plan.slot.key] = now
        log_service.announcer(
            f"[RADIO] [{sess.session_id[:8]}] {plan.label} break finished ({reason}, "
            f"{now - plan.started_at:.0f}s on air)")
        await self._publish(sess, notify=notify)

    async def handle_client_event(self, session_id: str, device_id: str, event: str, data: dict):
        sess = self.sessions.get(session_id)
        plan = sess.plan if sess is not None else None
        if plan is None or not isinstance(data, dict) or data.get("break_id") != plan.id:
            return
        stream_id = data.get("stream_id")
        now = self.clock()
        state = self._playback(session_id)
        if event in ("talk_break_ready", "talk_break_start") and state is not None and state.active_device_id                 and device_id != state.active_device_id:
            return
        if event == "talk_break_ready":
            if plan.status != "rendering" or stream_id != plan.stream_id:
                return
            try:
                duration = float(data.get("duration_s") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            plan.duration_s = max(0.0, min(duration, MAX_REPORTED_DURATION_S))
            plan.status = "ready"
            plan.ready_device_id = device_id
            log_service.announcer(f"[RADIO] [{session_id[:8]}] {plan.label} break ready on {device_id[:8]} "
                                  f"({plan.duration_s:.0f}s)")
            await self._publish(sess)
        elif event == "talk_break_failed":
            if plan.status not in ("rendering", "ready") or stream_id != plan.stream_id:
                return
            log_service.announcer(f"[RADIO] [{session_id[:8]}] Client could not stage the break "
                                  f"({str(data.get('reason'))[:40]})")
            plan.failures += 1
            if plan.failures >= settings.RADIO_MAX_RENDER_ATTEMPTS:
                await self._drop(sess, "client_failed", served=True, backoff_s=FAILURE_BACKOFF_S)
                return
            plan.status = "scripted"
            plan.ready_device_id = None
            await self._publish(sess)
        elif event == "talk_break_start":
            if plan.status not in ("ready", "rendering"):
                return
            plan.status = "on_air"
            plan.started_at = now
            plan.device_id = device_id
            for key in plan.keys:
                sess.aired[key] = now
            sess.aired = {k: at for k, at in sess.aired.items() if now - at < settings.RADIO_AIRED_MEMORY_S}
            content_bank.record_airing(session_id, plan.script)
            content_bank.mark_offered(session_id, plan.keys)
            spawn(listener_timeline.record_talk(sess.user_id, session_id, TTS_TYPE, spoken_text(plan.script),
                                                label=f"{plan.label} break", seconds=plan.duration_s),
                  name=f"aired_talk:{session_id}")
            for hook in plan.on_air_hooks:
                try:
                    hook()
                except Exception as e:
                    log_service.warning(f"[RADIO] on-air hook failed: {type(e).__name__}: {e}")
            log_service.announcer(f"[RADIO] [{session_id[:8]}] ON AIR: {plan.label} ({plan.words} words)")
            if self.broadcast is not None:
                await self.broadcast(session_id, {"type": "conversation_update", "data": {
                    "bot_response": plan.script,
                    "message_type": "announcer",
                    "segment": plan.label,
                }})
            await self._publish(sess)
        elif event == "talk_break_end":
            reason = str(data.get("reason") or "done")[:40]
            if plan.status == "on_air":
                await self._finish(sess, plan, reason)
            elif plan.status in ("ready", "rendering"):
                plan.status = "scripted"
                plan.ready_device_id = None
                await self._publish(sess)

    async def on_user_transport(self, session_id: str, command: str):
        sess = self.sessions.get(session_id)
        plan = sess.plan if sess is not None else None
        if plan is not None and plan.status == "on_air":
            await self._finish(sess, plan, f"user_{command}", notify=False)

    async def on_transfer(self, session_id: str, target_device_id: Optional[str]):
        sess = self.sessions.get(session_id)
        plan = sess.plan if sess is not None else None
        if plan is None:
            return
        if plan.status == "on_air" and target_device_id != plan.device_id:
            await self._finish(sess, plan, "device_transfer", notify=False)
        elif plan.status == "ready" and target_device_id != plan.ready_device_id:
            plan.status = "scripted"
            plan.ready_device_id = None
            await self._publish(sess, notify=False)

    def blocks_announcer(self, session_id: str) -> bool:
        sess = self.sessions.get(session_id)
        if sess is None or not sess.prefs.enabled:
            return False
        if sess.plan is not None:
            return True
        return sess.last_break_at is not None and \
            self.clock() - sess.last_break_at < settings.RADIO_ANNOUNCER_QUIET_AFTER_S

    def status(self, session_id: str) -> Optional[dict]:
        sess = self.sessions.get(session_id)
        if sess is None:
            return None
        return {
            "prefs": sess.prefs.to_dict(),
            "tz": sess.tz_name,
            "last_break_at": sess.last_break_at,
            "feature_anchor": sess.feature_anchor,
            "plan": sess.plan.public() if sess.plan else None,
            "plan_status": sess.plan.status if sess.plan else None,
        }

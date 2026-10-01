import asyncio
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import random
import time
from typing import Optional, List, Dict, Callable, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
from database import AsyncSessionLocal
from database.models import Conversation, User
from services import log_service
from services_radio.tts_stream_planner import spoken_text
from services_radio import talk_clock
from services_radio.filler_scripts import IMPULSE, conversation_context, plain_talk
from services import usage_tracking
from services.task_utils import spawn
from config.settings import settings

FILLER_CONTEXT_TURNS = 2
FILLER_CONTEXT_CHARS = 200
TEMP_CONVERSATION_MAX_SESSIONS = 1000
TEMP_CONVERSATION_TTL_S = 6 * 3600
FAILED_TURN_LINES = (
    "[BROADCAST] [LEO] &0.2& ~groans~ Ah, the desk just ate that one. &0.1& Hit us again?",
    "[BROADCAST] [JESS] &0.2& ~sighs~ Lost you in the static there. &0.1& Say that again for us?",
    "[BROADCAST] [LEO] &0.2& Hold up, the studio gremlins got that one. &0.1& Try us one more time.",
    "[BROADCAST] [JESS] &0.2& ~chuckles~ Pirate gear, baby. &0.1& That one didn't come through, go again?",
)

temp_conversations: Dict[str, List[str]] = {}
_turn_logger: Optional[logging.Logger] = None


def _turn_trace_logger() -> logging.Logger:
    global _turn_logger
    if _turn_logger is None:
        logs_dir = Path(settings.LOGS_DIR)
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(logs_dir / "dj_turns.jsonl", when="H", interval=1,
                                           backupCount=max(1, settings.DJ_TURN_TRACE_HOURS), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        _turn_logger = logging.getLogger("plair.dj_turns")
        _turn_logger.setLevel(logging.INFO)
        _turn_logger.propagate = False
        _turn_logger.addHandler(handler)
    return _turn_logger


def write_turn_trace(record: Dict) -> None:
    if not settings.DJ_TURN_TRACE_ENABLED:
        return
    try:
        _turn_trace_logger().info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception as e:
        log_service.warning(f"[DJ TRACE] could not write turn trace: {type(e).__name__}: {e}")
_temp_conversation_touched: Dict[str, float] = {}

def deduplicate_conversation_history(text_history: List[str]) -> List[str]:
    if not text_history:
        return text_history

    deduplicated = []
    last_bot_response = None

    for _, entry in enumerate(text_history):
        if not entry.startswith('['):
            cleaned_entry = entry.replace('[BROADCAST]', '').replace('[TXT]', '').replace('[LEO]', '').replace(
                '[JESS]', '').strip()

            if cleaned_entry == last_bot_response:
                log_service.detail(f"[Dedup] Skipping duplicate bot response: {cleaned_entry[:50]}...", "conversation")
                continue

            last_bot_response = cleaned_entry

        deduplicated.append(entry)

    removed_count = len(text_history) - len(deduplicated)
    if removed_count > 0:
        log_service.detail(f"[Dedup] Removed {removed_count} duplicate responses from conversation history",
                           "conversation")

    return deduplicated

async def save_conversation_to_database(
        user_id: int,
        db: AsyncSession,
        user_input: Optional[str] = None,
        bot_response: Optional[str] = None,
        commands: Optional[str] = None,
        info: Optional[str] = None,
        warning: Optional[str] = None,
        error: Optional[str] = None,
        audio_file_path: Optional[str] = None,
        message_type: str = 'interactive'
):
    if user_id:
        conversation = Conversation(user_id=user_id, message_type=message_type)
        saved_fields = []

        if user_input and user_input.strip():
            conversation.user_input = user_input.strip()  # type: ignore
            saved_fields.append(f"User Input: {conversation.user_input[:50]}...")

        if bot_response and bot_response.strip():
            conversation.bot_response = bot_response.strip()  # type: ignore
            saved_fields.append(f"Bot Response: {conversation.bot_response[:50]}...")

        if commands and commands.strip():
            conversation.commands = commands.strip()  # type: ignore
            saved_fields.append(f"Commands: {conversation.commands}")

        if info and info.strip():
            conversation.info_message = info.strip()  # type: ignore
            saved_fields.append(f"Info: {conversation.info_message[:50]}...")

        if warning and warning.strip():
            conversation.warning_message = warning.strip()  # type: ignore
            saved_fields.append(f"Warning: {conversation.warning_message[:50]}...")

        if error and error.strip():
            conversation.error_message = error.strip()  # type: ignore
            saved_fields.append(f"Error: {conversation.error_message[:50]}...")

        if audio_file_path:
            conversation.audio_file_path = audio_file_path  # type: ignore
            saved_fields.append(f"Audio: {audio_file_path}")

        if saved_fields:
            db.add(conversation)
            await db.commit()
            log_service.conversation(f"Saved for User ID: {user_id}")
            for field in saved_fields:
                log_service.conversation(f"  {field}")

        return conversation
    else:
        log_service.conversation("User not authenticated. Cannot save conversation.")
        return None

def _prune_temp_conversations(now: float):
    for stale in [key for key, touched in _temp_conversation_touched.items() if now - touched >= TEMP_CONVERSATION_TTL_S]:
        temp_conversations.pop(stale, None)
        _temp_conversation_touched.pop(stale, None)
    while len(temp_conversations) > TEMP_CONVERSATION_MAX_SESSIONS:
        oldest = next(iter(temp_conversations))
        temp_conversations.pop(oldest, None)
        _temp_conversation_touched.pop(oldest, None)

def save_temp_conversation(temp_user_id: str, transcription: str, response: str):
    now = time.time()
    entries = temp_conversations.pop(temp_user_id, [])

    conversation_entry = f"[LISTENER TXT] {transcription}\n{response}"
    entries.append(conversation_entry)

    temp_conversations[temp_user_id] = entries[-10:]
    _temp_conversation_touched[temp_user_id] = now
    _prune_temp_conversations(now)

    log_service.conversation(f"Saved temp conversation for guest: {temp_user_id}")

async def get_conversation_history(
        user_id: Optional[int] = None,
        temp_user_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
        format_type: str = 'text',
        limit: int = 10
) -> List[Dict] | str:
    if user_id and db:
        log_service.conversation(f"Retrieving DB history for User ID: {user_id}")

        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.timestamp.desc())
            .limit(limit)
        )
        last_conversations = result.scalars().all()

        json_history = []
        for conversation in reversed(last_conversations):
            message_type = getattr(conversation, 'message_type', 'interactive')
            if conversation.user_input is not None:
                json_history.append({"type": "user", "content": conversation.user_input, "messageType": message_type})
            if conversation.bot_response is not None:
                json_history.append({"type": "bot", "content": conversation.bot_response, "messageType": message_type})
            if conversation.commands is not None:
                json_history.append({"type": "command", "content": conversation.commands, "messageType": message_type})
            if conversation.warning_message is not None:
                json_history.append(
                    {"type": "warning", "content": conversation.warning_message, "messageType": message_type})
            if conversation.error_message is not None:
                json_history.append(
                    {"type": "error", "content": conversation.error_message, "messageType": message_type})
            if conversation.info_message is not None:
                json_history.append({"type": "info", "content": conversation.info_message, "messageType": message_type})

        if format_type == 'json':
            return json_history

        text_history = []
        for item in json_history:
            if item['type'] == 'user':
                text_history.append(f"[LISTENER TXT] {item['content']}")
            elif item['type'] == 'bot':
                text_history.append(item['content'])
            elif item['type'] == 'command':
                text_history.append(item['content'])
            else:
                text_history.append(f"[{item['type'].upper()}] {item['content']}")

        text_history = deduplicate_conversation_history(text_history)

        return "\n".join(text_history)

    elif temp_user_id:
        log_service.conversation(f"Retrieving in-memory history for Temp User ID: {temp_user_id}")

        if temp_user_id not in temp_conversations:
            return [] if format_type == 'json' else ""

        recent_conversations = temp_conversations[temp_user_id][-3:]

        if format_type == 'json':
            json_history = []
            for convo in recent_conversations:
                parts = convo.split('\n', 1)
                if len(parts) == 2:
                    user_part = parts[0].replace('[LISTENER TXT] ', '')
                    bot_part = parts[1]
                    json_history.append({"type": "user", "content": user_part})
                    json_history.append({"type": "bot", "content": bot_part})
            return json_history

        recent_conversations = deduplicate_conversation_history(recent_conversations)

        return "\n".join(recent_conversations)

    else:
        return [] if format_type == 'json' else "No user identifier"

def _quote(text: str, limit: int = 160) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."




class ConversationService:
    def __init__(self):
        self.dj_prompt_service = None
        self.tts_queue_manager = None
        self.command_executor = None
        self.user_content_service = None
        self.whisper_service = None
        self.ai_service = None
        self.broadcast_func: Optional[Callable] = None
        self.broadcast_all_func: Optional[Callable] = None
        self.fillers = None
        self._active_turns: Dict[str, Set[asyncio.Task]] = {}
        self._turn_started_at: Dict[str, float] = {}

    def initialize(self,
                   dj_prompt_service,
                   tts_queue_manager,
                   command_executor,
                   user_content_service,
                   whisper_service,
                   ai_service=None,
                   broadcast_func: Optional[Callable] = None,
                   broadcast_all_func: Optional[Callable] = None,
                   fillers=None):

        self.dj_prompt_service = dj_prompt_service
        self.tts_queue_manager = tts_queue_manager
        self.command_executor = command_executor
        self.user_content_service = user_content_service
        self.whisper_service = whisper_service
        self.ai_service = ai_service
        self.broadcast_func = broadcast_func
        self.broadcast_all_func = broadcast_all_func
        self.fillers = fillers
        log_service.success("✓ Conversation Orchestrator Service initialized")

    async def _safe_bg_task(self, coro, name="conversation_task"):
        try:
            await coro
        except Exception as e:
            log_service.error(f"{name} failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

    async def _interrupt_session_speech(self, session_id: str, user_id: Optional[int]):
        if self.tts_queue_manager is None:
            return
        try:
            await self.tts_queue_manager.cancel_session(session_id, user_id)
        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: TTS interrupt failed: {e}")

    def last_turn_at(self, session_id: Optional[str]) -> float:
        return self._turn_started_at.get(session_id or "", 0.0)

    def turn_in_progress(self, session_id: Optional[str]) -> bool:
        return any(not task.done() for task in self._active_turns.get(session_id or "", ()))

    async def _begin_turn(self, session_id: str, session_dict: Dict, origin: str) -> Set[asyncio.Task]:
        now = time.time()
        self._turn_started_at = {key: at for key, at in self._turn_started_at.items() if now - at < 3600}
        self._turn_started_at[session_id] = now
        previous = self._active_turns.pop(session_id, None)
        self._active_turns = {key: tasks for key, tasks in self._active_turns.items() if tasks}
        turn_tasks: Set[asyncio.Task] = set()
        self._active_turns[session_id] = turn_tasks
        session_dict['_turn_tasks'] = turn_tasks
        session_dict['origin'] = origin

        pending = [task for task in (previous or ()) if not task.done() and task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=settings.DJ_TURN_CANCEL_TIMEOUT_S)
            log_service.listener(
                f"{log_service.who(session_id)}: new turn replaced the previous one ({len(pending)} task(s) cancelled)")
        return turn_tasks

    @staticmethod
    def _track_turn_task(turn_tasks: Set[asyncio.Task], task: asyncio.Task):
        turn_tasks.add(task)
        task.add_done_callback(turn_tasks.discard)

    async def handle_text_interaction(self, text: str, session_id: str, user: Optional[User], is_guest: bool = False):
        user_id = user.id if user else None
        session_dict = {"session_id": session_id, "user_id": user_id}
        usage_tracking.bind_session(session_id, user_id)

        log_service.listener(f"{log_service.who(session_id)}: typed to the DJ: \"{_quote(text)}\"")
        turn_tasks = await self._begin_turn(session_id, session_dict, "text")
        await self._interrupt_session_speech(session_id, user_id)
        await self._play_impulse(text, user_id, session_id, is_guest, session_dict)

        self._track_turn_task(turn_tasks, spawn(self._safe_bg_task(
            self._process_gpt_and_orchestrate(text, user_id, session_id, is_guest, session_dict, origin="text"),
            "process_text_interaction"
        ), name="process_text_interaction"))
        return {"status": "processing", "input": text}

    async def handle_audio_interaction(self, audio_bytes: bytes, session_id: str, user: Optional[User],
                                       is_guest: bool = False):
        user_id = user.id if user else None
        session_dict = {"session_id": session_id, "user_id": user_id}
        storage_id = user_id or session_id
        usage_tracking.bind_session(session_id, user_id)

        turn_tasks = await self._begin_turn(session_id, session_dict, "voice")
        await self._interrupt_session_speech(session_id, user_id)

        timestamp = str(int(time.time()))

        if self.user_content_service is None:
            raise RuntimeError("user_content_service not initialized")
        if self.whisper_service is None:
            raise RuntimeError("whisper_service not initialized")

        save_task = asyncio.create_task(self.user_content_service.save_audio_file(storage_id, timestamp, audio_bytes))

        fast_transcription_task = asyncio.create_task(self.whisper_service.transcribe_fast(audio_bytes))

        self._track_turn_task(turn_tasks, spawn(self._safe_bg_task(
            self._process_audio_full_flow_parallel(audio_bytes, save_task, session_id, user_id, session_dict, is_guest,
                                                   user),
            "process_audio_full_flow"
        ), name="process_audio_full_flow"))

        fast_transcription = await fast_transcription_task

        if not fast_transcription:
            raise ValueError("Transcription failed")

        log_service.listener(f"{log_service.who(session_id)}: said to the DJ: \"{_quote(fast_transcription)}\"")

        if self.broadcast_func:
            await self.broadcast_func(session_id, {
                "type": "transcription_complete",
                "data": {"text": fast_transcription}
            })

        await self._play_impulse(fast_transcription, user_id, session_id, is_guest, session_dict)
        return fast_transcription

    async def _process_audio_full_flow_parallel(self, audio_bytes, save_task, session_id, user_id, session_dict,
                                                is_guest, user_obj):
        if self.whisper_service is None:
            raise RuntimeError("whisper_service not initialized")
        try:
            result = await self.whisper_service.transcribe_quality(audio_bytes)

            if not result:
                return

            transcription = result["text"]
            log_service.detail(f"{log_service.who(session_id)}: quality transcription: {transcription}", "listener")

            words = transcription.split()
            if len(words) == 1 and len(words[0]) < 5:
                return

            webm_path = await asyncio.shield(save_task)
            if not webm_path:
                log_service.error(f"{log_service.who(session_id)}: failed to save voice recording, skipping metadata")
                return

            timestamp = webm_path.stem

            async with AsyncSessionLocal() as _db:
                user = user_obj

                metadata = {
                    "full_transcription": transcription,
                    "word_level_transcription": result.get("words", []),
                    "transcription_metadata": {
                        "language": result.get("language", "en"),
                        "language_probability": result.get("language_probability", 1.0),
                        "duration": result.get("duration", 0)
                    },
                    "user_data": {
                        "user_id": user_id or session_id,
                        "username": user.username if user else "Guest",
                        "location": user.location if user and hasattr(user, 'location') else "Unknown",
                        "latitude": float(user.latitude) if user and hasattr(user,
                                                                             'latitude') and user.latitude else None,
                        "longitude": float(user.longitude) if user and hasattr(user,
                                                                               'longitude') and user.longitude else None,
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await asyncio.shield(self.user_content_service.save_metadata_file(user_id or session_id, timestamp, metadata))  # type: ignore
                session_dict['recording'] = str(webm_path)

            await self._process_gpt_and_orchestrate(transcription, user_id, session_id, is_guest, session_dict,
                                                    origin="voice")

        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: voice turn failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

    async def _persist_conversation_turn(self, user_id, transcription, full_response, commands_for_display):
        async with AsyncSessionLocal() as db:
            await save_conversation_to_database(
                user_id=user_id,
                db=db,
                user_input=transcription,
                bot_response=full_response,
                commands=commands_for_display,
                message_type='interactive'
            )

        from services_radio.persona_service import update_user_persona_if_needed
        try:
            async with AsyncSessionLocal() as db:
                await update_user_persona_if_needed(user_id, db, self.ai_service)
        except Exception:
            pass

    async def _speak_dj_text(self, text, user_id, session_id, is_guest):
        broadcast = spoken_text(text)
        if broadcast:
            if self.tts_queue_manager is None:
                raise RuntimeError("tts_queue_manager not initialized")
            await self.tts_queue_manager.add_tts_request(
                text=broadcast,
                user_id=user_id or 0,
                tts_type="interactive",
                is_broadcast=True,
                is_temp_user=is_guest,
                session_id=session_id
            )

    async def _publish_turn(self, transcription, full_response, commands_for_display, user_id, session_id, is_guest,
                            turn_id=None):
        if is_guest:
            save_temp_conversation(session_id, transcription, full_response)

        if self.broadcast_func:
            await self.broadcast_func(session_id, {
                "type": "conversation_update",
                "data": {
                    "user_input": transcription,
                    "bot_response": full_response,
                    "commands": commands_for_display,
                    "message_type": "interactive",
                    "turn_id": turn_id
                }
            })

        if not is_guest:
            spawn(self._safe_bg_task(
                self._persist_conversation_turn(user_id, transcription, full_response, commands_for_display),
                "persist_conversation_turn"
            ), name=f"persist_conversation_turn:{session_id}")

    async def _filler_context(self, current: str, user_id, session_id, is_guest) -> str:
        if is_guest or not user_id:
            history = await get_conversation_history(temp_user_id=session_id, format_type='json')
        else:
            async with AsyncSessionLocal() as db:
                history = await get_conversation_history(user_id=user_id, db=db, format_type='json',
                                                         limit=FILLER_CONTEXT_TURNS + 1)
        pairs = []
        for entry in history or []:
            if entry.get('type') == 'user':
                pairs.append([entry.get('content') or '', ''])
            elif entry.get('type') == 'bot' and pairs:
                pairs[-1][1] = plain_talk(entry.get('content') or '')
        return conversation_context(current, [tuple(pair) for pair in pairs], FILLER_CONTEXT_TURNS,
                                    FILLER_CONTEXT_CHARS)

    async def _play_impulse(self, text, user_id, session_id, is_guest, session_dict):
        if self.fillers is None:
            return
        try:
            context = await self._filler_context(text, user_id, session_id, is_guest)
            session_dict['filler_context'] = context
            script = await self.fillers.pick(IMPULSE, context, session_id)
            if script is not None:
                await self.fillers.play(IMPULSE, script, user_id, session_id, is_guest, session_dict)
        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: impulse failed: {e}")

    async def _process_tool_turn(self, transcription, user_id, session_id, is_guest, session_dict, origin):
        from services_radio.dj_tools import DJToolRuntime, DJTurnContext, tool_activity

        if self.dj_prompt_service is None:
            raise RuntimeError("dj_prompt_service not initialized")

        ctx = DJTurnContext(session_dict=session_dict, transcription=transcription, origin=origin)
        if self.broadcast_func:
            broadcast = self.broadcast_func

            async def notify(data):
                await broadcast(session_id, {"type": "dj_activity", "data": data})
            ctx.notify = notify
        runtime = DJToolRuntime(self.command_executor, ctx)
        spoken = []
        on_air = session_dict.setdefault('on_air', [])
        trace = {"route": ""}

        await ctx.activity("turn", input=transcription, origin=origin)
        for aired in list(on_air):
            await ctx.activity("say", text=aired)
        session_dict['turn_ctx'] = ctx
        if self.fillers is not None:
            context = session_dict.get('filler_context') or \
                await self._filler_context(transcription, user_id, session_id, is_guest)
            self.fillers.begin_wait(session_id, user_id, is_guest, session_dict, context)

        async def speak_preamble(text, calls=()):
            if text:
                await self._speak_dj_text(text, user_id, session_id, is_guest)
                spoken.append(text)
                on_air.append(text)
                await ctx.activity("say", text=text)
                return
            if self.fillers is not None:
                self.fillers.note_activity(session_id, tool_activity(calls))

        async def announce_route(route):
            steps = [str(step).split("(", 1)[0].strip() for step in route.get("tool_plan") or []]
            steps = list(dict.fromkeys(step.split()[-1] for step in steps if step))
            ctx.planned = set(steps)
            found = route.get("pulse_found") or {}
            notes = ", ".join(f"{count} {kind}" for kind, count in found.items())
            source = route.get("source") or ""
            label = ("Plan: " + " > ".join(steps)) if steps else "Plan: just talk"
            summary = " · ".join(filter(None, [f"station notes: {notes}" if notes else "", source]))
            trace["route"] = f"{label} [{summary}]"
            detail = [f"{i}. {step}" for i, step in enumerate(route.get("tool_plan") or [], 1)]
            pulse = route.get("pulse") or {}
            if pulse.get("kinds"):
                detail.append("Station notes: " + ", ".join(pulse["kinds"]) +
                              (f" on '{pulse['topic']}'" if pulse.get("topic") else "") +
                              (f", {pulse['when']}" if pulse.get("when") else "") +
                              (", near the listener" if pulse.get("near_me") else ""))
            context = [node for node in route.get("context_nodes") or [] if not node.startswith("format_")]
            if context:
                detail.append("Context: " + ", ".join(context))
            await ctx.activity("start", call_id=f"{ctx.turn_id}:route", tool="producer", source="producer",
                               label=label, command="\n".join(detail))
            await ctx.activity("result", call_id=f"{ctx.turn_id}:route", tool="producer", source="producer",
                               outcome="done", summary=summary[:140])

        try:
            result = await self.dj_prompt_service.gpt_dj_interactive_tools(transcription, session_dict, runtime,
                                                                           speak_preamble, announce_route)
            if result and result["status"] == "na":
                log_service.detail(f"{log_service.who(session_id)}: DJ response not applicable", "listener")
                return

            main_response = (result or {}).get("main") or ""
            notes = (result or {}).get("notes") or ""

            if not main_response and not spoken:
                log_service.error(f"{log_service.who(session_id)}: DJ turn produced no reply - airing a fallback line")
                fallback = random.choice(FAILED_TURN_LINES)
                await self._speak_dj_text(fallback, user_id, session_id, is_guest)
                on_air.append(fallback)
                await ctx.activity("say", text=fallback)
                return

            if main_response and main_response not in spoken:
                await self._speak_dj_text(main_response, user_id, session_id, is_guest)
                on_air.append(main_response)
                await ctx.activity("say", text=main_response)
            if notes:
                await ctx.activity("say", text=notes)

            full_main = "\n".join(on_air)
            full_response = full_main + "\n" + notes if notes else full_main

            commands_for_display = runtime.commands_for_display()
            log_service.commands(
                f"{log_service.who(session_id)}: DJ turn | {trace['route']}"
                f" | tools: {runtime.summary() or 'none'} | {(result or {}).get('rounds') or 0} round(s)"
                f" | said {sum(talk_clock.spoken_words(part) for part in [*spoken, main_response or ''])} words"
                f" | [TASK] {notes.rsplit('[TASK]', 1)[1].strip()[:160] if '[TASK]' in notes else 'none'}")
            write_turn_trace({
                "at": datetime.now(timezone.utc).isoformat(), "turn_id": ctx.turn_id,
                "who": log_service.who(session_id), "origin": origin, "listener": transcription,
                "plan": trace["route"], "user_message": (result or {}).get("user_message"),
                "rounds": [{**round_, "results": [{"name": r["name"], "result": json.dumps(r["result"], default=str)[:2000]}
                                                  for r in round_.get("results", [])]}
                           for round_ in (result or {}).get("trace") or []],
                "spoken_preambles": spoken, "main": main_response, "notes": notes,
            })

            await asyncio.shield(self._publish_turn(transcription, full_response, commands_for_display,
                                                    user_id, session_id, is_guest, ctx.turn_id))
        finally:
            if self.fillers is not None:
                self.fillers.end_wait(session_id, session_dict)
            session_dict.pop('turn_ctx', None)
            ctx.gate.set()
            if ctx.activity_sent:
                await ctx.activity("done")

    async def _process_gpt_and_orchestrate(self, transcription, user_id, session_id, is_guest, session_dict,
                                           origin="text"):
        try:
            log_service.detail(f"{log_service.who(session_id)}: orchestrating DJ response", "listener")

            if self.dj_prompt_service is None:
                raise RuntimeError("dj_prompt_service not initialized")

            await self._process_tool_turn(transcription, user_id, session_id, is_guest, session_dict, origin)

        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: DJ turn failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

conversation_service = ConversationService()
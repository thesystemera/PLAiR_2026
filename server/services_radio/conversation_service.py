import asyncio
import re
from typing import Optional, List, Dict, Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
from database import AsyncSessionLocal
from database.models import Conversation, User
from services import log_service

temp_conversations = {}

def deduplicate_conversation_history(text_history: List[str]) -> List[str]:
    if not text_history:
        return text_history

    deduplicated = []
    last_bot_response = None

    for _, entry in enumerate(text_history):
        if not entry.startswith('['):
            cleaned_entry = entry.replace('[BROADCAST]', '').replace('[TXT]', '').replace('[SHAQUILLE]', '').replace(
                '[TERRY]', '').strip()

            if cleaned_entry == last_bot_response:
                log_service.system(f"[Dedup] Skipping duplicate bot response: {cleaned_entry[:50]}...")
                continue

            last_bot_response = cleaned_entry

        deduplicated.append(entry)

    removed_count = len(text_history) - len(deduplicated)
    if removed_count > 0:
        log_service.system(f"[Dedup] Removed {removed_count} duplicate responses from conversation history")

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

def save_temp_conversation(temp_user_id: str, transcription: str, response: str):
    if temp_user_id not in temp_conversations:
        temp_conversations[temp_user_id] = []

    conversation_entry = f"[LISTENER TXT] {transcription}\n{response}"
    temp_conversations[temp_user_id].append(conversation_entry)

    if len(temp_conversations[temp_user_id]) > 10:
        temp_conversations[temp_user_id] = temp_conversations[temp_user_id][-10:]

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

    def initialize(self,
                   dj_prompt_service,
                   tts_queue_manager,
                   command_executor,
                   user_content_service,
                   whisper_service,
                   ai_service=None,
                   broadcast_func: Optional[Callable] = None,
                   broadcast_all_func: Optional[Callable] = None):

        self.dj_prompt_service = dj_prompt_service
        self.tts_queue_manager = tts_queue_manager
        self.command_executor = command_executor
        self.user_content_service = user_content_service
        self.whisper_service = whisper_service
        self.ai_service = ai_service
        self.broadcast_func = broadcast_func
        self.broadcast_all_func = broadcast_all_func
        log_service.success("✓ Conversation Orchestrator Service initialized")

    async def _safe_bg_task(self, coro, name="conversation_task"):
        try:
            await coro
        except Exception as e:
            log_service.error(f"{name} failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

    async def handle_text_interaction(self, text: str, session_id: str, user: Optional[User], is_guest: bool = False):
        user_id = user.id if user else None
        session_dict = {"session_id": session_id, "user_id": user_id}

        asyncio.create_task(self._safe_bg_task(
            self._process_gpt_and_orchestrate(text, user_id, session_id, is_guest, session_dict),
            "process_text_interaction"
        ))
        return {"status": "processing", "input": text}

    async def handle_audio_interaction(self, audio_bytes: bytes, session_id: str, user: Optional[User],
                                       is_guest: bool = False):
        user_id = user.id if user else None
        session_dict = {"session_id": session_id, "user_id": user_id}
        storage_id = user_id or session_id

        import time
        timestamp = str(int(time.time()))

        if self.user_content_service is None:
            raise RuntimeError("user_content_service not initialized")
        if self.whisper_service is None:
            raise RuntimeError("whisper_service not initialized")

        save_task = asyncio.create_task(self.user_content_service.save_audio_file(storage_id, timestamp, audio_bytes))

        fast_transcription_task = asyncio.create_task(self.whisper_service.transcribe_fast(audio_bytes))

        asyncio.create_task(self._safe_bg_task(
            self._process_audio_full_flow_parallel(audio_bytes, save_task, session_id, user_id, session_dict, is_guest,
                                                   user),
            "process_audio_full_flow"
        ))

        fast_transcription = await fast_transcription_task

        if not fast_transcription:
            raise ValueError("Transcription failed")

        log_service.api(f"[{session_id}] Fast transcription: {fast_transcription}")

        if self.broadcast_func:
            await self.broadcast_func(session_id, {
                "type": "transcription_complete",
                "data": {"text": fast_transcription}
            })

        impulse_text = f"[IMPULSE]{fast_transcription}[/IMPULSE]"

        if self.tts_queue_manager is None:
            raise RuntimeError("tts_queue_manager not initialized")

        await self.tts_queue_manager.add_tts_request(
            text=impulse_text,
            user_id=user_id or 0,
            tts_type="interactive",
            is_broadcast=True,
            is_temp_user=is_guest,
            session_id=session_id
        )

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
            log_service.api(f"[{session_id}] Quality transcription: {transcription}")

            words = transcription.split()
            if len(words) == 1 and len(words[0]) < 5:
                return

            webm_path = await save_task
            if not webm_path:
                log_service.error(f"[{session_id}] Failed to save audio file, skipping metadata")
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
                await self.user_content_service.save_metadata_file(user_id or session_id, timestamp, metadata)  # type: ignore

            await self._process_gpt_and_orchestrate(transcription, user_id, session_id, is_guest, session_dict)

        except Exception as e:
            log_service.error(f"[{session_id}] Audio flow failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

    async def _process_gpt_and_orchestrate(self, transcription, user_id, session_id, is_guest, session_dict):
        try:
            log_service.system(f"[{session_id}] Orchestrating DJ Response...")

            if self.dj_prompt_service is None:
                raise RuntimeError("dj_prompt_service not initialized")
            result = await self.dj_prompt_service.gpt_dj_interactive(transcription, session_dict)
            if not result:
                log_service.error(f"[{session_id}] GPT response failed")
                return

            main_response, notes = result

            sections = re.split(r'(\[BROADCAST]|\[TXT])', main_response)
            broadcast_content = []
            txt_content = []
            current_mode = None

            for section in sections:
                if section == '[BROADCAST]':
                    current_mode = 'BROADCAST'
                elif section == '[TXT]':
                    current_mode = 'TXT'
                elif section.strip():
                    if current_mode == 'BROADCAST':
                        broadcast_content.append(section.strip())
                    elif current_mode == 'TXT':
                        txt_content.append(section.strip())

            if broadcast_content:
                broadcast_package = " ".join(broadcast_content)
                if self.tts_queue_manager is None:
                    raise RuntimeError("tts_queue_manager not initialized")
                await self.tts_queue_manager.add_tts_request(
                    text=broadcast_package,
                    user_id=user_id or 0,
                    tts_type="interactive",
                    is_broadcast=True,
                    is_temp_user=is_guest,
                    session_id=session_id
                )

            full_response = main_response + "\n" + notes if notes else main_response

            if self.dj_prompt_service is None:
                raise RuntimeError("dj_prompt_service not initialized")
            commands = await self.dj_prompt_service.gpt_command_extraction(full_response, transcription, session_dict)
            commands_for_display = None

            if commands and commands.strip() and commands.strip() != "{N/A}":
                log_service.commands(f"[HAL11000 PIPELINE] Formatted Commands:\n{commands}")
                if self.command_executor:
                    log_service.commands(f"[HAL11000 PIPELINE] Executing commands for session {session_id}...")
                    await self.command_executor.process_commands(commands, session_dict)
                    log_service.commands("[HAL11000 PIPELINE] Command execution completed")

                if commands.strip().startswith('[HAL11000]'):
                    commands_for_display = commands
                else:
                    commands_for_display = f"[HAL11000]{commands}"
            else:
                log_service.commands("[HAL11000 PIPELINE] No commands extracted")

            if not is_guest:
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
            else:
                save_temp_conversation(session_id, transcription, full_response)

            if self.broadcast_func:
                await self.broadcast_func(session_id, {
                    "type": "conversation_update",
                    "data": {
                        "user_input": transcription,
                        "bot_response": full_response,
                        "commands": commands_for_display,
                        "message_type": "interactive"
                    }
                })

        except Exception as e:
            log_service.error(f"[{session_id}] GPT processing failed: {e}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")

conversation_service = ConversationService()
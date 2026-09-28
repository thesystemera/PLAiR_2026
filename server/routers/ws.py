import asyncio
import json
import re
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services import log_service
from services import auth_service
from security_middleware import is_valid_guest_id
from database import get_db
from service_registry import services
from services.task_utils import spawn
from services import usage_tracking
from services_radio.dj_content_bank import content_bank
from services_radio import listener_location as location_resolver
from config.settings import settings

router = APIRouter()

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TALK_BREAK_MESSAGES = {"talk_break_ready", "talk_break_failed", "talk_break_start", "talk_break_end"}
ACTIVE_DEVICE_ONLY_MESSAGES = {"track_transition", "playback_heartbeat"} | TALK_BREAK_MESSAGES
TRANSPORT_COMMANDS = {"next", "previous", "seek"}
ORDERED_COMMAND_MESSAGES = {"playback_command", "track_transition"}
MAX_INFLIGHT_COMMANDS = 32

_announcer_pending: Dict[str, dict] = {}
_announcer_workers: Dict[str, asyncio.Task] = {}


async def _announcer_worker(session_id: str):
    usage_tracking.bind_session(session_id)
    try:
        while session_id in _announcer_pending:
            state = _announcer_pending.pop(session_id)
            if services.announcer_service:
                await services.announcer_service.on_playback_state_update(session_id, state)  # type: ignore
    finally:
        if _announcer_workers.get(session_id) is asyncio.current_task():
            del _announcer_workers[session_id]


def _queue_announcer_update(session_id: str, state: dict):
    _announcer_pending[session_id] = state
    worker = _announcer_workers.get(session_id)
    if worker is None or worker.done():
        _announcer_workers[session_id] = spawn(_announcer_worker(session_id), name=f"announcer_update_{session_id}")


async def _warm_area(latitude: float, longitude: float) -> None:
    from services_radio import area_geocode
    try:
        await area_geocode.describe(latitude, longitude, 0)
    except Exception as e:
        log_service.warning(f"[WS] Area warm-up failed: {type(e).__name__}: {e}")


def update_guest_location(session_id: str, message_data: dict) -> str:
    store = location_resolver.guest_locations
    if message_data.get('clear') is True:
        store.forget(session_id)
        return 'cleared'
    status = store.update(session_id, message_data.get('latitude'), message_data.get('longitude'),
                          message_data.get('accuracy_m'), message_data.get('timezone'))
    entry = store.get(session_id)
    if status == location_resolver.ACCEPTED and entry is not None:
        if entry.timezone and settings.DJ_GUEST_TIMEZONE_ENABLED:
            content_bank.set_session_timezone(session_id, entry.timezone)
        spawn(_warm_area(entry.latitude, entry.longitude), name="guest_location_area_warm")
    if status not in (location_resolver.ACCEPTED, location_resolver.UNCHANGED):
        log_service.info(f"[WS] Guest location update for {session_id[:14]} {status}")
    return status


def _message_seq(message_data: dict) -> Optional[int]:
    seq = message_data.get('seq')
    if isinstance(seq, int) and not isinstance(seq, bool) and 0 <= seq < 2 ** 53:
        return seq
    return None


async def handle_playback_message(session_id: str, device_id: str, user_id: Optional[int],
                                  message_type: Optional[str], message_data: dict, session_callback):
    assert services.playback_service is not None
    playback_state = services.playback_service.get_session_state(session_id)
    seq = _message_seq(message_data)

    if message_type in ACTIVE_DEVICE_ONLY_MESSAGES and playback_state.active_device_id \
            and playback_state.active_device_id != device_id:
        if message_type == 'track_transition':
            log_service.playback(f"[WS] Ignoring track_transition from inactive device {device_id[:8]}")
            await playback_state.acknowledge_command(device_id, seq, session_callback)
        return

    if message_type == 'playback_command':
        command = message_data.get('command')
        radio = services.radio_mode_service
        if radio is not None:
            if command in TRANSPORT_COMMANDS or (command == 'play' and message_data.get('track_id')):
                await radio.on_user_transport(session_id, command)
            elif command == 'transfer':
                await radio.on_transfer(session_id, message_data.get('device_id') or device_id)
            elif command == 'play' and message_data.get('claim'):
                await radio.on_transfer(session_id, device_id)

        if command == 'play':
            track_id = message_data.get('track_id')
            claim = bool(message_data.get('claim'))
            log_service.playback(f"[WS] Play: {track_id[:8] if track_id else 'current'}{' (claim)' if claim else ''}")
            await playback_state.play(track_id=track_id, user_id=user_id, notify_callback=session_callback,
                                      device_id=device_id, seq=seq, claim=claim)

        elif command == 'pause':
            log_service.playback("[WS] Pause")
            await playback_state.pause(notify_callback=session_callback, device_id=device_id, seq=seq)

        elif command == 'seek':
            position_ms = message_data.get('position_ms', 0)
            log_service.playback(f"[WS] Seek: {position_ms}ms")
            await playback_state.seek(position_ms, notify_callback=session_callback, device_id=device_id, seq=seq)

        elif command == 'next':
            skip_reason = message_data.get('skip_reason', 'user_skip')
            log_service.playback(f"[WS] Next: {skip_reason}")
            await playback_state.next(user_id=user_id, notify_callback=session_callback,
                                      skip_reason=skip_reason, device_id=device_id, seq=seq)

        elif command == 'previous':
            log_service.playback("[WS] Previous")
            await playback_state.previous(user_id=user_id, notify_callback=session_callback,
                                          device_id=device_id, seq=seq)

        elif command == 'claim':
            verdict = playback_state.open_claim_verdict(device_id)
            if verdict == 'claim' and radio is not None:
                await radio.on_transfer(session_id, device_id)
            verdict = await playback_state.claim_on_open(device_id, notify_callback=session_callback, seq=seq)
            log_service.playback(f"[WS] Claim on open from {device_id[:8]}: {verdict}")

        elif command == 'transfer':
            target_device_id = message_data.get('device_id') or device_id
            if not isinstance(target_device_id, str) or not DEVICE_ID_PATTERN.match(target_device_id):
                await playback_state.acknowledge_command(device_id, seq, session_callback)
                return
            if target_device_id != device_id and not playback_state.is_device_online(target_device_id):
                log_service.warning(f"[WS] Transfer to offline device {target_device_id[:8]} refused")
                await playback_state.acknowledge_command(device_id, seq, session_callback)
                return
            play = message_data.get('play')
            await playback_state.transfer(target_device_id, play=play if isinstance(play, bool) else None,
                                          notify_callback=session_callback, requester_device_id=device_id, seq=seq)

        else:
            log_service.warning(f"[WS] Unknown command: {command}")
            await playback_state.acknowledge_command(device_id, seq)

    elif message_type == 'track_transition':
        from_track_id = message_data.get('from_track_id')
        to_track_id = message_data.get('to_track_id')
        transition_type = message_data.get('transition_type') or 'crossfade'
        crossfade_info = message_data.get('crossfade_info')

        await playback_state.handle_track_transition(
            from_track_id=from_track_id,
            to_track_id=to_track_id,
            transition_type=transition_type,
            user_id=user_id,
            notify_callback=session_callback,
            crossfade_info=crossfade_info,
            device_id=device_id,
            seq=seq
        )

    elif message_type in TALK_BREAK_MESSAGES:
        if services.radio_mode_service is not None:
            await services.radio_mode_service.handle_client_event(session_id, device_id, message_type, message_data)

    elif message_type == 'listener_location':
        if user_id is None:
            update_guest_location(session_id, message_data)

    elif message_type == 'radio_mode_prefs':
        if user_id is None and services.radio_mode_service is not None:
            await services.radio_mode_service.set_guest_prefs(session_id, message_data)

    elif message_type == 'playback_heartbeat':
        await playback_state.handle_playback_heartbeat(
            track_id=message_data.get('track_id'),
            actual_position_ms=message_data.get('actual_position_ms', 0),
            is_playing=message_data.get('is_playing', False),
            buffered_ahead_ms=message_data.get('buffered_ahead_ms', 0),
            timestamp=message_data.get('timestamp'),
            device_id=device_id
        )


async def _handle_message_safely(**kwargs):
    try:
        await handle_playback_message(**kwargs)
    except Exception as e:
        log_service.error(f"Error handling WebSocket message: {str(e)}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")


@router.websocket("/ws/playback")
async def websocket_endpoint(
        websocket: WebSocket,
        token: Optional[str] = None,
        guest_id: Optional[str] = None,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        device_type: Optional[str] = None,
        tz: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
):
    assert services.websocket_service is not None
    assert services.playback_service is not None
    await websocket.accept()  # noqa: F541

    user = None
    if token:
        payload = auth_service.decode_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                user = await auth_service.get_user_by_id(db, int(user_id))
    token_rejected = bool(token) and user is None

    if not user and not (guest_id and is_valid_guest_id(guest_id)):
        await websocket.close(code=4401 if token_rejected else 1008)
        return

    if device_id and not DEVICE_ID_PATTERN.match(device_id):
        await websocket.close(code=1008)
        return

    session_id = str(int(user.id)) if user else guest_id
    session_device_id = device_id or "unknown"
    session_device_name = (device_name or "Unknown Device")[:100]
    session_device_type = (device_type or "desktop")[:32]
    session_user_id = int(user.id) if user else None

    if not session_id:
        log_service.error("WebSocket connection rejected: no session_id")
        await websocket.close(code=1008, reason="No session ID provided")
        return

    if token_rejected:
        log_service.api(f"[WS] Token rejected for device {session_device_id[:8]}; connected as guest")

    services.websocket_service.register_connection(session_id, session_device_id, websocket)
    if tz and settings.DJ_GUEST_TIMEZONE_ENABLED:
        content_bank.set_session_timezone(session_id, tz)
    if services.radio_mode_service is not None:
        await services.radio_mode_service.on_connect(session_id, session_user_id)

    def _make_session_callback():
        async def _session_broadcast(new_state):
            await services.websocket_service.broadcast_playback_state(session_id, new_state)  # type: ignore
            if services.announcer_service:
                _queue_announcer_update(session_id, new_state)
        return _session_broadcast

    session_callback = None
    inflight_commands = set()

    try:
        await websocket.send_json({"type": "session_info", "data": {
            "authenticated": user is not None,
            "user_id": session_user_id,
            "token_rejected": token_rejected,
        }})

        existing_state = services.playback_service.get_state(session_id) if services.playback_service.has_session(session_id) else None  # type: ignore
        has_existing_playback = bool(existing_state and existing_state.get('current_track') is not None)

        if user:
            assert services.device_management_service is not None
            await services.device_management_service.register_or_update_device(
                db=db,
                user_id=int(user.id),
                device_id=session_device_id,
                device_name=session_device_name,
                device_type=session_device_type,
                set_active=False
            )

        if not has_existing_playback:
            await services.playback_service.initialize_new_session(session_id, user_id=session_user_id)
        else:
            log_service.info(f"Preserving existing playback state for {session_id}")

        session_callback = services.playback_service.ensure_broadcast_callback(session_id, _make_session_callback)  # type: ignore

        playback_state = services.playback_service.get_session_state(session_id)
        activated = playback_state.device_connected(session_device_id)
        if activated:
            log_service.api(f"Activated first device for {session_id}: {session_device_id}")
        elif playback_state.active_device_id == session_device_id:
            log_service.api(f"Active device reconnected: {session_device_id}")
        else:
            log_service.api(f"Device connected as remote: {session_device_id} (active: {str(playback_state.active_device_id)[:8]})")

        state = playback_state.get_state()
        if activated or playback_state.active_device_id == session_device_id:
            await services.websocket_service.broadcast_playback_state(session_id, state)
        else:
            await websocket.send_json({"type": "playback_state", "data": state})

        while True:
            raw_data = await websocket.receive_text()

            services.websocket_service.update_activity(session_id, session_device_id)  # type: ignore

            try:
                message = json.loads(raw_data)
                if not isinstance(message, dict):
                    continue
                message_data = message.get('data') or {}
                if not isinstance(message_data, dict):
                    continue
                message_kwargs = dict(
                    session_id=session_id,
                    device_id=session_device_id,
                    user_id=session_user_id,
                    message_type=message.get('type'),
                    message_data=message_data,
                    session_callback=session_callback,
                )
                if message_kwargs["message_type"] in ORDERED_COMMAND_MESSAGES:
                    if len(inflight_commands) >= MAX_INFLIGHT_COMMANDS:
                        await asyncio.wait(inflight_commands, return_when=asyncio.FIRST_COMPLETED)
                    task = spawn(_handle_message_safely(**message_kwargs), name=f"ws_command_{session_id}")
                    inflight_commands.add(task)
                    task.add_done_callback(inflight_commands.discard)
                else:
                    await handle_playback_message(**message_kwargs)

            except json.JSONDecodeError:
                log_service.warning(f"Failed to parse WebSocket message from {session_id}/{session_device_id}")
            except Exception as e:
                log_service.error(f"Error handling WebSocket message: {str(e)}")
                import traceback
                log_service.error(f"Traceback: {traceback.format_exc()}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log_service.error(f"WebSocket error for {session_id}/{session_device_id}: {str(e)}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
    finally:
        services.websocket_service.unregister_connection(session_id, session_device_id, websocket)  # type: ignore
        device_still_connected = services.websocket_service.get_connection(session_id, session_device_id) is not None  # type: ignore
        if not device_still_connected and services.playback_service.has_session(session_id):  # type: ignore
            playback_state = services.playback_service.get_session_state(session_id)  # type: ignore
            was_active = playback_state.device_disconnected(session_device_id)
            if was_active and services.websocket_service.has_session(session_id):  # type: ignore
                log_service.api(f"Active device {session_device_id} disconnected; keeping it active (no auto-transfer)")
                try:
                    await services.websocket_service.broadcast_playback_state(session_id, playback_state.get_state())  # type: ignore
                except Exception as e:
                    log_service.warning(f"Broadcast after disconnect failed for {session_id}: {e}")
        if not services.websocket_service.has_session(session_id):  # type: ignore
            services.playback_service.release_broadcast_callback(session_id)  # type: ignore

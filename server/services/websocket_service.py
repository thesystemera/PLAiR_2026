from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from fastapi import WebSocket
from config import settings
from services import log_service
from services.task_utils import spawn

WS_SEND_TIMEOUT_S = 3.0

class WebSocketService:
    def __init__(self):
        self._connections: Dict[str, Dict[str, WebSocket]] = {}
        self._display_renderer = None

        self._last_activity: Dict[str, Dict[str, float]] = {}

        self._cleanup_task: Optional[asyncio.Task] = None

        self._stale_timeout: int = 1800

        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        self._initialized = True
        log_service.success("✓ WebSocket service initialized")

    async def start_background_tasks(self):
        self._cleanup_task = asyncio.create_task(self._cleanup_stale_connections())
        log_service.success("✓ WebSocket cleanup task started")

    async def stop_background_tasks(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        log_service.system("WebSocket cleanup task stopped")

    async def close_all_connections(self):
        for session_id in list(self._connections.keys()):
            for device_id, ws in list(self._connections[session_id].items()):
                try:
                    await ws.close()
                except Exception:
                    pass

        self._connections.clear()
        self._last_activity.clear()
        log_service.system("All WebSocket connections closed")

    def register_connection(self, session_id: str, device_id: str, websocket: WebSocket):
        if session_id not in self._connections:
            self._connections[session_id] = {}
        if session_id not in self._last_activity:
            self._last_activity[session_id] = {}

        session_connections = self._connections[session_id]
        if device_id not in session_connections:
            while len(session_connections) >= max(settings.WS_MAX_CONNECTIONS_PER_SESSION, 1):
                activity = self._last_activity.get(session_id, {})
                oldest_device = min(session_connections, key=lambda did: activity.get(did, 0.0))
                log_service.warning(
                    f"{log_service.who(session_id)}: WebSocket limit reached - evicting least active device "
                    f"{oldest_device[:8]}"
                )
                self._evict(session_id, oldest_device, session_connections[oldest_device])
                session_connections = self._connections.setdefault(session_id, {})

        self._last_activity.setdefault(session_id, {})
        session_connections[device_id] = websocket
        self._last_activity[session_id][device_id] = time.time()

        log_service.detail(f"WebSocket registered: {log_service.who(session_id, device_id)}", "system")

    def connection_totals(self) -> Tuple[int, int]:
        return sum(len(devices) for devices in self._connections.values()), len(self._connections)

    def unregister_connection(self, session_id: str, device_id: str, websocket: Optional[WebSocket] = None):
        current = self._connections.get(session_id, {}).get(device_id)
        if websocket is not None and current is not None and current is not websocket:
            log_service.detail(
                f"WebSocket closed for {log_service.who(session_id, device_id)} but a newer connection is registered",
                "system"
            )
            return

        if session_id in self._connections and device_id in self._connections[session_id]:
            del self._connections[session_id][device_id]
            if not self._connections[session_id]:
                del self._connections[session_id]

        if session_id in self._last_activity and device_id in self._last_activity[session_id]:
            del self._last_activity[session_id][device_id]
            if not self._last_activity[session_id]:
                del self._last_activity[session_id]

        total_connections, total_sessions = self.connection_totals()
        log_service.system(
            f"{log_service.who(session_id, device_id)} disconnected | "
            f"{total_connections} connections, {total_sessions} listeners online"
        )

    def update_activity(self, session_id: str, device_id: str):
        if session_id in self._last_activity and device_id in self._last_activity[session_id]:
            self._last_activity[session_id][device_id] = time.time()

    def get_connection(self, session_id: str, device_id: str) -> Optional[WebSocket]:
        return self._connections.get(session_id, {}).get(device_id)

    def session_for_device(self, device_id: str) -> Optional[str]:
        for session_id, devices in self._connections.items():
            if device_id in devices:
                return session_id
        return None

    def has_session(self, session_id: str) -> bool:
        return session_id in self._connections and len(self._connections[session_id]) > 0

    def get_online_device_ids(self, session_id: str) -> List[str]:
        return list(self._connections.get(session_id, {}).keys())

    @staticmethod
    def _serialize(message: dict) -> str:
        return json.dumps(message, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    async def _close_quietly(ws: WebSocket):
        try:
            await asyncio.wait_for(ws.close(), timeout=WS_SEND_TIMEOUT_S)
        except Exception:
            pass

    def close_session(self, session_id: str) -> int:
        connections = list(self._connections.get(session_id, {}).items())
        for device_id, ws in connections:
            self._evict(session_id, device_id, ws)
        return len(connections)

    def _evict(self, session_id: str, device_id: str, ws: WebSocket):
        current = self._connections.get(session_id, {}).get(device_id)
        if current is ws:
            del self._connections[session_id][device_id]
            if not self._connections[session_id]:
                del self._connections[session_id]
            if session_id in self._last_activity and device_id in self._last_activity[session_id]:
                del self._last_activity[session_id][device_id]
                if not self._last_activity[session_id]:
                    del self._last_activity[session_id]
        spawn(self._close_quietly(ws), name=f"ws_close:{session_id}/{device_id}")

    async def _send_many(self, targets: List[Tuple[str, str, WebSocket]], text: str, label: str):
        async def send_one(sess_id: str, dev_id: str, ws_conn: WebSocket) -> Optional[Tuple[str, str, WebSocket]]:
            try:
                await asyncio.wait_for(ws_conn.send_text(text), timeout=WS_SEND_TIMEOUT_S)
                return None
            except asyncio.TimeoutError:
                log_service.warning(
                    f"{log_service.who(sess_id, dev_id)}: dropped WebSocket - {label} timed out after {WS_SEND_TIMEOUT_S}s")
                return sess_id, dev_id, ws_conn
            except Exception as e:
                log_service.warning(f"{log_service.who(sess_id, dev_id)}: dropped WebSocket - {label} failed: {e}")
                return sess_id, dev_id, ws_conn

        results = await asyncio.gather(*[send_one(sid, did, ws) for sid, did, ws in targets])

        for failed in results:
            if failed is not None:
                self._evict(*failed)

    def set_display_renderer(self, renderer):
        self._display_renderer = renderer

    async def broadcast_to_session(self, session_id: str, message: dict):
        if session_id not in self._connections:
            return
        if self._display_renderer is not None:
            try:
                message = await self._display_renderer(message)
            except Exception as e:
                log_service.warning(f"Display renderer failed, sending raw message: {e}")

        connections = list(self._connections[session_id].items())
        if not connections:
            return

        try:
            text = self._serialize(message)
        except (TypeError, ValueError) as e:
            log_service.error(f"Failed to serialize message for {session_id}: {str(e)}")
            return

        await self._send_many([(session_id, did, ws) for did, ws in connections], text, "send to")

    async def broadcast_to_all_users(self, message: dict):
        all_connections = []
        for session_id, connections in list(self._connections.items()):
            for device_id, ws in list(connections.items()):
                all_connections.append((session_id, device_id, ws))

        if not all_connections:
            return

        try:
            text = self._serialize(message)
        except (TypeError, ValueError) as e:
            log_service.error(f"Failed to serialize broadcast message: {str(e)}")
            return

        await self._send_many(all_connections, text, "broadcast to")

    async def broadcast_playback_state(self, session_id: str, state: dict):
        message = {"type": "playback_state", "data": state}
        await self.broadcast_to_session(session_id, message)

    async def broadcast_preference_change(self, user_id: int, track_id: str, preference_type: str):
        session_id = str(user_id)
        message = {
            "type": "preference_change",
            "data": {
                "user_id": user_id,
                "track_id": track_id,
                "preference_type": preference_type
            }
        }
        await self.broadcast_to_session(session_id, message)

    async def broadcast_user_settings_updated(self, user_id: int, settings: dict):
        session_id = str(user_id)
        message = {
            "type": "user_settings_updated",
            "data": settings
        }
        await self.broadcast_to_session(session_id, message)
        log_service.system(f"{log_service.who(user_id=user_id)}: settings updated")

    async def broadcast_content_updated(self, content_type: str, content_id: str, metadata: Optional[Dict[str, Any]] = None):
        message = {
            "type": "content_updated",
            "data": {
                "content_type": content_type,
                "content_id": content_id,
                "metadata": metadata or {}
            }
        }
        await self.broadcast_to_all_users(message)
        action = "removed" if (metadata or {}).get("deleted") else "added"
        log_service.system(f"{content_type.capitalize()} {action} ({content_id}) - announced to all listeners")

    async def _cleanup_stale_connections(self):
        while True:
            await asyncio.sleep(300)
            current_time = time.time()

            sessions_to_remove = []
            for session_id in list(self._last_activity.keys()):
                devices_to_remove = []
                for device_id, last_seen in list(self._last_activity[session_id].items()):
                    if current_time - last_seen > self._stale_timeout:
                        devices_to_remove.append(device_id)

                for device_id in devices_to_remove:
                    log_service.warning(
                        f"{log_service.who(session_id, device_id)}: closing stale WebSocket (no activity)")
                    if session_id in self._connections and device_id in self._connections[session_id]:
                        stale_ws = self._connections[session_id].pop(device_id)
                        spawn(self._close_quietly(stale_ws), name=f"ws_close_stale:{session_id}/{device_id}")
                    del self._last_activity[session_id][device_id]

                if not self._last_activity[session_id]:
                    sessions_to_remove.append(session_id)

            for session_id in sessions_to_remove:
                del self._last_activity[session_id]
                if session_id in self._connections:
                    del self._connections[session_id]
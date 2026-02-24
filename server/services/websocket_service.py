from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple
from fastapi import WebSocket
from services import log_service

class WebSocketService:
    def __init__(self):
        self._connections: Dict[str, Dict[str, WebSocket]] = {}

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

        self._connections[session_id][device_id] = websocket
        self._last_activity[session_id][device_id] = time.time()

        total_connections = sum(len(devices) for devices in self._connections.values())
        log_service.system(
            f"WebSocket connected: {session_id}/{device_id} "
            f"(total: {total_connections} connections, {len(self._connections)} sessions)"
        )

    def unregister_connection(self, session_id: str, device_id: str):
        if session_id in self._connections and device_id in self._connections[session_id]:
            del self._connections[session_id][device_id]
            if not self._connections[session_id]:
                del self._connections[session_id]

        if session_id in self._last_activity and device_id in self._last_activity[session_id]:
            del self._last_activity[session_id][device_id]
            if not self._last_activity[session_id]:
                del self._last_activity[session_id]

        total_connections = sum(len(devices) for devices in self._connections.values())
        log_service.system(
            f"WebSocket disconnected: {session_id}/{device_id} "
            f"(total: {total_connections} connections, {len(self._connections)} sessions)"
        )

    def update_activity(self, session_id: str, device_id: str):
        if session_id in self._last_activity and device_id in self._last_activity[session_id]:
            self._last_activity[session_id][device_id] = time.time()

    def get_connection(self, session_id: str, device_id: str) -> Optional[WebSocket]:
        return self._connections.get(session_id, {}).get(device_id)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._connections and len(self._connections[session_id]) > 0

    def get_online_device_ids(self, session_id: str) -> List[str]:
        return list(self._connections.get(session_id, {}).keys())

    async def broadcast_to_session(self, session_id: str, message: dict):
        if session_id not in self._connections:
            return

        connections = list(self._connections[session_id].items())
        if not connections:
            return

        async def send_to_device(dev_id: str, ws_conn: WebSocket) -> Optional[Tuple[str, str]]:
            try:
                await ws_conn.send_json(message)
                return None
            except (RuntimeError, ConnectionError) as e:
                log_service.error(f"Failed to send to {session_id}/{dev_id}: {str(e)}")
                return session_id, dev_id
            except Exception as e:
                log_service.error(f"Failed to send to {session_id}/{dev_id}: {str(e)}")
                return session_id, dev_id

        results = await asyncio.gather(
            *[send_to_device(device_id, ws) for device_id, ws in connections]
        )

        disconnected = [r for r in results if r is not None]
        for sid, did in disconnected:
            if sid in self._connections and did in self._connections[sid]:
                del self._connections[sid][did]
                if not self._connections[sid]:
                    del self._connections[sid]

    async def broadcast_to_all_users(self, message: dict):
        all_connections = []
        for session_id, connections in list(self._connections.items()):
            for device_id, ws in list(connections.items()):
                all_connections.append((session_id, device_id, ws))

        if not all_connections:
            return

        async def send_to_device_broadcast(sess_id: str, dev_id: str, ws_conn: WebSocket) -> Optional[Tuple[str, str]]:
            try:
                await ws_conn.send_json(message)
                return None
            except (RuntimeError, ConnectionError) as e:
                log_service.error(f"Failed to broadcast to {sess_id}/{dev_id}: {str(e)}")
                return sess_id, dev_id
            except Exception as e:
                log_service.error(f"Failed to broadcast to {sess_id}/{dev_id}: {str(e)}")
                return sess_id, dev_id

        results = await asyncio.gather(
            *[send_to_device_broadcast(sid, did, ws) for sid, did, ws in all_connections]
        )

        disconnected = [r for r in results if r is not None]
        for sid, did in disconnected:
            if sid in self._connections and did in self._connections[sid]:
                del self._connections[sid][did]
                if not self._connections[sid]:
                    del self._connections[sid]

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
        log_service.system(f"📢 Broadcast: User settings updated for user {user_id}")

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
        log_service.system(f"📢 Broadcast: New {content_type} added ({content_id})")

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
                    log_service.warning(f"Cleaning up stale WebSocket: {session_id}/{device_id}")
                    if session_id in self._connections and device_id in self._connections[session_id]:
                        del self._connections[session_id][device_id]
                    del self._last_activity[session_id][device_id]

                if not self._last_activity[session_id]:
                    sessions_to_remove.append(session_id)

            for session_id in sessions_to_remove:
                del self._last_activity[session_id]
                if session_id in self._connections:
                    del self._connections[session_id]
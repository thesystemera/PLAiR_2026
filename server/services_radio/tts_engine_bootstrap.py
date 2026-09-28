import asyncio
import os
import subprocess
from typing import Optional

import aiohttp

from config.settings import settings, BASE_DIR
from services import log_service

TTS_SERVER_DIR = BASE_DIR / "tts_server"
TTS_SERVER_SCRIPT = TTS_SERVER_DIR / "server.py"
TTS_PYTHON = TTS_SERVER_DIR / ".venv" / "Scripts" / "python.exe"
TTS_LOG_FILE = BASE_DIR / "data" / "logs" / "tts_server.log"


class TTSEngineBootstrap:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self.ready = asyncio.Event()

    async def health(self) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{settings.TTS_SERVER_URL}/health") as response:
                    if response.status == 200:
                        return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None
        return None

    async def start(self):
        existing = await self.health()
        if existing:
            log_service.tts_generation(f"TTS engine already running at {settings.TTS_SERVER_URL} ({existing.get('status')})")
            asyncio.create_task(self._wait_until_ready())
            return

        if settings.TTS_SERVER_EXTERNAL:
            log_service.tts_generation(f"TTS engine is external - waiting for {settings.TTS_SERVER_URL}")
            asyncio.create_task(self._wait_until_ready())
            return

        if not TTS_PYTHON.exists():
            log_service.error(f"TTS engine venv missing at {TTS_PYTHON} - DJ speech disabled. See tts_server/README.md")
            return

        TTS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(TTS_LOG_FILE, "a", encoding="utf-8")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            [str(TTS_PYTHON), str(TTS_SERVER_SCRIPT)],
            cwd=str(TTS_SERVER_DIR),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        log_service.tts_generation(f"TTS engine launching (pid {self._proc.pid}) - log: {TTS_LOG_FILE}")
        asyncio.create_task(self._wait_until_ready())

    async def _wait_until_ready(self, timeout: float = 300):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                log_service.error(f"TTS engine exited with code {self._proc.returncode} - see {TTS_LOG_FILE}")
                return
            status = await self.health()
            if status and status.get("status") == "ok":
                self.ready.set()
                log_service.success(f"✓ TTS engine ready ({status.get('workers')} workers, {status.get('quality')})")
                return
            await asyncio.sleep(1)
        log_service.error(f"TTS engine not ready after {timeout:.0f}s - see {TTS_LOG_FILE}")

    async def stop(self):
        self.ready.clear()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.to_thread(self._proc.wait, 10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            log_service.system("TTS engine stopped")
        self._proc = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


tts_engine_bootstrap = TTSEngineBootstrap()

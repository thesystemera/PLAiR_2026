import json
import aiofiles
from typing import Dict
from datetime import datetime, timezone
from services import log_service
from services.base_service import SingletonService
from config import settings

class AnalyticsFileService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.base_dir = settings.ANALYTICS_DIR
        self.tracks_dir = self.base_dir / "tracks"
        self.events_dir = self.base_dir / "daily_events"
        self.exports_dir = self.base_dir / "exports"
        self._initialized = True

    async def initialize(self):
        log_service.analytics("AnalyticsFileService initialized")

    async def write_track_analytics(self, track_id: str, data: Dict) -> bool:
        try:
            file_path = self.tracks_dir / f"{track_id}.json"
            async with aiofiles.open(file_path, 'w') as f:
                await f.write(json.dumps(data, indent=2))
            return True
        except Exception as e:
            log_service.error(f"Failed to write analytics for track {track_id}: {e}")
            return False

    async def append_daily_event(self, event: Dict) -> bool:
        try:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            file_path = self.events_dir / f"{date_str}.jsonl"
            async with aiofiles.open(file_path, 'a') as f:
                await f.write(json.dumps(event) + '\n')
            return True
        except Exception as e:
            log_service.error(f"Failed to append event to daily log: {e}")
            return False

analytics_file_service = AnalyticsFileService()
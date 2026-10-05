from pathlib import Path
from typing import Optional

from config import settings
from services.audio_apollo_service import AudioApolloService


class AudioVocalEnhanceService(AudioApolloService):
    _instance = None
    LABEL = "Vocal Apollo"
    MODEL_ARGS = {"sr": 44100, "win": 20, "feature_dim": 192, "layer": 6}
    CHUNK_SECONDS = 6
    OVERLAP_SECONDS = 1
    KEEP_SOURCE_BELOW_CUTOFF = False

    def checkpoint(self) -> Optional[Path]:
        return settings.VOCAL_APOLLO_CHECKPOINT

    @property
    def models_loaded(self) -> bool:
        return self.apollo_loaded

    async def enhance_vocals(self, input_path: Path, output_path: Path) -> Optional[Path]:
        return await self.process_audio(input_path, output_path)

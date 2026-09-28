from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from services import log_service
from config import settings

BANDWIDTH_STAGES = ("apollo", "flashsr", "off")
SEPARATION_MODELS = ("demucs", "roformer")

QualityScorer = Callable[[Any], Awaitable[Optional[Dict[str, float]]]]
QUALITY_SCORERS: Dict[str, QualityScorer] = {}
BUILTIN_QUALITY_SCORERS = ("audiobox",)


def _bandwidth_factory(name: str):
    if name == "apollo":
        from services.audio_apollo_service import AudioApolloService
        return AudioApolloService()
    if name == "flashsr":
        from services.audio_flashsr_service import AudioFlashSRService
        return AudioFlashSRService()
    return None


def _separation_factory(name: str):
    if name == "demucs":
        from services.audio_demucs_service import AudioDemucsService
        return AudioDemucsService()
    if name == "roformer":
        from services.audio_roformer_service import AudioRoformerService
        return AudioRoformerService()
    return None


def stems_dir_for(track_id: str, model: Optional[str] = None) -> Path:
    base = settings.DEMUCS_STEMS_DIR / track_id
    if (model or settings.SEPARATION_MODEL or "demucs").lower() == "roformer":
        return base / "roformer"
    return base


async def _audiobox_scorer(path) -> Optional[Dict[str, float]]:
    from services.audio_quality_score_service import AudioQualityScoreService
    scorer = AudioQualityScoreService()
    if not scorer.scorer_available:
        await scorer.initialize()
        if not scorer.scorer_available:
            return None
    return await scorer.score(path)


def resolve_bandwidth_stage(name: Optional[str] = None) -> Tuple[Optional[Any], str]:
    requested = (name or settings.BANDWIDTH_STAGE or "apollo").lower()
    if requested == "off":
        return None, "off"
    service = _bandwidth_factory(requested)
    if service is None:
        log_service.warning(f"[Audio stages] Bandwidth stage '{requested}' is not available yet, using Apollo")
        return _bandwidth_factory("apollo"), "apollo"
    return service, requested


def resolve_separation_model(name: Optional[str] = None) -> Tuple[Any, str]:
    requested = (name or settings.SEPARATION_MODEL or "demucs").lower()
    service = _separation_factory(requested)
    if service is None:
        log_service.warning(f"[Audio stages] Separation model '{requested}' is not available yet, using Demucs")
        return _separation_factory("demucs"), "demucs"
    return service, requested


def register_quality_scorer(name: str, scorer: QualityScorer):
    QUALITY_SCORERS[name.lower()] = scorer


def quality_scorer(name: Optional[str] = None) -> Optional[QualityScorer]:
    key = (name if name is not None else settings.QUALITY_SCORER or "").lower()
    if not key:
        return None
    if key == "audiobox" and key not in QUALITY_SCORERS:
        QUALITY_SCORERS[key] = _audiobox_scorer
    return QUALITY_SCORERS.get(key)


def stage_available(kind: str, name: str) -> bool:
    if kind == "bandwidth":
        return name == "off" or _bandwidth_factory(name) is not None
    if kind == "separation":
        return _separation_factory(name) is not None
    return False


def create_stage_service(kind: str, name: str):
    if kind == "bandwidth":
        return _bandwidth_factory(name)
    if kind == "separation":
        return _separation_factory(name)
    return None

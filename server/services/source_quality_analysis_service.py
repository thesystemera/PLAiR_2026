import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np
import soundfile as sf
import librosa

from services import log_service
from services.base_service import SingletonService


class QualityTier(Enum):
    STUDIO = "studio"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class SourceQualityReport:

    sample_rate: int
    channels: int
    bit_depth: Optional[str]
    format: str
    duration_seconds: float
    file_size_bytes: int

    quality_tier: str
    is_lossless: bool
    estimated_bitrate_kbps: Optional[int]

    effective_bandwidth_hz: int
    bandwidth_utilization: float
    has_compression_artifacts: bool

    dynamic_range_db: float
    noise_floor_db: float
    peak_db: float

    recommended_processing: List[str]
    skip_processing: List[str]
    processing_notes: str

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        for key, value in result.items():
            if hasattr(value, 'item'):
                result[key] = value.item()
            elif isinstance(value, float):
                result[key] = float(value)
        return result


class SourceQualityAnalysisService(SingletonService):

    def __init__(self):
        if self._initialized:
            return

        self.analysis_bands = [
            (0, 4000, "low_mid"),
            (4000, 8000, "high_mid"),
            (8000, 12000, "presence"),
            (12000, 16000, "air"),
            (16000, 20000, "ultra_high"),
            (20000, 22050, "near_nyquist")
        ]

        self.HIGH_QUALITY_BANDWIDTH_RATIO = 0.85
        self.LOW_QUALITY_BANDWIDTH_RATIO = 0.65

        self._initialized = True

    async def initialize(self):
        log_service.system("✓ SourceQualityAnalysisService initialized")

    async def analyze(self, audio_path: Path) -> Optional[SourceQualityReport]:

        if not audio_path.exists():
            log_service.error(f"[QualityAnalysis] File not found: {audio_path}")
            return None

        try:
            return await asyncio.to_thread(self._analyze_sync, audio_path)
        except Exception as e:
            log_service.error(f"[QualityAnalysis] Analysis failed: {e}")
            return None

    def _analyze_sync(self, audio_path: Path) -> SourceQualityReport:

        file_size = audio_path.stat().st_size
        info = sf.info(str(audio_path))

        sample_rate = info.samplerate
        channels = info.channels
        duration = info.duration
        format_name = info.format
        subtype = info.subtype

        bit_depth = self._parse_bit_depth(subtype)
        is_lossless = self._is_lossless_format(format_name, subtype)

        estimated_bitrate = None
        if not is_lossless and duration > 0:
            estimated_bitrate = int((file_size * 8) / duration / 1000)

        analysis_duration = min(duration, 30.0)
        y, sr = librosa.load(str(audio_path), sr=sample_rate, duration=analysis_duration, mono=True)

        effective_bandwidth, bandwidth_util, has_artifacts = self._analyze_spectrum(y, sr)

        dynamic_range, noise_floor, peak_db = self._analyze_dynamics(y)

        quality_tier = self._classify_quality(
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            is_lossless=is_lossless,
            estimated_bitrate=estimated_bitrate,
            bandwidth_utilization=bandwidth_util,
            has_artifacts=has_artifacts
        )

        recommended, skip, notes = self._generate_recommendations(
            quality_tier=quality_tier,
            is_lossless=is_lossless,
            sample_rate=sample_rate,
            bandwidth_utilization=bandwidth_util,
            dynamic_range=dynamic_range
        )

        report = SourceQualityReport(
            sample_rate=sample_rate,
            channels=channels,
            bit_depth=bit_depth,
            format=format_name,
            duration_seconds=round(duration, 2),
            file_size_bytes=file_size,
            quality_tier=quality_tier.value,
            is_lossless=is_lossless,
            estimated_bitrate_kbps=estimated_bitrate,
            effective_bandwidth_hz=effective_bandwidth,
            bandwidth_utilization=round(bandwidth_util, 3),
            has_compression_artifacts=has_artifacts,
            dynamic_range_db=round(dynamic_range, 2),
            noise_floor_db=round(noise_floor, 2),
            peak_db=round(peak_db, 2),
            recommended_processing=recommended,
            skip_processing=skip,
            processing_notes=notes
        )

        log_service.info(
            f"[QualityAnalysis] {audio_path.name}: "
            f"{quality_tier.value.upper()} tier, "
            f"{sample_rate}Hz, {bit_depth or 'unknown'}, "
            f"bandwidth: {bandwidth_util:.0%}"
        )

        return report

    def _parse_bit_depth(self, subtype: str) -> Optional[str]:
        subtype_upper = subtype.upper()

        if "PCM_16" in subtype_upper:
            return "16-bit"
        elif "PCM_24" in subtype_upper:
            return "24-bit"
        elif "PCM_32" in subtype_upper or "FLOAT" in subtype_upper:
            return "32-bit"
        elif "PCM_8" in subtype_upper:
            return "8-bit"
        elif "MPEG" in subtype_upper:
            return "lossy"
        elif "VORBIS" in subtype_upper or "OPUS" in subtype_upper:
            return "lossy"

        return None

    def _is_lossless_format(self, format_name: str, subtype: str) -> bool:
        format_upper = format_name.upper()
        subtype_upper = subtype.upper()

        if format_upper in ["WAV", "AIFF", "FLAC", "W64"]:
            return True

        lossy_subtypes = ["MPEG", "VORBIS", "OPUS", "AAC", "MP3"]
        for lossy in lossy_subtypes:
            if lossy in subtype_upper or lossy in format_upper:
                return False

        return True

    def _analyze_spectrum(self, y: np.ndarray, sr: int) -> tuple:

        n_fft = 4096
        hop_length = 1024

        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
        S_db = librosa.amplitude_to_db(S, ref=np.max)

        avg_spectrum = np.mean(S_db, axis=1)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

        threshold = -60
        nyquist = sr / 2

        effective_bandwidth = nyquist
        for i in range(len(avg_spectrum) - 1, 0, -1):
            if avg_spectrum[i] > threshold:
                effective_bandwidth = freqs[i]
                break

        bandwidth_utilization = effective_bandwidth / nyquist

        has_artifacts = False

        if sr >= 44100:
            high_freq_mask = (freqs >= 15000) & (freqs <= 18000)
            ultra_high_mask = freqs > 18000

            if np.any(high_freq_mask) and np.any(ultra_high_mask):
                high_energy = np.mean(avg_spectrum[high_freq_mask])
                ultra_high_energy = np.mean(avg_spectrum[ultra_high_mask])

                if (high_energy - ultra_high_energy) > 20:
                    has_artifacts = True

        return int(effective_bandwidth), bandwidth_utilization, has_artifacts

    def _analyze_dynamics(self, y: np.ndarray) -> tuple:

        rms = librosa.feature.rms(y=y)[0]
        rms_db = librosa.amplitude_to_db(rms, ref=1.0)

        peak = np.max(np.abs(y))
        peak_db = 20 * np.log10(peak + 1e-10)

        non_silent_mask = rms_db > (np.max(rms_db) - 40)
        if np.any(non_silent_mask):
            dynamic_range = np.max(rms_db[non_silent_mask]) - np.min(rms_db[non_silent_mask])
        else:
            dynamic_range = 0

        noise_floor = np.percentile(rms_db, 5)

        return dynamic_range, noise_floor, peak_db

    def _classify_quality(
        self,
        sample_rate: int,
        bit_depth: Optional[str],
        is_lossless: bool,
        estimated_bitrate: Optional[int],
        bandwidth_utilization: float,
        has_artifacts: bool
    ) -> QualityTier:

        if (is_lossless and
            sample_rate >= 48000 and
            bit_depth in ["24-bit", "32-bit"] and
            bandwidth_utilization >= self.HIGH_QUALITY_BANDWIDTH_RATIO):
            return QualityTier.STUDIO

        if (is_lossless and
            sample_rate >= 44100 and
            bandwidth_utilization >= self.HIGH_QUALITY_BANDWIDTH_RATIO):
            return QualityTier.HIGH

        if estimated_bitrate:
            if estimated_bitrate >= 256 and bandwidth_utilization >= 0.75:
                return QualityTier.HIGH
            elif estimated_bitrate >= 192 and bandwidth_utilization >= 0.70:
                return QualityTier.MEDIUM
            else:
                return QualityTier.LOW

        if bandwidth_utilization >= self.HIGH_QUALITY_BANDWIDTH_RATIO:
            return QualityTier.HIGH
        elif bandwidth_utilization >= self.LOW_QUALITY_BANDWIDTH_RATIO:
            return QualityTier.MEDIUM
        else:
            return QualityTier.LOW

    def _generate_recommendations(
        self,
        quality_tier: QualityTier,
        is_lossless: bool,
        sample_rate: int,
        bandwidth_utilization: float,
        dynamic_range: float
    ) -> tuple:
        recommended = []
        skip = []
        notes_parts = []

        recommended.append("mastering")
        recommended.append("audio_features")
        recommended.append("transcoding")

        if quality_tier == QualityTier.STUDIO:
            skip.extend(["apollo", "demucs", "clearvoice", "sonic_master"])
            notes_parts.append(
                "Studio-quality source detected. Skipping enhancement to preserve "
                "original mastering. Only applying loudness normalization."
            )

        elif quality_tier == QualityTier.HIGH:
            skip.extend(["apollo", "sonic_master"])

            if bandwidth_utilization < 0.90:
                recommended.append("clearvoice_optional")
                notes_parts.append(
                    "High-quality source with minor bandwidth limitations. "
                    "Optional vocal enhancement available."
                )
            else:
                skip.extend(["demucs", "clearvoice"])
                notes_parts.append("High-quality source. Minimal processing recommended.")

        elif quality_tier == QualityTier.MEDIUM:
            recommended.extend(["apollo", "clearvoice_optional"])
            skip.append("sonic_master")  # May add artifacts
            notes_parts.append(
                "Medium-quality source. Apollo bandwidth restoration recommended. "
                "Vocal enhancement optional."
            )

        elif quality_tier == QualityTier.LOW:
            recommended.extend(["apollo", "demucs", "clearvoice", "sonic_master"])
            notes_parts.append(
                "Low-quality source detected. Full enhancement pipeline recommended "
                "to restore bandwidth and improve clarity."
            )

        else:
            recommended.extend(["apollo", "mastering"])
            notes_parts.append("Quality could not be determined. Applying standard processing.")

        if dynamic_range < 6:
            notes_parts.append("Low dynamic range detected (heavily compressed).")

        return recommended, skip, " ".join(notes_parts)

    def should_apply_processing(self, report: SourceQualityReport, processing_name: str) -> bool:
        return processing_name.lower() in [p.lower() for p in report.recommended_processing]

source_quality_analysis_service = SourceQualityAnalysisService()
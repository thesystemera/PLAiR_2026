import json
import librosa
import numpy as np
import aiofiles
import time
import os
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from services import log_service
from services.base_service import SingletonService
from config import settings

os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'

class AudioFeaturesService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.audiofeatures_dir = settings.AUDIOFEATURES_DIR
        self.sample_rate = 44100
        self.hop_length = 512
        self.frame_duration = self.hop_length / self.sample_rate
        self._initialized = True

    async def initialize(self):
        log_service.system("AudioFeaturesService initialized")

    def _analyze_audio_sync(
        self,
        audio_path: Path,
        track_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            start_total = time.time()

            t0 = time.time()
            y, sr = librosa.load(audio_path, sr=self.sample_rate, mono=True)
            duration = librosa.get_duration(y=y, sr=sr)
            log_service.audio_features(f"  [1/9] Load audio: {time.time()-t0:.2f}s")

            t0 = time.time()
            tempo, beats = self._extract_tempo_and_beats(y, int(sr))
            log_service.audio_features(f"  [2/9] Tempo/beats: {time.time()-t0:.2f}s")

            t0 = time.time()
            loudness_segments = self._extract_loudness_segments(y, int(sr))
            log_service.audio_features(f"  [3/9] Loudness: {time.time()-t0:.2f}s")

            t0 = time.time()
            sections = self._detect_sections(y, int(sr))
            log_service.audio_features(f"  [4/9] Sections: {time.time()-t0:.2f}s")

            t0 = time.time()
            overall_stats = self._calculate_overall_stats(loudness_segments)
            crossfade_points = self._calculate_crossfade_points(
                loudness_segments, beats, duration
            )
            log_service.audio_features(f"  [5/9] Stats/crossfade: {time.time()-t0:.2f}s")

            t0 = time.time()
            announcer_zones = self._detect_announcer_safe_zones(
                loudness_segments, y, int(sr), duration
            )
            log_service.audio_features(f"  [6/9] Announcer zones: {time.time()-t0:.2f}s")

            t0 = time.time()
            key, mode = self._extract_key_and_mode(y, int(sr))
            log_service.audio_features(f"  [7/9] Key/mode: {time.time()-t0:.2f}s")

            t0 = time.time()
            energy = self._calculate_energy(y)
            danceability = self._calculate_danceability(tempo, beats, duration)
            valence = self._calculate_valence(y, int(sr))
            speechiness = self._calculate_speechiness(y)
            acousticness = self._calculate_acousticness(y, int(sr))
            instrumentalness = self._calculate_instrumentalness(y)
            log_service.audio_features(f"  [8/9] Musical features: {time.time()-t0:.2f}s")

            t0 = time.time()
            features = {
                "id": track_id,
                "analysis_version": "1.0",
                "duration": round(duration, 3),
                "sample_rate": sr,
                "tempo": round(tempo, 2),
                "tempo_confidence": 0.85,
                "time_signature": 4,
                "key": key,
                "mode": mode,
                "overall_loudness": round(overall_stats["mean_loudness"], 2),
                "dynamic_range": round(overall_stats["dynamic_range"], 2),
                "peak_loudness": round(overall_stats["peak_loudness"], 2),
                "min_loudness": round(overall_stats["min_loudness"], 2),
                "energy": round(energy, 3),
                "danceability": round(danceability, 3),
                "valence": round(valence, 3),
                "speechiness": round(speechiness, 3),
                "acousticness": round(acousticness, 3),
                "instrumentalness": round(instrumentalness, 3),
                "beats": [round(b, 3) for b in beats],
                "beat_count": len(beats),
                "loudness_segments": loudness_segments,
                "sections": sections,
                "crossfade_points": crossfade_points,
                "announcer_safe_zones": announcer_zones
            }
            log_service.audio_features(f"  [9/9] Build dict: {time.time()-t0:.2f}s")
            log_service.audio_features(f"  ✓ TOTAL: {time.time()-start_total:.2f}s")

            return features

        except Exception as e:
            log_service.error(f"[AudioFeatures] Analysis error: {str(e)}")
            return None

    async def analyze_audio(
        self,
        audio_path: Path,
        track_id: str,
        save: bool = True
    ) -> Optional[Dict[str, Any]]:
        try:
            log_service.info(f"[AudioFeatures] Analyzing {track_id}...")

            import asyncio
            features = await asyncio.to_thread(self._analyze_audio_sync, audio_path, track_id)

            if not features:
                log_service.error(f"[AudioFeatures] Analysis failed for {track_id}")
                return None

            if save:
                await self._save_features(features, track_id)

            log_service.success(
                f"[AudioFeatures] Complete: {len(features['loudness_segments'])} segments, "
                f"{len(features['beats'])} beats, {len(features['sections'])} sections"
            )

            return features

        except Exception as e:
            log_service.error(f"[AudioFeatures] Analysis failed: {str(e)}")
            return None

    def _extract_tempo_and_beats(self, y: np.ndarray, sr: int) -> Tuple[float, List[float]]:
        tempo, beat_frames = librosa.beat.beat_track(
            y=y,
            sr=sr,
            hop_length=self.hop_length,
            start_bpm=120.0,
            tightness=100
        )

        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=self.hop_length)

        return float(tempo), beat_times.tolist()

    def _extract_loudness_segments(self, y: np.ndarray, sr: int) -> List[Dict[str, Any]]:
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=self.hop_length)[0]
        db = librosa.amplitude_to_db(rms, ref=np.max)
        times = librosa.frames_to_time(np.arange(len(db)), sr=sr, hop_length=self.hop_length)
        segment_duration = 0.1  # 100ms
        segments = []

        i = 0
        while i < len(times):
            start_time = times[i]

            end_time = start_time + segment_duration
            window_mask = (times >= start_time) & (times < end_time)
            window_db = db[window_mask]
            window_rms = rms[window_mask]

            if len(window_db) > 0:
                segments.append({
                    "start": round(start_time, 3),
                    "duration": segment_duration,
                    "loudness": round(float(np.mean(window_db)), 2),
                    "loudness_max": round(float(np.max(window_db)), 2),
                    "rms": round(float(np.mean(window_rms)), 4)
                })

            i += int(segment_duration / self.frame_duration)

        return segments

    def _detect_sections(
        self,
        y: np.ndarray,
        sr: int
    ) -> List[Dict[str, Any]]:
        try:
            chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=self.hop_length)
            bounds = librosa.segment.agglomerative(chroma, k=8)
            bound_times = librosa.frames_to_time(bounds, sr=sr, hop_length=self.hop_length)

            sections = []
            section_types = ["intro", "verse", "chorus", "verse", "bridge", "chorus", "outro"]

            for i, start in enumerate(bound_times[:-1]):
                end = bound_times[i + 1]
                duration = end - start

                start_sample = int(float(start) * sr)
                end_sample = int(float(end) * sr)
                section_audio = y[start_sample:end_sample]

                if len(section_audio) > 0:
                    rms = librosa.feature.rms(y=section_audio, frame_length=2048)[0]
                    db = librosa.amplitude_to_db(rms, ref=np.max)
                    avg_loudness = float(np.mean(db))
                else:
                    avg_loudness = -60.0

                section_type = section_types[i % len(section_types)]
                if i == 0:
                    section_type = "intro"
                elif i == len(bound_times) - 2:
                    section_type = "outro"

                sections.append({
                    "start": round(float(start), 3),
                    "duration": round(float(duration), 3),
                    "type": section_type,
                    "loudness": round(avg_loudness, 2),
                    "confidence": 0.75  # Placeholder
                })

            return sections

        except Exception as e:
            log_service.warning(f"[AudioFeatures] Section detection failed, using fallback: {str(e)}")
            return [
                {
                    "start": 0.0,
                    "duration": 8.0,
                    "type": "intro",
                    "loudness": -20.0,
                    "confidence": 0.5
                }
            ]

    @staticmethod
    def _calculate_overall_stats(
        segments: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        loudness_values = [seg["loudness"] for seg in segments]

        return {
            "mean_loudness": np.mean(loudness_values),
            "peak_loudness": np.max(loudness_values),
            "min_loudness": np.min(loudness_values),
            "dynamic_range": np.max(loudness_values) - np.min(loudness_values)
        }

    def _calculate_crossfade_points(
        self,
        segments: List[Dict[str, Any]],
        beats: List[float],
        duration: float
    ) -> Dict[str, Dict[str, float]]:
        fade_in_end = 0.0
        intro_segments = [s for s in segments if s["start"] < 15.0]

        if intro_segments:
            intro_loudness = [s["loudness"] for s in intro_segments]
            threshold = np.percentile(intro_loudness, 30)

            for seg in intro_segments:
                if seg["loudness"] > threshold:
                    fade_in_end = self._find_nearest_beat(seg["start"], beats)
                    break

            if fade_in_end == 0.0:
                fade_in_end = min(4.0, duration * 0.1)

        fade_out_start = duration - 8.0
        outro_segments = [s for s in segments if s["start"] > duration - 15.0]

        if outro_segments:
            outro_loudness = [s["loudness"] for s in outro_segments]
            threshold = np.percentile(outro_loudness, 40)

            for seg in reversed(outro_segments[:-10]):
                if seg["loudness"] > threshold:
                    fade_out_start = self._find_nearest_beat(seg["start"] + seg["duration"], beats)
                    break

        return {
            "optimal_fade_in": {
                "start": 0.0,
                "end": round(fade_in_end, 3),
                "type": "beat_aligned"
            },
            "optimal_fade_out": {
                "start": round(fade_out_start, 3),
                "end": round(duration, 3),
                "type": "beat_aligned"
            }
        }

    def _detect_announcer_safe_zones(
        self,
        segments: List[Dict[str, Any]],
        y: np.ndarray,
        sr: int,
        duration: float
    ) -> List[Dict[str, Any]]:
        safe_zones = []

        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=self.hop_length)

        min_zone_duration = 3.0
        loudness_threshold = -25.0

        current_zone_start = None

        for _, seg in enumerate(segments):
            is_quiet = seg["loudness"] < loudness_threshold

            onset_idx = int(seg["start"] / self.frame_duration)
            if onset_idx < len(onset_env):
                onset_strength = onset_env[onset_idx]
                is_low_activity = onset_strength < np.percentile(onset_env, 40)
            else:
                is_low_activity = True

            if is_quiet and is_low_activity:
                if current_zone_start is None:
                    current_zone_start = seg["start"]
            else:
                if current_zone_start is not None:
                    zone_duration = seg["start"] - current_zone_start
                    if zone_duration >= min_zone_duration:
                        confidence = min(0.95, 0.6 + (zone_duration / 10.0) * 0.3)
                        safe_zones.append({
                            "start": round(current_zone_start, 3),
                            "end": round(seg["start"], 3),
                            "duration": round(zone_duration, 3),
                            "confidence": round(confidence, 2),
                            "avg_loudness": round(
                                np.mean([s["loudness"] for s in segments
                                        if current_zone_start <= s["start"] < seg["start"]]),
                                2
                            )
                        })
                    current_zone_start = None

        if current_zone_start is not None and segments:
            zone_duration = duration - current_zone_start
            if zone_duration >= min_zone_duration:
                confidence = min(0.95, 0.6 + (zone_duration / 10.0) * 0.3)
                safe_zones.append({
                    "start": round(current_zone_start, 3),
                    "end": round(duration, 3),
                    "duration": round(zone_duration, 3),
                    "confidence": round(confidence, 2),
                    "avg_loudness": -30.0
                })

        safe_zones.sort(key=lambda z: z["confidence"], reverse=True)

        return safe_zones[:3]

    @staticmethod
    def _find_nearest_beat(timestamp: float, beats: List[float]) -> float:
        if not beats:
            return timestamp

        beats_array = np.array(beats)
        idx = np.argmin(np.abs(beats_array - timestamp))
        return float(beats_array[idx])

    @staticmethod
    def _extract_key_and_mode(y: np.ndarray, sr: int) -> Tuple[int, int]:
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)
        key = int(np.argmax(chroma_mean))

        harmonic = librosa.effects.harmonic(y)
        chroma_harmonic = librosa.feature.chroma_stft(y=harmonic, sr=sr)

        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        chroma_sum = np.sum(chroma_harmonic, axis=1)
        chroma_sum = chroma_sum / np.sum(chroma_sum)

        major_corr = np.corrcoef(chroma_sum, np.roll(major_profile, key))[0, 1]
        minor_corr = np.corrcoef(chroma_sum, np.roll(minor_profile, key))[0, 1]

        mode = 1 if major_corr > minor_corr else 0

        return key, mode

    @staticmethod
    def _calculate_energy(y: np.ndarray) -> float:
        rms = librosa.feature.rms(y=y)[0]
        energy = np.mean(rms ** 2)
        energy_normalized = min(1.0, energy * 10)
        return float(energy_normalized)

    @staticmethod
    def _calculate_danceability(tempo: float, beats: List[float], duration: float) -> float:
        tempo_factor = 1.0 - abs(tempo - 120) / 120
        tempo_factor = max(0.0, min(1.0, tempo_factor))

        if len(beats) > 1 and duration > 0:
            beat_regularity = len(beats) / duration / (tempo / 60.0)
            beat_regularity = max(0.0, min(1.0, beat_regularity))
        else:
            beat_regularity = 0.5

        danceability = 0.6 * tempo_factor + 0.4 * beat_regularity
        return float(danceability)

    @staticmethod
    def _calculate_valence(y: np.ndarray, sr: int) -> float:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        major_profile = major_profile / np.sum(major_profile)

        major_similarity = np.dot(chroma_mean, major_profile)
        valence = min(1.0, major_similarity * 2)
        return float(valence)

    @staticmethod
    def _calculate_speechiness(y: np.ndarray) -> float:
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        zcr_mean = np.mean(zcr)

        spectral_flatness = librosa.feature.spectral_flatness(y=y)[0]
        flatness_mean = np.mean(spectral_flatness)

        speechiness = (zcr_mean * 0.6 + flatness_mean * 0.4)
        speechiness = min(1.0, speechiness * 3)
        return float(speechiness)

    @staticmethod
    def _calculate_acousticness(y: np.ndarray, sr: int) -> float:
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        centroid_mean = np.mean(spectral_centroid)

        centroid_normalized = 1.0 - (centroid_mean / (sr / 2))

        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
        rolloff_mean = np.mean(spectral_rolloff)
        rolloff_normalized = 1.0 - (rolloff_mean / (sr / 2))

        acousticness = 0.6 * centroid_normalized + 0.4 * rolloff_normalized
        acousticness = max(0.0, min(1.0, acousticness))
        return float(acousticness)

    @staticmethod
    def _calculate_instrumentalness(y: np.ndarray) -> float:
        harmonic, percussive = librosa.effects.hpss(y)

        harmonic_energy = np.sum(harmonic ** 2)
        percussive_energy = np.sum(percussive ** 2)
        total_energy = harmonic_energy + percussive_energy

        if total_energy > 0:
            harmonic_ratio = harmonic_energy / total_energy
        else:
            harmonic_ratio = 0.5

        zcr = librosa.feature.zero_crossing_rate(y)[0]
        zcr_mean = np.mean(zcr)
        speech_like = min(1.0, zcr_mean * 4)

        instrumentalness = harmonic_ratio * (1.0 - speech_like * 0.5)
        instrumentalness = max(0.0, min(1.0, instrumentalness))
        return float(instrumentalness)

    async def _save_features(self, features: Dict[str, Any], track_id: str) -> Path:
        filepath = self.audiofeatures_dir / f"{track_id}.json"

        try:
            async with aiofiles.open(filepath, 'w') as f:
                await f.write(json.dumps(features, indent=2))

            log_service.success(f"[AudioFeatures] Saved: {filepath.name}")
            return filepath

        except Exception as e:
            log_service.error(f"[AudioFeatures] Save failed: {str(e)}")
            raise

    async def load_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        filepath = self.audiofeatures_dir / f"{track_id}.json"

        try:
            async with aiofiles.open(filepath, 'r') as f:
                content = await f.read()
                return json.loads(content)

        except FileNotFoundError:
            log_service.warning(f"[AudioFeatures] Not found: {track_id}")
            return None
        except Exception as e:
            log_service.error(f"[AudioFeatures] Load failed: {str(e)}")
            return None

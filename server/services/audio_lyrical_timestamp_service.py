import asyncio
import gc
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import numpy as np
import soundfile as sf
import torch

from services import log_service
from services import track_asset_stages
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom

SECTION_PATTERN = re.compile(r'\[(Intro|Verse|Chorus|Bridge|Outro|Breakdown|Instrumental|Pre-Chorus)[^]]*?\]', re.IGNORECASE)
MATCH_SCORE = 2.0
FUZZY_SCORE = 1.0
MISMATCH_SCORE = -1.0
GAP_SCORE = -1.0
FUZZY_THRESHOLD = 0.75
MAX_DP_CELLS = 3_000_000
MIN_ALIGNED_RATIO = 0.15
INTERPOLATED_WORD_S = 0.4
MAX_LINE_WORDS = 14
MAX_LINE_DURATION_S = 8.0
MAX_WORD_GAP_S = 1.5
LANGUAGE_DETECTION_SEGMENTS = 4
SYNC_VERSION = "1.1"
VOICE_HOP_S = 0.01
VOICE_REFERENCE_PERCENTILE = 95
VOICE_AUDIBLE_DB = -80.0
VOICE_SILENT_DB = -40.0
VOICE_PRESENT_DB = -30.0
VOICE_PROBE_S = 0.06
VOICE_PREROLL_S = 0.02
VOICE_PHRASE_GAP_S = 0.6
MIN_WORD_S = 0.08
ENGLISH_RATIO_THRESHOLD = 0.12

ENGLISH_MARKERS = frozenset(
    "the and you i to a me my in it of is on that for we your be all with so but what just its im dont "
    "know like now oh this are no can when up out go get got was were will wont cant aint youre ive ill "
    "our us they them their there here not do did have has had been baby love yeah heart night never "
    "feel want need way time from into only one more let make down back tonight gonna wanna".split()
)

LANGUAGE_NAMES = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt", "italian": "it",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "mandarin": "zh", "cantonese": "zh", "russian": "ru",
    "hindi": "hi", "arabic": "ar", "dutch": "nl", "swedish": "sv", "polish": "pl", "turkish": "tr",
}


def _clear_cuda_cache():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_language_code(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower().replace("_", "-")
    if not cleaned:
        return None
    if cleaned in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[cleaned]
    base = cleaned.split("-")[0]
    if len(base) == 2 and base.isalpha():
        return base
    return None


def timing_is_current(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sync_version") == SYNC_VERSION
    except (OSError, ValueError):
        return False


def voice_levels(path: Path) -> np.ndarray:
    """The vocal stem's level every VOICE_HOP_S, in dB against its own loud singing."""
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    hop = max(1, int(rate * VOICE_HOP_S))
    frames = len(mono) // hop
    rms = np.sqrt(np.mean(mono[:frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-12)
    levels = 20 * np.log10(rms)
    audible = levels[levels > VOICE_AUDIBLE_DB]
    return levels - (np.percentile(audible, VOICE_REFERENCE_PERCENTILE) if audible.size else 0.0)


def snap_to_voice(words: List[Dict[str, Any]], levels: np.ndarray) -> int:
    """Whisper starts a word that follows a pause where the pause began. A word whose start falls in silence on the
    vocal stem starts where the voice comes in instead (by the end of the next word: one Whisper heard in silence
    lands with the next sung word), and ends no sooner than MIN_WORD_S after it."""
    moved = 0
    for index, word in enumerate(words):
        first = round(word["start"] / VOICE_HOP_S)
        probe = levels[first:first + round(VOICE_PROBE_S / VOICE_HOP_S)]
        if probe.size == 0 or float(np.median(probe)) >= VOICE_SILENT_DB:
            continue
        limit = max(word["end"], words[index + 1]["end"]) if index + 1 < len(words) else word["end"]
        voiced = np.nonzero(levels[first:round(limit / VOICE_HOP_S)] >= VOICE_PRESENT_DB)[0]
        if voiced.size == 0:
            continue
        start = round(max(word["start"], (first + int(voiced[0])) * VOICE_HOP_S - VOICE_PREROLL_S), 3)
        if start > word["start"]:
            word["start"] = start
            word["end"] = round(max(word["end"], start + MIN_WORD_S), 3)
            word["snapped"] = True
            moved += 1
    return moved


def voiced_frames(levels: np.ndarray, low: float, high: float, after: bool, before: bool) -> Optional[np.ndarray]:
    """Where words Whisper missed were sung: the sung frames between the matched words around them. With a matched
    word only after them, the phrase that ends nearest it; only before them, the phrase that starts nearest it."""
    first = max(0, round(low / VOICE_HOP_S))
    frames = np.nonzero(levels[first:max(first, round(high / VOICE_HOP_S))] >= VOICE_PRESENT_DB)[0] + first
    if frames.size == 0:
        return None
    if before and after:
        return frames
    phrases = np.split(frames, np.nonzero(np.diff(frames) * VOICE_HOP_S > VOICE_PHRASE_GAP_S)[0] + 1)
    return phrases[-1] if after else phrases[0]


def detect_lyrics_language(metadata: Dict[str, Any], lyrics: str) -> Optional[str]:
    params = metadata.get("generation_params") or {}
    derived = metadata.get("derived_tags") or {}
    for candidate in (metadata.get("language"), metadata.get("lyrics_language"),
                      params.get("language"), derived.get("language")):
        code = normalize_language_code(candidate)
        if code:
            return code

    letters = [ch for ch in lyrics if ch.isalpha()]
    if len(letters) < 20:
        return None
    latin = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    if latin / len(letters) < 0.6:
        return None
    tokens = normalize_text(lyrics).split()
    if not tokens:
        return None
    hits = sum(1 for token in tokens if token in ENGLISH_MARKERS)
    return "en" if hits / len(tokens) >= ENGLISH_RATIO_THRESHOLD else None


class LyricalTimestampService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.model_name = settings.LYRIC_WHISPER_MODEL
        self.model_loaded = False
        self.device = None
        self.lyric_timestamps_dir = settings.LYRIC_TIMESTAMPS_DIR
        self.lock = asyncio.Lock()
        self._initialized = True

    async def _load_model_attempt(self, device: str, compute_type: str, download_root: Optional[str] = None):
        from faster_whisper import WhisperModel
        log_service.upscaling(f"Whisper: Loading {self.model_name} model...")
        log_service.upscaling(f"  Device: {device}, Compute: {compute_type}")

        self.model = await asyncio.to_thread(
            WhisperModel,
            self.model_name,
            device=device,
            compute_type=compute_type,
            download_root=download_root
        )
        self.device = device
        self.model_loaded = True
        log_service.upscaling(f"Whisper model loaded and ready ({self.model_name})")

    async def initialize(self):
        if self.model_loaded:
            log_service.upscaling("Whisper already loaded")
            return

        self.lyric_timestamps_dir = settings.LYRIC_TIMESTAMPS_DIR

        if torch.cuda.is_available():
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")

        try:
            await self._load_model_attempt(device="cuda", compute_type=settings.WHISPER_COMPUTE_TYPE)
        except Exception as e:
            log_service.error(f"Whisper GPU load failed: {str(e)}")
            log_service.upscaling("Falling back to CPU...")
            try:
                await self._load_model_attempt(device="cpu", compute_type="int8")
            except Exception as cpu_error:
                log_service.error(f"Whisper CPU load failed: {str(cpu_error)}")
                self.model_loaded = False

    @staticmethod
    def _parse_lyrics_from_metadata(metadata: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            prompt = track_asset_stages.track_lyrics_text(metadata)
            if not prompt:
                return "", []

            sections = [
                {"type": match.group(1).lower(), "position": match.start(), "marker": match.group(0)}
                for match in SECTION_PATTERN.finditer(prompt)
            ]

            return track_asset_stages.clean_lyrics_text(prompt), sections

        except Exception as e:
            log_service.warning(f"Failed to parse lyrics from metadata: {e}")
            return "", []

    @staticmethod
    def _normalize_text(text: str) -> str:
        return normalize_text(text)

    @staticmethod
    def _lyric_lines(ground_truth_lyrics: str) -> List[Tuple[str, List[str]]]:
        lines = []
        for raw_line in ground_truth_lyrics.split('\n'):
            text = raw_line.strip()
            tokens = normalize_text(text).split() if text else []
            if tokens:
                lines.append((text, tokens))
        return lines

    @staticmethod
    def _whisper_tokens(whisper_words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tokens = []
        for word in whisper_words:
            parts = normalize_text(word.get("word", "")).split()
            if not parts:
                continue
            start = float(word.get("start", 0.0))
            end = max(start, float(word.get("end", start)))
            step = (end - start) / len(parts)
            for index, part in enumerate(parts):
                tokens.append({
                    "text": part,
                    "start": start + index * step,
                    "end": start + (index + 1) * step,
                    "confidence": float(word.get("confidence", 0.9)),
                })
        return tokens

    @staticmethod
    def _similarity(a: str, b: str, cache: Dict[Tuple[str, str], float]) -> float:
        if a == b:
            return 1.0
        key = (a, b)
        value = cache.get(key)
        if value is None:
            if abs(len(a) - len(b)) > max(2, min(len(a), len(b)) // 2):
                value = 0.0
            else:
                value = SequenceMatcher(None, a, b).ratio()
            cache[key] = value
        return value

    @staticmethod
    def _align_tokens_fast(hyp: List[str], ref: List[str]) -> List[Tuple[Optional[int], float]]:
        mapping: List[Tuple[Optional[int], float]] = [(None, 0.0)] * len(ref)
        matcher = SequenceMatcher(None, hyp, ref, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for offset in range(i2 - i1):
                    mapping[j1 + offset] = (i1 + offset, 1.0)
            elif tag == 'replace' and (i2 - i1) == (j2 - j1):
                for offset in range(i2 - i1):
                    mapping[j1 + offset] = (i1 + offset, 0.0)
        return mapping

    def _align_tokens(self, hyp: List[str], ref: List[str]) -> List[Tuple[Optional[int], float]]:
        n, m = len(hyp), len(ref)
        if n == 0 or m == 0:
            return [(None, 0.0)] * m
        if n * m > MAX_DP_CELLS:
            return self._align_tokens_fast(hyp, ref)
        reversed_mapping = self._align_tokens_core(hyp[::-1], ref[::-1])
        return [
            (None if hyp_index is None else n - 1 - hyp_index, sim)
            for hyp_index, sim in reversed_mapping[::-1]
        ]

    def _align_tokens_core(self, hyp: List[str], ref: List[str]) -> List[Tuple[Optional[int], float]]:
        n, m = len(hyp), len(ref)
        cache: Dict[Tuple[str, str], float] = {}
        similarity = self._similarity
        previous = [j * GAP_SCORE for j in range(m + 1)]
        moves = [bytearray([2]) * (m + 1)]
        last_column = [previous[m]]

        for i in range(1, n + 1):
            current = [0.0] * (m + 1)
            row = bytearray(m + 1)
            row[0] = 1
            token = hyp[i - 1]
            for j in range(1, m + 1):
                sim = similarity(token, ref[j - 1], cache)
                if sim == 1.0:
                    substitution = MATCH_SCORE
                elif sim >= FUZZY_THRESHOLD:
                    substitution = FUZZY_SCORE
                else:
                    substitution = MISMATCH_SCORE
                best = previous[j - 1] + substitution
                move = 0
                up = previous[j] + GAP_SCORE
                if up > best:
                    best, move = up, 1
                left = current[j - 1] + GAP_SCORE
                if left > best:
                    best, move = left, 2
                current[j] = best
                row[j] = move
            moves.append(row)
            last_column.append(current[m])
            previous = current

        i = max(range(n + 1), key=lambda index: (last_column[index], -index))
        j = m
        mapping: List[Tuple[Optional[int], float]] = [(None, 0.0)] * m
        while j > 0:
            move = moves[i][j] if i > 0 else 2
            if move == 0:
                mapping[j - 1] = (i - 1, similarity(hyp[i - 1], ref[j - 1], cache))
                i -= 1
                j -= 1
            elif move == 1:
                i -= 1
            else:
                j -= 1
        return mapping

    @staticmethod
    def _interpolate_gaps(words: List[Optional[Dict[str, Any]]], ref_tokens: List[str],
                          duration: Optional[float], levels: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
        total = len(words)
        index = 0
        while index < total:
            if words[index] is not None:
                index += 1
                continue
            run_end = index
            while run_end < total and words[run_end] is None:
                run_end += 1
            count = run_end - index
            previous_end = words[index - 1]["end"] if index > 0 else None
            next_start = words[run_end]["start"] if run_end < total else None

            if previous_end is None and next_start is None:
                return []
            voiced = None
            if levels is not None:
                voiced = voiced_frames(levels, previous_end or 0.0,
                                       next_start if next_start is not None else len(levels) * VOICE_HOP_S,
                                       after=next_start is not None, before=previous_end is not None)
            if voiced is not None:
                for offset in range(count):
                    low, high = offset * len(voiced) // count, (offset + 1) * len(voiced) // count
                    words[index + offset] = {
                        "word": ref_tokens[index + offset],
                        "start": round(voiced[low] * VOICE_HOP_S, 3),
                        "end": round((voiced[max(low, high - 1)] + 1) * VOICE_HOP_S, 3),
                        "confidence": 0.3,
                        "aligned": True
                    }
                index = run_end
                continue
            if previous_end is None:
                span_end = next_start
                span_start = max(0.0, next_start - count * INTERPOLATED_WORD_S)
            elif next_start is None:
                span_start = previous_end
                span_end = previous_end + count * INTERPOLATED_WORD_S
                if duration and duration > span_start:
                    span_end = min(span_end, duration)
            else:
                span_end = max(previous_end, next_start)
                span_start = max(previous_end, span_end - count * INTERPOLATED_WORD_S * 2)

            step = max(0.0, span_end - span_start) / count
            for offset in range(count):
                words[index + offset] = {
                    "word": ref_tokens[index + offset],
                    "start": round(span_start + offset * step, 3),
                    "end": round(span_start + (offset + 1) * step, 3),
                    "confidence": 0.3,
                    "aligned": True
                }
            index = run_end
        return [word for word in words if word is not None]

    @staticmethod
    def _split_words(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        for word in words:
            if current:
                too_many = len(current) >= MAX_LINE_WORDS
                too_long = word["end"] - current[0]["start"] > MAX_LINE_DURATION_S
                paused = word["start"] - current[-1]["end"] > MAX_WORD_GAP_S
                if too_many or too_long or paused:
                    chunks.append(current)
                    current = []
            current.append(word)
        if current:
            chunks.append(current)
        return chunks

    def _lines_from_chunks(self, chunks: List[List[Dict[str, Any]]], text: Optional[str] = None) -> List[Dict[str, Any]]:
        lines = []
        for chunk in chunks:
            lines.append({
                "line": text if text is not None and len(chunks) == 1 else " ".join(w["word"] for w in chunk),
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "words": chunk
            })
        return lines

    def _align_lyrics(
        self,
        whisper_words: List[Dict[str, Any]],
        ground_truth_lyrics: str,
        duration: Optional[float] = None,
        levels: Optional[np.ndarray] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
        lines = self._lyric_lines(ground_truth_lyrics)
        hyp_tokens = self._whisper_tokens(whisper_words)
        ref_tokens = [token for _text, tokens in lines for token in tokens]
        if not hyp_tokens or not ref_tokens:
            return [], [], 0

        mapping = self._align_tokens([t["text"] for t in hyp_tokens], ref_tokens)
        matched = sum(1 for hyp_index, sim in mapping if hyp_index is not None and sim >= FUZZY_THRESHOLD)
        if matched < max(1, int(MIN_ALIGNED_RATIO * len(ref_tokens))) and len(hyp_tokens) >= 10:
            return [], [], 0

        words: List[Optional[Dict[str, Any]]] = [None] * len(ref_tokens)
        for ref_index, (hyp_index, sim) in enumerate(mapping):
            if hyp_index is None:
                continue
            token = hyp_tokens[hyp_index]
            word = {
                "word": ref_tokens[ref_index],
                "start": round(token["start"], 3),
                "end": round(token["end"], 3),
                "confidence": round(token["confidence"] if sim >= FUZZY_THRESHOLD else min(token["confidence"], 0.5), 3),
            }
            if sim < 1.0:
                word["aligned"] = True
            words[ref_index] = word

        aligned_words = self._interpolate_gaps(words, ref_tokens, duration, levels)
        if not aligned_words:
            return [], [], 0
        if levels is not None:
            snap_to_voice(aligned_words, levels)

        grouped: List[Dict[str, Any]] = []
        offset = 0
        for text, tokens in lines:
            line_words = aligned_words[offset:offset + len(tokens)]
            offset += len(tokens)
            grouped.extend(self._lines_from_chunks(self._split_words(line_words), text))

        exact = sum(1 for hyp_index, sim in mapping if hyp_index is not None and sim == 1.0)
        return aligned_words, grouped, exact

    def _whisper_only_lines(self, whisper_words: List[Dict[str, Any]],
                            levels: Optional[np.ndarray] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        words = [
            {"word": t["text"], "start": round(t["start"], 3), "end": round(t["end"], 3), "confidence": round(t["confidence"], 3)}
            for t in self._whisper_tokens(whisper_words)
        ]
        if levels is not None:
            snap_to_voice(words, levels)
        return words, self._lines_from_chunks(self._split_words(words))

    def _create_placeholder_timestamps(
        self,
        track_id: str,
        sections: List[Dict[str, Any]],
        is_instrumental: bool
    ) -> Dict[str, Any]:
        return {
            "id": track_id,
            "duration": 0.0,
            "sync_version": SYNC_VERSION,
            "model": self.model_name,
            "confidence": 0.0,
            "alignment_score": 0.0,
            "word_count": 0,
            "line_count": 0,
            "lyrics": [],
            "raw_words": [],
            "sections": sections,
            "instrumental": is_instrumental
        }

    def _multilingual_model(self) -> Tuple[Any, Optional[str]]:
        try:
            from services.whisper_dual_service import whisper_dual_service
        except Exception:
            return None, None
        model = getattr(whisper_dual_service, "quality_model", None)
        name = settings.WHISPER_QUALITY_MODEL
        if model is None or name.endswith(".en"):
            return None, None
        return model, name

    def _select_model(self, language: Optional[str]) -> Tuple[Any, Optional[str], Optional[str]]:
        if not self.model_name.endswith(".en"):
            return self.model, self.model_name, language
        if language == "en":
            return self.model, self.model_name, "en"
        multilingual, multilingual_name = self._multilingual_model()
        if multilingual is not None:
            return multilingual, multilingual_name, language
        if language is None:
            return self.model, self.model_name, "en"
        return None, None, language

    def _transcription_source(self, track_id: str, audio_path: Optional[Path]) -> Tuple[Optional[Path], str]:
        if settings.LYRIC_PREFER_VOCAL_STEM:
            stem = track_asset_stages.demucs_vocal_stem(track_id)
            if stem is not None:
                return stem, "vocal_stem"
        for candidate in (audio_path, track_asset_stages.master_wav_path(track_id)):
            if candidate is not None and candidate.exists():
                return candidate, "mix"
        return None, "missing"

    @staticmethod
    def _transcribe_sync(model, audio_path: Path, language: Optional[str]) -> Tuple[Any, Any]:
        options = {
            "language": language,
            "word_timestamps": True,
            "beam_size": 5,
            "temperature": 0.0,
            "vad_filter": False,
            "condition_on_previous_text": False,
        }
        if language is None:
            options["language_detection_segments"] = LANGUAGE_DETECTION_SEGMENTS
        segments, info = model.transcribe(str(audio_path), **options)
        return list(segments), info

    def _build_result(
        self,
        track_id: str,
        whisper_words: List[Dict[str, Any]],
        ground_truth_lyrics: str,
        sections: List[Dict[str, Any]],
        duration: float,
        model_name: str,
        language: Optional[str],
        audio_source: str,
        levels: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        aligned_words, lines, exact = self._align_lyrics(whisper_words, ground_truth_lyrics, duration, levels)
        alignment = "lyrics"
        if not aligned_words:
            log_service.warning(f"Whisper: Lyrics did not align for {track_id[:8]}, using transcription")
            aligned_words, lines = self._whisper_only_lines(whisper_words, levels)
            alignment = "whisper_only"
            exact = 0

        avg_confidence = sum(w.get("confidence", 0.5) for w in aligned_words) / len(aligned_words) if aligned_words else 0.0
        alignment_score = exact / len(aligned_words) if aligned_words else 0.0

        return {
            "id": track_id,
            "duration": round(duration, 3),
            "sync_version": SYNC_VERSION,
            "model": model_name,
            "confidence": round(avg_confidence, 3),
            "alignment_score": round(alignment_score, 3),
            "word_count": len(aligned_words),
            "line_count": len(lines),
            "lyrics": lines,
            "raw_words": aligned_words,
            "sections": sections,
            "language": language,
            "audio_source": audio_source,
            "voice_snapped": sum(1 for word in aligned_words if word.get("snapped")),
            "alignment": alignment
        }

    async def generate_timestamps(
        self,
        track_id: str,
        metadata: Dict[str, Any],
        audio_path: Optional[Path] = None,
        save: bool = True
    ) -> Optional[Dict[str, Any]]:
        is_instrumental = track_asset_stages.track_is_instrumental(metadata)
        ground_truth_lyrics, sections = self._parse_lyrics_from_metadata(metadata)

        if is_instrumental or not ground_truth_lyrics:
            status_msg = "instrumental track" if is_instrumental else "no lyrics found"
            log_service.upscaling(f"Whisper: Skipping {track_id[:8]} ({status_msg})")
            placeholder = self._create_placeholder_timestamps(track_id, sections, is_instrumental)
            if save:
                await self._save_timestamps(placeholder, track_id)
            return placeholder

        async with self.lock:
            if not self.model_loaded:
                log_service.error("Whisper model not loaded")
                return None

            language = detect_lyrics_language(metadata, ground_truth_lyrics)
            model, model_name, transcribe_language = self._select_model(language)
            if model is None:
                log_service.warning(f"Whisper: No multilingual model for '{language}' lyrics ({track_id[:8]})")
                return None

            source, source_kind = await asyncio.to_thread(self._transcription_source, track_id, audio_path)
            if source is None:
                log_service.error(f"Whisper: Audio not found for {track_id[:8]}")
                return None

            log_service.upscaling(
                f"Whisper: Processing {source.name} ({source_kind}, {model_name}, "
                f"language={transcribe_language or 'auto'}, lyrics={len(ground_truth_lyrics)} chars)"
            )

            try:
                async with gpu_lease("Whisper lyrics"):
                    segments, info = await asyncio.to_thread(self._transcribe_sync, model, source, transcribe_language)
            except Exception as e:
                log_service.error(f"Whisper failed for {track_id[:8]}: {str(e)}")
                _clear_cuda_cache()
                raise_if_cuda_oom(e, "Whisper lyrics")
                return None
            _clear_cuda_cache()

            whisper_words = []
            for segment in segments:
                for word in (getattr(segment, 'words', None) or []):
                    whisper_words.append({
                        "word": word.word.strip(),
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                        "confidence": round(word.probability, 3) if hasattr(word, 'probability') else 0.9
                    })

            if not whisper_words:
                log_service.upscaling(f"Whisper: No words detected for {track_id[:8]}")
                return None

            levels = await asyncio.to_thread(voice_levels, source) if source_kind == "vocal_stem" else None

            detected_language = transcribe_language or getattr(info, "language", None)
            result = await asyncio.to_thread(
                self._build_result, track_id, whisper_words, ground_truth_lyrics, sections,
                float(getattr(info, "duration", 0.0) or 0.0), model_name, detected_language, source_kind, levels
            )

        if save:
            await self._save_timestamps(result, track_id)

        log_service.upscaling(
            f"Whisper complete: {result['line_count']} lines, {result['word_count']} words, "
            f"confidence: {result['confidence']:.0%}, alignment: {result['alignment_score']:.0%} ({result['alignment']}), "
            f"{result['audio_source']}, {result['voice_snapped']} start(s) moved to the voice"
        )
        return result

    async def _save_timestamps(self, timestamps: Dict[str, Any], track_id: str) -> Path:
        filepath = self.lyric_timestamps_dir / f"{track_id}.json"
        temp_path = filepath.with_name(f"{track_id}.json.tmp")
        try:
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(timestamps, indent=2))
            await asyncio.to_thread(os.replace, temp_path, filepath)
            return filepath
        except Exception as e:
            log_service.error(f"Whisper: Save failed: {e}")
            raise

    @staticmethod
    def _read_json(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def load_timestamps(self, track_id: str) -> Optional[Dict[str, Any]]:
        filepath = self.lyric_timestamps_dir / f"{track_id}.json"

        try:
            return await asyncio.to_thread(self._read_json, filepath)

        except FileNotFoundError:
            return None
        except Exception as e:
            log_service.error(f"Whisper: Load failed: {e}")
            return None

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None

        _clear_cuda_cache()
        self.model_loaded = False
        log_service.upscaling("Whisper model unloaded")

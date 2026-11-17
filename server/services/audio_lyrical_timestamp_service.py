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
from services import lyric_style_service, track_asset_stages
from services.lyric_sections import SECTION_PATTERN
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom

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
SYNC_VERSION = "1.2"
VOICE_HOP_S = 0.01
VOICE_REFERENCE_PERCENTILE = 95
VOICE_AUDIBLE_DB = -80.0
VOICE_SILENT_DB = -40.0
VOICE_PRESENT_DB = -30.0
VOICE_PROBE_S = 0.06
VOICE_PREROLL_S = 0.02
VOICE_PHRASE_GAP_S = 0.6
VOICE_RISE_DB = 12.0
VOICE_PAUSE_S = 0.25
VOICE_ONSET_S = 0.1
VOICE_ONSET_NEAR_S = 0.15
VOICE_TAIL_S = 1.0
VOICE_DECAY_DB = 6.0
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


def voice_onsets(levels: np.ndarray) -> np.ndarray:
    """Frames where the voice comes back after a pause: at least VOICE_PAUSE_S at VOICE_RISE_DB or more under the
    level the voice reaches within VOICE_ONSET_S, and that level loud enough to be singing. The dips of consonants
    inside a word are shorter than a pause, the fading tail of a sung word is not a rise."""
    ahead = round(VOICE_ONSET_S / VOICE_HOP_S)
    behind = round(VOICE_PAUSE_S / VOICE_HOP_S)
    if levels.size <= behind:
        return np.zeros(0, dtype=int)
    windows = np.lib.stride_tricks.sliding_window_view
    peak = windows(np.concatenate([levels, np.full(ahead - 1, -np.inf)]), ahead).max(axis=1)
    before = np.concatenate([np.full(behind, np.inf), windows(levels, behind).max(axis=1)[:-1]])
    floor = peak - VOICE_RISE_DB
    rising = np.nonzero((peak >= VOICE_PRESENT_DB) & (levels > floor) & (before <= floor))[0]
    return np.array([frame + int(np.argmax(levels[frame:frame + ahead] >= VOICE_PRESENT_DB)) for frame in rising], dtype=int)


def _pause_start(levels: np.ndarray, onset: int) -> int:
    floor = max(float(levels[onset:onset + round(VOICE_ONSET_S / VOICE_HOP_S)].max()) - VOICE_RISE_DB, VOICE_PRESENT_DB)
    frame = onset
    while frame > 0 and levels[frame - 1] <= floor:
        frame -= 1
    return frame


def snap_to_voice(words: List[Dict[str, Any]], levels: np.ndarray, line_starts: Optional[List[bool]] = None,
                  after_missed: Optional[List[bool]] = None) -> int:
    """Whisper gives a pause to the word after it, so a word sung after a pause often starts where the word before it
    ended, and can stay on screen through the whole gap. A word whose own span holds a pause and the voice coming
    back starts where the voice comes back (voice_onsets). A word that starts in silence, and the first word of a
    lyric line that does not start where a voice comes in (line_starts), may move to where the voice comes back by
    the end of the next word: one Whisper heard in silence lands with the next sung word, and a line is sung with its
    first word, which Whisper sometimes puts on the end of the line before. A word
    moves only when what sounds at its start is silence or the fading end of the singing before it (no louder than
    VOICE_DECAY_DB over its start, at most VOICE_TAIL_S), never a word being sung, unless the lyric words just before
    it were missed (after_missed): then what sounds there can be those words. Moves are decided from the last word
    back, so a word follows the next one when that one moves; then words are put back in order."""
    onsets = voice_onsets(levels)
    near = round(VOICE_ONSET_NEAR_S / VOICE_HOP_S)
    probe = round(VOICE_PROBE_S / VOICE_HOP_S)
    moved = 0
    for index in reversed(range(len(words))):
        word = words[index]
        first = round(word["start"] / VOICE_HOP_S)
        bound = word["end"] - MIN_WORD_S
        if index + 1 < len(words):
            silent = float(np.median(levels[first:first + probe])) < VOICE_SILENT_DB if first < levels.size else True
            opens_line = bool(line_starts and line_starts[index]) and not np.any(np.abs(onsets - first) <= near)
            if silent or opens_line:
                bound = max(bound, words[index + 1]["end"])
        later = onsets[(onsets > first) & (onsets <= round(bound / VOICE_HOP_S))]
        if later.size:
            pause = _pause_start(levels, int(later[0]))
            sounding = levels[first:pause]
            fading = sounding.size == 0 or bool(after_missed and after_missed[index]) or (
                sounding.size * VOICE_HOP_S <= VOICE_TAIL_S
                and float(sounding.max()) <= float(np.median(levels[first:first + probe])) + VOICE_DECAY_DB)
            if fading:
                word["start"] = round(int(later[0]) * VOICE_HOP_S - VOICE_PREROLL_S, 3)
                word["end"] = round(max(word["end"], word["start"] + MIN_WORD_S), 3)
                word["snapped"] = True
                moved += 1
    floor = 0.0
    for word in words:
        if word["start"] < floor:
            word["start"] = round(floor, 3)
            word["snapped"] = True
        word["end"] = round(max(word["end"], word["start"] + MIN_WORD_S), 3)
        floor = word["end"] if word.get("snapped") else word["start"]
    return moved


def sung_phrases(levels: np.ndarray, low: float, high: float) -> List[np.ndarray]:
    """The sung frames between two moments, split into phrases where the voice stops for over VOICE_PHRASE_GAP_S;
    blips shorter than a word are left out."""
    first = max(0, round(low / VOICE_HOP_S))
    frames = np.nonzero(levels[first:max(first, round(high / VOICE_HOP_S))] >= VOICE_PRESENT_DB)[0] + first
    if frames.size == 0:
        return []
    phrases = np.split(frames, np.nonzero(np.diff(frames) * VOICE_HOP_S > VOICE_PHRASE_GAP_S)[0] + 1)
    return [phrase for phrase in phrases if phrase.size * VOICE_HOP_S >= MIN_WORD_S]


def place_missed_lines(groups: List[Tuple[str, List[int]]], phrases: List[np.ndarray],
                       ringing: bool) -> List[Tuple[List[int], np.ndarray]]:
    """Where words Whisper missed were sung, line by line: words from the line of the matched word before them go in
    the first phrase after it, words from the line of the matched word after them in the last phrase before it, and
    whole lines in between share the phrases left evenly, in order; each line takes from the start of its share only
    as many phrases as its words need (INTERPOLATED_WORD_S a word), so ad-libs between lines stay empty. With no
    words from the line before, a first phrase still ringing from the word before (ringing) is that word's own note.
    Returns the words with the sung frames to spread them over."""
    count = len(phrases)
    lead = groups[0][1] if groups and groups[0][0] == "lead" else None
    tail = groups[-1][1] if groups and groups[-1][0] == "tail" else None
    middle = [words for kind, words in groups if kind == "middle"]
    low = 1 if lead is not None or (middle and ringing and count > 1) else 0
    high = count - 1 if tail is not None else count
    spans: List[Tuple[List[int], List[int]]] = []
    if lead is not None:
        spans.append((lead, [0]))
    room = high - low
    for position, words in enumerate(middle):
        if room <= 0:
            spans.append((words, [count - 1] if tail is not None else [0] if lead is not None else list(range(count))))
            continue
        first = low + position * room // len(middle)
        share = range(first, max(first + 1, low + (position + 1) * room // len(middle)))
        taken: List[int] = []
        for index in share:
            taken.append(index)
            if sum(phrases[k].size for k in taken) * VOICE_HOP_S >= len(words) * INTERPOLATED_WORD_S:
                break
        spans.append((words, taken))
    if tail is not None:
        spans.append((tail, [count - 1]))
    blocks: List[Tuple[List[int], List[int]]] = []
    for words, indexes in spans:
        if blocks and set(indexes) & set(blocks[-1][1]):
            blocks[-1] = (blocks[-1][0] + words, sorted(set(blocks[-1][1]) | set(indexes)))
        else:
            blocks.append((list(words), indexes))
    return [(words, np.concatenate([phrases[index] for index in indexes])) for words, indexes in blocks if words]


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
    def _missed_groups(index: int, run_end: int, line_of: List[int]) -> List[Tuple[str, List[int]]]:
        before = line_of[index - 1] if index > 0 else -1
        after = line_of[run_end] if run_end < len(line_of) else -1
        groups: List[Tuple[str, List[int]]] = []
        for line in dict.fromkeys(line_of[index:run_end]):
            members = [k for k in range(index, run_end) if line_of[k] == line]
            kind = "middle" if before == after else "lead" if line == before else "tail" if line == after else "middle"
            groups.append((kind, members))
        return groups

    @classmethod
    def _interpolate_gaps(cls, words: List[Optional[Dict[str, Any]]], ref_tokens: List[str],
                          duration: Optional[float], levels: Optional[np.ndarray] = None,
                          line_of: Optional[List[int]] = None) -> List[Dict[str, Any]]:
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
            phrases: List[np.ndarray] = []
            if levels is not None and line_of is not None:
                phrases = sung_phrases(levels, previous_end or 0.0,
                                       next_start if next_start is not None else len(levels) * VOICE_HOP_S)
            if phrases:
                ringing = previous_end is not None and int(phrases[0][0]) <= round(previous_end / VOICE_HOP_S) + 1
                groups = cls._missed_groups(index, run_end, line_of)
                for members, frames in place_missed_lines(groups, phrases, ringing):
                    breaks = np.nonzero(np.diff(frames) * VOICE_HOP_S > VOICE_PHRASE_GAP_S)[0]
                    for offset, ref_index in enumerate(members):
                        low, high = offset * len(frames) // len(members), (offset + 1) * len(frames) // len(members)
                        last = max(low, high - 1)
                        cut = breaks[breaks >= low]
                        if cut.size and cut[0] < last:
                            last = int(cut[0])
                        words[ref_index] = {
                            "word": ref_tokens[ref_index],
                            "start": round(frames[low] * VOICE_HOP_S, 3),
                            "end": round((frames[last] + 1) * VOICE_HOP_S, 3),
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
        line_of = [number for number, (_text, tokens) in enumerate(lines) for _token in tokens]
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

        if levels is not None:
            heard = [ref_index for ref_index, word in enumerate(words) if word is not None]
            line_starts = [
                (ref_index == 0 or line_of[ref_index - 1] != line_of[ref_index])
                and ref_index + 1 < len(words) and words[ref_index + 1] is not None
                and line_of[ref_index + 1] == line_of[ref_index]
                for ref_index in heard
            ]
            after_missed = [ref_index > 0 and words[ref_index - 1] is None for ref_index in heard]
            snap_to_voice([words[ref_index] for ref_index in heard], levels, line_starts, after_missed)
        aligned_words = self._interpolate_gaps(words, ref_tokens, duration, levels, line_of)
        if not aligned_words:
            return [], [], 0

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
            lyric_style_service.schedule_refresh(track_id, metadata, result)

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

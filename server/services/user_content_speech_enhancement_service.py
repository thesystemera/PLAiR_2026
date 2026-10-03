"""
User Content Speech Enhancement Service

Turns a listener's phone recording (shoutout, reply or song review) into clean, snappy radio audio.

Processing Pipeline:
1. Decode the upload at its native 48 kHz (browsers record Opus at 48 kHz; nothing is thrown away)
2. MossFormer2_SE_48K speech enhancement (removes background music and noise; 4 s windows, bounded memory)
3. 75 Hz low cut (handling rumble and wind)
4. The LLM (LLM_BACKGROUND chain) filters the transcript: process talk, stumbles and false starts go
5. Cut to the kept words (Whisper word timestamps) and shorten long pauses found by Silero VAD
6. Loudness normalise (-16 LUFS, the station level, -1 dBFS peak ceiling) and write MP3
7. Reviews: the LLM's best short line is cut out as a separate sting clip for playing over the song

No compression here: on air the DJ broadcast chain in the client compresses and limits every voice.
Measured on real uploads with Audiobox Aesthetics (2026-09-29): raw PQ 5.24 / PC 4.57, the old
16 kHz DeepFilterNet + super-resolution chain PQ 4.24 / PC 2.38, this chain PQ 5.68 / PC 1.82.
"""

import asyncio
import subprocess
import threading
import torch
import torchaudio
import traceback
import gc
import json
import aiofiles
import os
import difflib
import numpy as np
import pyloudnorm as pyln
from scipy.ndimage import maximum_filter1d, minimum_filter1d
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field
from services.audio_clearvoice_service import ClearVoice
from services.audio_master_service import MASTER_TARGET_LUFS

from services import log_service
from services.llm_router import LLM_BACKGROUND
from services.user_content_database_service import coarse_location
from models_global import gpu_lease
from config import settings
from config.settings import BASE_DIR


class TranscriptionMetadata(BaseModel):
    total_words: int
    language: str
    language_probability: float
    category: Optional[str] = None
    urgency_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    urgency_label: Optional[str] = None
    importance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    importance_label: Optional[str] = None
    tags: Optional[List[str]] = None
    location_relevant: Optional[bool] = None
    time_sensitive: Optional[bool] = None
    target_audience: Optional[str] = None
    quality_rating: int = Field(..., ge=1, le=5)
    sentiment: Optional[str] = None
    about_place: Optional[str] = None
    sting_quote: Optional[str] = None


class ShoutoutOpinionResponse(BaseModel):
    full_transcription: str
    transcription_metadata: TranscriptionMetadata


PLACE_RULES = """PLACE:
                    - about_place: the real place the message is about, as specific as the speaker makes it, written so a
                      map search finds it ("Karangahape Road, Auckland, New Zealand", "Ponsonby, Auckland, New Zealand").
                      Resolve "my street", "round here" or "down the road" against the given location at the level they
                      imply. Use null when the message isn't about a place."""

SHOUTOUT_PROMPT = {
    "system": "You are a LOSSLESS FILTER for radio. Your goal is to delete 'Process Talk' but keep the exact 'Message'.",
    "prompt": """Filter this listener message. You must keep the original wording exactly as is, only deleting specific segments.
                    If "replying_to" is given, the message is a reply to that shoutout: keep everything that answers it.

                    STRICT RULES:
                    1. DO NOT REWRITE. DO NOT FIX GRAMMAR. If they say "me and him went," KEEP IT.
                    2. REMOVE "Process Talk": Phrases *about* the recording or the app (e.g., "Can I get a shoutout?",
                       "Is this on?", "Hey DJ, save this", "reply to that one").
                    3. KEEP "Conversational Greetings": "Hey guys", "Hi everyone", "Yo bro" - these are ESSENTIAL.
                    4. REMOVE Stumbles: Stutters, false starts, and dead air.

                    CATEGORIZATION:
                    - Category: Short descriptive topic (e.g., "birthday_wishes")
                    - Urgency (0.0-1.0): 1.0 = Emergency, 0.5 = Event Soon, 0.0 = Casual
                    - Importance (0.0-1.0): 1.0 = Citywide, 0.5 = Local, 0.0 = Personal
                    - Sentiment: positive, negative, mixed or neutral

                    """ + PLACE_RULES + """

                    Input JSON:
                    {text}

                    Return EXACTLY this JSON structure (do not repeat user_data):
                    {{
                        "full_transcription": "The filtered text (must match original words)",
                        "transcription_metadata": {{
                            "total_words": 123,
                            "language": "en",
                            "language_probability": 1.0,
                            "category": "family_greeting",
                            "urgency_score": 0.2,
                            "urgency_label": "casual",
                            "importance_score": 0.1,
                            "importance_label": "personal",
                            "tags": ["tag1", "tag2"],
                            "location_relevant": false,
                            "time_sensitive": false,
                            "target_audience": "personal",
                            "quality_rating": 4,
                            "sentiment": "positive",
                            "about_place": null
                        }}
                    }}"""
}

PROMPT_SETTINGS = {
    "shoutout": SHOUTOUT_PROMPT,
    "reply": SHOUTOUT_PROMPT,
    "review": {
        "system": "You are a LOSSLESS FILTER for radio. Delete 'Process Talk' but keep the listener's exact words about the song.",
        "prompt": """Filter this listener's review of the song given in "track". Keep the original wording exactly, only delete segments.

                    STRICT RULES:
                    1. NO REWRITING. Do not fix grammar. Do not summarize.
                    2. REMOVE "Process Talk": anything *about* saving or recording it ("save this as my review",
                       "hey DJ", "record this", "rate this song").
                    3. REMOVE Stumbles: Stutters (um, uh), false starts, and unintelligible glitches.
                    4. KEEP the reaction, the reasons and the natural tone.

                    STING:
                    - sting_quote: the single best short line, 3 to 12 words, copied EXACTLY and contiguously from the
                      filtered text, that works on its own when played over this song on air ("oh my god I love this
                      song", "this is my summer anthem"). Use null when no line stands on its own, or when the review
                      is negative, rude or not fit for air.

                    CATEGORIZATION:
                    - Category: short descriptive topic (e.g., "loves_the_chorus", "gym_anthem")
                    - Sentiment: positive, negative, mixed or neutral
                    - Tags: what they talk about (vocals, beat, lyrics, memories, mood...)

                    """ + PLACE_RULES + """

                    Input JSON:
                    {text}

                    Return EXACTLY this JSON structure (do not repeat user_data):
                    {{
                        "full_transcription": "The filtered text (must be exact original words)",
                        "transcription_metadata": {{
                            "total_words": 123,
                            "language": "en",
                            "language_probability": 1.0,
                            "category": "loves_the_chorus",
                            "tags": ["chorus", "summer"],
                            "quality_rating": 4,
                            "sentiment": "positive",
                            "sting_quote": "oh my god I love this song",
                            "about_place": null
                        }}
                    }}"""
    },
}

ENHANCED_SAMPLE_RATE = 48000
SE_DECODE_WINDOW_S = 4.0
LOW_CUT_HZ = 75.0
PEAK_CEILING = 10 ** (-1.0 / 20)
LIMITER_WINDOW_S = 0.02
LIMITER_MAX_REDUCTION_DB = 6.0
VAD_SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.45
VAD_MIN_SILENCE_MS = 200
VAD_SPEECH_PAD_MS = 60
MAX_PAUSE_S = 0.45
WORD_GUARD_S = 0.05
MIN_INTERVAL_S = 0.03
SPAN_MERGE_GAP_S = 0.8
SPAN_PRE_PAD_S = 0.08
SPAN_POST_PAD_S = 0.12
LAST_SPAN_TAIL_S = 0.15
SPAN_FADE_S = 0.02
MIN_KEPT_WORD_RATIO = 0.35
MIN_CLEAN_MATCH_RATIO = 0.8
STING_PRE_PAD_S = 0.06
STING_TAIL_S = 0.2
STING_MIN_WORDS = 2
STING_MAX_S = 6.0


def _default_transcription_metadata(content_type: str) -> dict:
    if content_type == "review":
        return {"category": "song_review", "sentiment": "neutral", "tags": [], "quality_rating": 3, "sting_quote": None}
    return {
        "category": "general",
        "urgency_score": 0.0,
        "urgency_label": "casual",
        "importance_score": 0.0,
        "importance_label": "personal",
        "tags": [],
        "location_relevant": False,
        "time_sensitive": False,
        "target_audience": "general",
        "quality_rating": 3,
    }


def _token(word: str) -> str:
    return "".join(char for char in word.lower() if char.isalnum())


def sting_path_for(mp3_path) -> str:
    base, _ext = os.path.splitext(str(mp3_path))
    return f"{base}_sting.mp3"


async def shoutout_where(metadata: dict, user_data: dict):
    from services_radio import geo
    phrase = metadata.get('about_place') or coarse_location(user_data.get('location'))
    return await geo.resolver.resolve(phrase) if phrase else None


def _subtract(intervals: List[Tuple[float, float]], cuts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    result = []
    for start, end in intervals:
        pieces = [(start, end)]
        for cut_start, cut_end in cuts:
            next_pieces = []
            for a, b in pieces:
                if cut_end <= a or cut_start >= b:
                    next_pieces.append((a, b))
                    continue
                if cut_start > a:
                    next_pieces.append((a, cut_start))
                if cut_end < b:
                    next_pieces.append((cut_end, b))
            pieces = next_pieces
        result.extend(p for p in pieces if p[1] - p[0] >= MIN_INTERVAL_S)
    return result


class UserContentSpeechEnhancementService:
    def __init__(self):
        self.se_model = None
        self.model_loaded = False
        self._gpu_lock = threading.Lock()

    async def initialize(self):
        if self.model_loaded:
            return

        original_cwd = os.getcwd()
        try:
            os.chdir(BASE_DIR)
            log_service.system("Loading speech enhancement (MossFormer2_SE_48K)...")
            self.se_model = ClearVoice(task='speech_enhancement', model_names=['MossFormer2_SE_48K'])
            self.se_model.models[0].args.one_time_decode_length = SE_DECODE_WINDOW_S
            self.model_loaded = True
            log_service.success("✓ Speech enhancement loaded (MossFormer2_SE_48K)")
        except Exception as e:
            log_service.error(f"Failed to load speech enhancement: {str(e)}\n{traceback.format_exc()}")
            self.model_loaded = False
        finally:
            os.chdir(original_cwd)

    @staticmethod
    def decode_audio(input_path: str, sample_rate: int = ENHANCED_SAMPLE_RATE) -> np.ndarray:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")
        if os.path.getsize(input_path) == 0:
            raise ValueError(f"Input file is empty: {input_path}")
        result = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', input_path, '-ac', '1', '-ar', str(sample_rate), '-f', 'f32le', '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg decode failed: {result.stderr.decode(errors='ignore')}")
        return np.frombuffer(result.stdout, np.float32).copy()

    @staticmethod
    def _limit_peaks(signal: np.ndarray, sample_rate: int) -> np.ndarray:
        peak = float(np.abs(signal).max()) if signal.size else 0.0
        if peak <= PEAK_CEILING:
            return signal

        excess_db = 20 * np.log10(peak / PEAK_CEILING) - LIMITER_MAX_REDUCTION_DB
        if excess_db > 0:
            signal = signal * (10 ** (-excess_db / 20))

        window = max(3, int(LIMITER_WINDOW_S * sample_rate))
        magnitude = np.abs(signal) if signal.ndim == 1 else np.abs(signal).max(axis=1)
        envelope = maximum_filter1d(magnitude, size=window)
        required = np.minimum(1.0, PEAK_CEILING / np.maximum(envelope, 1e-9))
        kernel = np.hanning(window + 2)[1:-1]
        gain = np.convolve(minimum_filter1d(required, size=window), kernel / kernel.sum(), mode="same")

        limited = signal * (gain if signal.ndim == 1 else gain[:, np.newaxis])
        final_peak = float(np.abs(limited).max())
        if final_peak > PEAK_CEILING:
            limited = limited * (PEAK_CEILING / final_peak)
        return limited

    def loudness_normalize(self, audio: np.ndarray, sample_rate: int, target_lufs=MASTER_TARGET_LUFS) -> np.ndarray:
        try:
            samples = audio.astype(np.float64)
            loudness = pyln.Meter(sample_rate).integrated_loudness(samples)
            if not np.isfinite(loudness) or loudness <= -70.0:
                return audio
            return self._limit_peaks(pyln.normalize.loudness(samples, loudness, target_lufs), sample_rate).astype(np.float32)
        except Exception as e:
            log_service.error(f"Normalization failed: {e}")
            return audio

    def _enhance_sync(self, audio: np.ndarray) -> np.ndarray:
        if self.se_model is None:
            raise RuntimeError("Speech enhancement not initialized. Call initialize() first.")
        with torch.no_grad():
            restored = self.se_model.call_t2t_mode(audio[np.newaxis, :])
        if restored is None:
            raise RuntimeError("Speech enhancement returned None")
        restored = np.asarray(restored, dtype=np.float32).reshape(-1)[:audio.shape[0]]
        if restored.shape[0] < audio.shape[0]:
            restored = np.pad(restored, (0, audio.shape[0] - restored.shape[0]))
        low_cut = torchaudio.functional.highpass_biquad(torch.from_numpy(restored), ENHANCED_SAMPLE_RATE, LOW_CUT_HZ)
        return low_cut.numpy().astype(np.float32)

    def _enhance_file_sync(self, input_path: str) -> Tuple[np.ndarray, bool]:
        audio = self.decode_audio(input_path)
        try:
            with self._gpu_lock:
                return self._enhance_sync(audio), True
        except Exception as e:
            log_service.error(f"Vocal enhancement failed: {str(e)}\n{traceback.format_exc()}")
            return audio, False
        finally:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    async def _enhance(self, input_path: str) -> Tuple[np.ndarray, bool]:
        async with gpu_lease("Shoutout enhancement"):
            return await asyncio.to_thread(self._enhance_file_sync, input_path)

    @staticmethod
    def _speech_regions(audio: np.ndarray) -> List[Tuple[float, float]]:
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            audio_16k = torchaudio.functional.resample(torch.from_numpy(audio), ENHANCED_SAMPLE_RATE, VAD_SAMPLE_RATE)
            options = VadOptions(threshold=VAD_THRESHOLD, min_silence_duration_ms=VAD_MIN_SILENCE_MS,
                                 speech_pad_ms=VAD_SPEECH_PAD_MS)
            stamps = get_speech_timestamps(audio_16k.numpy(), options, sampling_rate=VAD_SAMPLE_RATE)
            return [(s['start'] / VAD_SAMPLE_RATE, s['end'] / VAD_SAMPLE_RATE) for s in stamps]
        except Exception as e:
            log_service.warning(f"User Content Processing: VAD unavailable ({e}), pauses kept")
            return []

    def _get_keep_spans(self, original_words: List[dict], cleaned_text: str) -> List[dict]:
        cleaned_tokens = [_token(w) for w in cleaned_text.split() if _token(w)]
        original_tokens = [_token(w['word']) for w in original_words]

        matcher = difflib.SequenceMatcher(None, original_tokens, cleaned_tokens, autojunk=False)

        raw_matches = []
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag == 'equal':
                raw_matches.append({
                    "start_time": original_words[i1]['start'],
                    "end_time": original_words[i2 - 1]['end'],
                    "words": list(original_words[i1:i2])
                })

        if not raw_matches:
            return []

        merged_spans = [raw_matches[0]]
        for next_span in raw_matches[1:]:
            current_span = merged_spans[-1]
            if next_span['start_time'] - current_span['end_time'] < SPAN_MERGE_GAP_S:
                current_span['end_time'] = next_span['end_time']
                current_span['words'].extend(next_span['words'])
            else:
                merged_spans.append(next_span)
        return merged_spans

    @staticmethod
    def _spans_are_safe(spans: List[dict], original_words: List[dict], cleaned_text: str) -> bool:
        if not spans or not original_words:
            return False
        kept = sum(len(span['words']) for span in spans)
        cleaned_count = sum(1 for word in cleaned_text.split() if any(ch.isalnum() for ch in word))
        if kept < MIN_KEPT_WORD_RATIO * len(original_words):
            return False
        if cleaned_count and kept < MIN_CLEAN_MATCH_RATIO * cleaned_count:
            return False
        return True

    @staticmethod
    def _keep_intervals(duration: float, words: List[dict], spans: Optional[List[dict]],
                        speech: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        if spans:
            base = []
            last = len(spans) - 1
            for i, span in enumerate(spans):
                start = span['start_time'] - SPAN_PRE_PAD_S
                end = span['end_time'] + (max(SPAN_POST_PAD_S, LAST_SPAN_TAIL_S) if i == last else SPAN_POST_PAD_S)
                if i > 0:
                    start = max(start, (spans[i - 1]['end_time'] + span['start_time']) / 2)
                if i < last:
                    end = min(end, (span['end_time'] + spans[i + 1]['start_time']) / 2)
                base.append((max(0.0, start), min(duration, end)))
        else:
            starts = [s for s, _e in speech[:1]] + [w['start'] for w in words[:1]]
            ends = [e for _s, e in speech[-1:]] + [w['end'] for w in words[-1:]]
            if starts and ends:
                base = [(max(0.0, min(starts) - SPAN_PRE_PAD_S), min(duration, max(ends) + LAST_SPAN_TAIL_S))]
            else:
                base = [(0.0, duration)]

        cuts = []
        half = MAX_PAUSE_S / 2
        for (_s0, gap_start), (gap_end, _e1) in zip(speech, speech[1:]):
            if gap_end - gap_start <= MAX_PAUSE_S:
                continue
            if any(gap_start + WORD_GUARD_S < w['start'] < gap_end - WORD_GUARD_S for w in words):
                continue
            cuts.append((gap_start + half, gap_end - half))
        return _subtract([iv for iv in base if iv[1] > iv[0]], cuts)

    @staticmethod
    def _stitch(audio: np.ndarray, sr: int, intervals: List[Tuple[float, float]],
                words: List[dict]) -> Tuple[np.ndarray, List[dict]]:
        if not intervals:
            return audio, [dict(w) for w in words]

        fade_len = int(SPAN_FADE_S * sr)
        segments = []
        offsets = []
        cursor = 0
        for start, end in intervals:
            a = min(max(0, int(round(start * sr))), audio.shape[0])
            b = min(max(0, int(round(end * sr))), audio.shape[0])
            if b <= a:
                continue
            segment = audio[a:b].copy()
            if segment.shape[0] > 2 * fade_len > 0:
                segment[:fade_len] *= np.linspace(0, 1, fade_len, dtype=np.float32)
                segment[-fade_len:] *= np.linspace(1, 0, fade_len, dtype=np.float32)
            offsets.append((a / sr, b / sr, cursor / sr))
            segments.append(segment)
            cursor += segment.shape[0]

        if not segments:
            return audio, [dict(w) for w in words]

        remapped = []
        for word in words:
            for start, end, offset in offsets:
                if start - 0.01 <= word['start'] < end:
                    new_word = dict(word)
                    new_word['start'] = round(offset + max(0.0, word['start'] - start), 3)
                    new_word['end'] = round(offset + max(0.0, min(word['end'], end) - start), 3)
                    remapped.append(new_word)
                    break
        return np.concatenate(segments), remapped

    @staticmethod
    def _sting_bounds(words: List[dict], quote: Optional[str], duration: float) -> Optional[Tuple[float, float]]:
        quote_tokens = [_token(w) for w in (quote or "").split() if _token(w)]
        if len(quote_tokens) < STING_MIN_WORDS or not words:
            return None
        word_tokens = [_token(w['word']) for w in words]
        matcher = difflib.SequenceMatcher(None, word_tokens, quote_tokens, autojunk=False)
        match = matcher.find_longest_match(0, len(word_tokens), 0, len(quote_tokens))
        if match.size < max(STING_MIN_WORDS, int(0.7 * len(quote_tokens))):
            return None
        first, last = words[match.a], words[match.a + match.size - 1]
        start = max(0.0, first['start'] - STING_PRE_PAD_S)
        end = min(duration, last['end'] + STING_TAIL_S)
        if end - start > STING_MAX_S or end <= start:
            return None
        return start, end

    @staticmethod
    async def _load_json(path) -> Optional[dict]:
        if not path or not os.path.exists(path):
            return None
        try:
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                data = json.loads(await f.read())
            return data if isinstance(data, dict) else None
        except Exception as e:
            log_service.error(f"Failed to read {path}: {e}")
            return None

    @staticmethod
    async def _write_json_atomic(path: str, data: dict):
        temp_path = f"{path}.tmp"
        async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        await asyncio.to_thread(os.replace, temp_path, path)

    async def filter_text(self, text: str, content_type: str, ai_service, context: Optional[dict] = None,
                          location: Optional[str] = None) -> dict:
        try:
            payload = {"transcription": text}
            if location:
                payload["location"] = location
            payload.update({k: v for k, v in (context or {}).items() if v})

            prompt_settings = PROMPT_SETTINGS[content_type]
            prompt = prompt_settings["prompt"].format(text=json.dumps(payload, ensure_ascii=False))

            completion = await ai_service.generate(
                messages=[
                    {"role": "system", "content": prompt_settings["system"]},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                response_schema=ShoutoutOpinionResponse,
                role=LLM_BACKGROUND
            )

            processed_data = getattr(completion, 'structured_data', None) if completion else None
            if processed_data is None and completion:
                try:
                    processed_data = json.loads((completion.choices[0].message.content or "").strip())
                except json.JSONDecodeError as e:
                    log_service.error(f"Failed to parse LLM response as JSON: {e}")
                    return {}

            if not isinstance(processed_data, dict):
                log_service.error("User Content Enhancement: LLM returned no structured transcript")
                return {}

            processed_data = dict(processed_data)
            if not isinstance(processed_data.get('transcription_metadata'), dict):
                processed_data['transcription_metadata'] = {}

            log_service.detail(
                f"User Content Enhancement: Processed {content_type} with category "
                f"'{processed_data['transcription_metadata'].get('category', 'N/A')}'", "user_content")
            return processed_data

        except Exception as e:
            log_service.error(f"Error filtering {content_type} text: {str(e)}\n{traceback.format_exc()}")
            return {}

    async def process_transcription_with_gpt(self, json_path: str, content_type: str, ai_service,
                                             context: Optional[dict] = None) -> dict:
        original_data = await self._load_json(json_path)
        if not original_data:
            return {}
        location = coarse_location((original_data.get("user_data") or {}).get("location"))
        return await self.filter_text(original_data.get("full_transcription", ""), content_type, ai_service,
                                      context=context, location=location)

    @staticmethod
    def _build_transcript(original_data: dict, processed: Optional[dict], content_type: str) -> Tuple[dict, bool]:
        filtered_text = processed.get('full_transcription') if isinstance(processed, dict) else None
        if isinstance(filtered_text, str) and filtered_text.strip():
            transcript = dict(processed)
            transcript['full_transcription'] = filtered_text.strip()
            transcript['transcription_metadata'] = dict(processed.get('transcription_metadata') or {})
            filtered = True
        else:
            original_text = (original_data.get('full_transcription') or '').strip()
            original_meta = original_data.get('transcription_metadata') or {}
            transcript = {
                'full_transcription': original_text,
                'transcription_metadata': {
                    'total_words': len(original_text.split()),
                    'language': original_meta.get('language', 'en'),
                    'language_probability': original_meta.get('language_probability', 1.0),
                    **_default_transcription_metadata(content_type),
                },
            }
            filtered = False
            log_service.warning("User Content Processing: LLM filter unavailable, keeping the original transcription")

        for key in ('user_data', 'timestamp'):
            if key in original_data:
                transcript[key] = original_data[key]
        return transcript, filtered

    def _write_mp3(self, audio: np.ndarray, sr: int, output_path: str):
        temp_mp3 = f"{output_path}.tmp"
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        command = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-f', 's16le', '-ar', str(sr), '-ac', '1', '-i', 'pipe:0',
            '-codec:a', 'libmp3lame', '-qscale:a', '2', '-f', 'mp3', temp_mp3
        ]
        try:
            subprocess.run(command, input=pcm, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.replace(temp_mp3, output_path)
        finally:
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)

    def _finalize_sync(self, enhanced: np.ndarray, sr: int, words: List[dict], spans: Optional[List[dict]],
                       output_path: str, sting_quote: Optional[str]) -> Tuple[float, List[dict], Optional[dict]]:
        speech = self._speech_regions(enhanced)
        duration = enhanced.shape[0] / sr
        kept_words = [w for span in spans for w in span['words']] if spans else list(words)
        intervals = self._keep_intervals(duration, kept_words, spans, speech)
        final_audio, new_words = self._stitch(enhanced, sr, intervals, kept_words)
        final_audio = self.loudness_normalize(final_audio, sr)
        self._write_mp3(final_audio, sr, output_path)
        final_duration = final_audio.shape[0] / sr

        sting = None
        bounds = self._sting_bounds(new_words, sting_quote, final_duration)
        if bounds:
            a, b = int(bounds[0] * sr), int(bounds[1] * sr)
            clip = final_audio[a:b].copy()
            fade_len = int(SPAN_FADE_S * sr)
            if clip.shape[0] > 2 * fade_len:
                clip[:fade_len] *= np.linspace(0, 1, fade_len, dtype=np.float32)
                clip[-fade_len:] *= np.linspace(1, 0, fade_len, dtype=np.float32)
            sting_path = sting_path_for(output_path)
            self._write_mp3(clip, sr, sting_path)
            sting = {'file': os.path.basename(sting_path), 'text': sting_quote,
                     'duration': round(clip.shape[0] / sr, 3)}
        elif os.path.exists(sting_path_for(output_path)):
            os.remove(sting_path_for(output_path))
        return final_duration, new_words, sting

    async def enhance_audio(self, input_path: str, output_path: str, content_type: str,
                            json_path=None, filtered_json_path=None, ai_service=None,
                            context: Optional[dict] = None) -> bool:
        if content_type not in PROMPT_SETTINGS:
            raise ValueError(f"Unknown content type: {content_type}")

        if not self.model_loaded:
            await self.initialize()

        original_data = await self._load_json(json_path) if json_path else None
        if original_data is None or not filtered_json_path:
            log_service.error("Missing JSON path. Skipping slicing.")

        llm_task = None
        if original_data is not None and filtered_json_path and ai_service:
            llm_task = asyncio.create_task(
                self.process_transcription_with_gpt(json_path, content_type, ai_service, context=context))

        return await self._render(input_path, output_path, content_type, original_data, filtered_json_path, llm_task, None)

    async def rerender_audio(self, input_path: str, output_path: str, content_type: str,
                             json_path: str, filtered_json_path: str) -> bool:
        if content_type not in PROMPT_SETTINGS:
            raise ValueError(f"Unknown content type: {content_type}")

        if not self.model_loaded:
            await self.initialize()

        original_data = await self._load_json(json_path)
        existing_transcript = await self._load_json(filtered_json_path)
        if original_data is None or existing_transcript is None:
            return False
        return await self._render(input_path, output_path, content_type, original_data, filtered_json_path,
                                  None, existing_transcript)

    async def _render(self, input_path: str, output_path: str, content_type: str, original_data: Optional[dict],
                      filtered_json_path: Optional[str], llm_task, existing_transcript: Optional[dict]) -> bool:
        written = []
        try:
            enhanced_audio, enhanced_ok = await self._enhance(input_path)
            processed = await llm_task if llm_task is not None else None

            transcript = None
            filtered = False
            if original_data is not None and filtered_json_path:
                if existing_transcript is not None:
                    transcript = dict(existing_transcript)
                    filtered = bool(existing_transcript.get('transcript_filtered', True))
                else:
                    transcript, filtered = self._build_transcript(original_data, processed, content_type)

            original_words = (original_data or {}).get('word_level_transcription') or []
            spans = None
            if transcript is not None and filtered and original_words:
                clean_text = transcript.get('full_transcription', '')
                candidate_spans = self._get_keep_spans(original_words, clean_text)
                if self._spans_are_safe(candidate_spans, original_words, clean_text):
                    spans = candidate_spans
                else:
                    log_service.warning("User Content Processing: Filtered transcript does not match the audio closely, keeping full audio")

            sting_quote = ((transcript or {}).get('transcription_metadata') or {}).get('sting_quote') \
                if content_type == "review" else None
            duration_s, new_words, sting = await asyncio.to_thread(
                self._finalize_sync, enhanced_audio, ENHANCED_SAMPLE_RATE, original_words, spans, output_path, sting_quote
            )
            written.append(output_path)
            if sting:
                written.append(sting_path_for(output_path))

            if transcript is not None:
                metadata = dict(transcript.get('transcription_metadata') or {})
                metadata['total_words'] = len(new_words) if new_words else len(transcript.get('full_transcription', '').split())
                metadata['duration'] = round(duration_s, 3)
                if not metadata.get('where'):
                    where = await shoutout_where(metadata, transcript.get('user_data') or {})
                    if where is not None:
                        metadata['where'] = where.as_dict()
                transcript['transcription_metadata'] = metadata
                transcript['word_level_transcription'] = new_words
                transcript['audio_enhanced'] = enhanced_ok
                transcript['enhancement_version'] = settings.SHOUTOUT_ENHANCEMENT_VERSION if enhanced_ok else 0
                transcript['transcript_filtered'] = filtered
                transcript.pop('sting', None)
                if sting:
                    transcript['sting'] = sting
                await self._write_json_atomic(filtered_json_path, transcript)
                written.append(filtered_json_path)

            log_service.detail(
                f"User Content Processing: Saved {output_path} ({duration_s:.1f}s, enhanced={enhanced_ok}, "
                f"sliced={bool(spans)}, filtered={filtered}, sting={bool(sting)})", "user_content"
            )
            return True

        except Exception as e:
            log_service.error(f"Error processing {input_path}: {str(e)}\n{traceback.format_exc()}")
            if llm_task is not None and not llm_task.done():
                llm_task.cancel()
            for path in written:
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception as cleanup_error:
                    log_service.error(f"Failed to remove partial output {path}: {cleanup_error}")
            return False

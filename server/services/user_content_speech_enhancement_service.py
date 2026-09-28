"""
User Content Speech Enhancement Service

Processes user-generated audio (shoutouts/opinions) through hybrid enhancement chain.

Current State:
- Uses DeepFilterNet for noise reduction at 48kHz
- Uses ClearVoice MossFormer2 SR model for super-resolution (band-limited 48kHz → full-band 48kHz)
- Applies spectral balance for frequency flattening
- Applies proper broadcast-standard LUFS normalization (-14 LUFS, Spotify standard)
- GPT processes transcriptions to remove filler words and extract metadata
- Energy-based audio segment extraction finds natural silence points for clean cuts

Processing Pipeline:
1. Convert WebM → 16kHz mono WAV (native input)
2. Upsample 16kHz → 48kHz (for DeepFilterNet requirement)
3. DeepFilterNet (48kHz, CPU tensors in/out) - removes background noise
4. MossFormer2_SR_48K (band-limited 48kHz numpy in → full-band 48kHz out) - adds realistic high frequencies back
5. Spectral balance - gentle tilt-corrected equalisation towards a speech target curve
6. Loudness normalize (-14 LUFS, -1 dBFS peak ceiling)

TODO - Streaming Pipeline Integration:
- Currently outputs MP3 which creates wasteful double-encoding
- System already has multi-bitrate streaming pipeline (OPUS_128K/192K/256K + WebM)
- Should integrate directly with streaming encoder instead of intermediate MP3
- This would eliminate double-encoding and provide proper adaptive streaming support
- See settings.py: OPUS_128K_DIR, OPUS_192K_DIR, OPUS_256K_DIR, streaming infrastructure
"""

import asyncio
import subprocess
import threading
import torch
import torchaudio
import torch.nn.functional as F
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
from df.enhance import enhance, init_df

from services import log_service
from services.llm_router import LLM_BACKGROUND
from services.user_content_database_service import coarse_location
from models_global import gpu_lease
from config import settings
from config.settings import BASE_DIR

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

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
    opinion_type: Optional[str] = None
    sentiment: Optional[str] = None

class ShoutoutOpinionResponse(BaseModel):
    full_transcription: str
    transcription_metadata: TranscriptionMetadata

PROMPT_SETTINGS = {
    "opinion": {
        "system": "You are a LOSSLESS FILTER. Your job is to delete garbage, NOT to rewrite content.",
        "prompt": """Filter this content to remove noise while keeping the exact original phrasing.

                    RULES:
                    1. NO REWRITING. Do not fix grammar. Do not summarize.
                    2. KEEP: The core opinion, the exact words used, and the natural tone.
                    3. REMOVE ONLY: Stutters (um, uh), false starts (restarted sentences), and unintelligible glitches.

                    Input JSON:
                    {text}

                    Return EXACTLY this JSON structure (do not repeat user_data):
                    {{
                        "full_transcription": "The filtered text (must be exact original words)",
                        "transcription_metadata": {{
                            "total_words": 123,
                            "language": "en",
                            "language_probability": 1.0,
                            "opinion_type": "loves new restaurant",
                            "quality_rating": 4,
                            "sentiment": "positive"
                        }}
                    }}"""
    },
    "shoutout": {
        "system": "You are a LOSSLESS FILTER for radio. Your goal is to delete 'Process Talk' but keep the exact 'Message'.",
        "prompt": """Filter this shoutout. You must keep the original wording exactly as is, only deleting specific segments.

                    STRICT RULES:
                    1. DO NOT REWRITE. DO NOT FIX GRAMMAR. If they say "me and him went," KEEP IT.
                    2. REMOVE "Process Talk": Phrases *about* the recording (e.g., "Can I get a shoutout?", "Is this on?").
                    3. KEEP "Conversational Greetings": "Hey guys", "Hi everyone", "Yo bro" - these are ESSENTIAL.
                    4. REMOVE Stumbles: Stutters, false starts, and dead air.

                    CATEGORIZATION:
                    - Category: Short descriptive topic (e.g., "birthday_wishes")
                    - Urgency (0.0-1.0): 1.0 = Emergency, 0.5 = Event Soon, 0.0 = Casual
                    - Importance (0.0-1.0): 1.0 = Citywide, 0.5 = Local, 0.0 = Personal

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
                            "quality_rating": 4
                        }}
                    }}"""
    }
}


SPEECH_TARGET_PIVOT_HZ = 500.0
SPEECH_TARGET_SLOPE_DB_PER_OCTAVE = 4.5
SPECTRAL_BALANCE_STRENGTH = 0.5
SPECTRAL_MAX_BOOST_DB = 6.0
SPECTRAL_MAX_CUT_DB = 9.0
SPECTRAL_NOISE_FLOOR_DB = 60.0
PEAK_CEILING = 10 ** (-1.0 / 20)
LIMITER_WINDOW_S = 0.02
LIMITER_MAX_REDUCTION_DB = 6.0
ENHANCED_SAMPLE_RATE = 48000
SPAN_MERGE_GAP_S = 0.8
SPAN_PRE_PAD_S = 0.08
SPAN_POST_PAD_S = 0.12
LAST_SPAN_TAIL_S = 0.15
SPAN_FADE_S = 0.02
MIN_KEPT_WORD_RATIO = 0.35
MIN_CLEAN_MATCH_RATIO = 0.8


def _default_transcription_metadata(content_type: str) -> dict:
    if content_type == "opinion":
        return {"opinion_type": None, "sentiment": "neutral", "quality_rating": 3}
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


class UserContentSpeechEnhancementService:
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.sr_model = None
        self.df_model = None
        self.df_state = None
        self.model_loaded = False
        self._gpu_lock = threading.Lock()

    async def initialize(self):
        if self.model_loaded:
            return

        original_cwd = os.getcwd()
        try:
            os.chdir(BASE_DIR)

            log_service.system(f"Loading Audio Enhancement Chain on {self.device}...")

            log_service.system("  - Loading DeepFilterNet...")
            self.df_model, self.df_state, _ = init_df()
            self.df_model = self.df_model.to(self.device)
            log_service.system("  - DeepFilterNet loaded")

            log_service.system("  - Loading SR Model (MossFormer2_SR_48K)...")
            self.sr_model = ClearVoice(
                task='speech_super_resolution',
                model_names=['MossFormer2_SR_48K'],
            )

            self.model_loaded = True
            log_service.success(f"✓ Vocal enhancement chain loaded (DeepFilterNet + ClearVoice SR on {self.device})")

        except Exception as e:
            log_service.error(f"Failed to load vocal enhancement models: {str(e)}\n{traceback.format_exc()}")
            self.model_loaded = False
        finally:
            os.chdir(original_cwd)

    def convert_webm_to_wav(self, input_path, output_path, target_sample_rate=16000):

        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        if os.path.getsize(input_path) == 0:
            raise ValueError(f"Input file is empty: {input_path}")

        command = [
            'ffmpeg',
            '-y',
            '-i', input_path,
            '-acodec', 'pcm_s16le',
            '-ac', '1',
            '-ar', str(target_sample_rate),
            output_path,
            '-loglevel', 'error'
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            log_service.detail(f"User Content Processing: Converted WebM to mono WAV @ {target_sample_rate}Hz", "user_content")
        except subprocess.CalledProcessError as e:
            log_service.error(f"FFmpeg conversion failed: {e.stderr.decode(errors='ignore')}")
            raise

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

    def loudness_normalize(self, audio_tensor: torch.Tensor, sample_rate: int, target_lufs=-14.0) -> torch.Tensor:
        try:
            meter = pyln.Meter(sample_rate)
            audio_np = audio_tensor.detach().cpu().numpy().astype(np.float64)

            if audio_tensor.dim() == 2:
                if audio_np.shape[0] < audio_np.shape[1]:
                    audio_np = audio_np.T

            loudness = meter.integrated_loudness(audio_np)

            if not np.isfinite(loudness) or loudness <= -70.0:
                return audio_tensor

            normalized_np = self._limit_peaks(pyln.normalize.loudness(audio_np, loudness, target_lufs), sample_rate)

            normalized = torch.from_numpy(normalized_np.astype(np.float32))

            if audio_tensor.dim() == 2:
                if normalized.shape[0] > normalized.shape[1]:
                    normalized = normalized.t()

            return normalized.to(audio_tensor.device)
        except Exception as e:
            log_service.error(f"Normalization failed: {e}")
            return audio_tensor

    def spectral_balance(self, audio, sr):

        log_service.detail(f"User Content Processing: Applying spectral balance, shape={np.shape(audio)}, sample_rate={sr}", "user_content")

        audio_tensor = torch.as_tensor(np.asarray(audio), dtype=torch.float32)

        if audio_tensor.dim() == 2 and audio_tensor.shape[0] == 2:
            balanced_left = self.spectral_balance_mono(audio_tensor[0], sr)
            balanced_right = self.spectral_balance_mono(audio_tensor[1], sr)
            return torch.stack([balanced_left, balanced_right]).numpy()
        return self.spectral_balance_mono(audio_tensor, sr).numpy()

    def spectral_balance_mono(self, audio, sr):

        if audio.dim() == 2 and audio.shape[0] == 1:
            audio = audio.squeeze(0)
        audio = audio.detach().float().cpu()

        n_fft = 4096
        hop_length = 1024
        if audio.shape[-1] < n_fft:
            return audio

        window = torch.hann_window(n_fft)
        stft = torch.stft(audio, n_fft=n_fft, hop_length=hop_length, window=window, return_complex=True)

        power_spec = torch.mean(torch.abs(stft) ** 2, dim=1)
        power_spec_smooth = torch.nn.functional.conv1d(
            power_spec.unsqueeze(0).unsqueeze(0),
            torch.ones(1, 1, 51) / 51,
            padding='same'
        ).squeeze()

        freqs = torch.linspace(0, sr / 2, n_fft // 2 + 1)
        measured_db = 10 * torch.log10(power_spec_smooth + 1e-12)
        octaves = torch.log2(torch.clamp(freqs, min=SPEECH_TARGET_PIVOT_HZ) / SPEECH_TARGET_PIVOT_HZ)
        target_db = -SPEECH_TARGET_SLOPE_DB_PER_OCTAVE * octaves

        speech_band = (freqs >= 200) & (freqs <= 4000)
        difference = target_db - measured_db
        difference = difference - difference[speech_band].mean()

        correction_db = torch.clamp(difference * SPECTRAL_BALANCE_STRENGTH, -SPECTRAL_MAX_CUT_DB, SPECTRAL_MAX_BOOST_DB)
        near_floor = measured_db < (measured_db.max() - SPECTRAL_NOISE_FLOOR_DB)
        correction_db = torch.where(near_floor, torch.clamp(correction_db, max=0.0), correction_db)
        gain = torch.pow(10.0, correction_db / 20)

        balanced_audio = torch.istft(
            stft * gain.unsqueeze(1), n_fft=n_fft, hop_length=hop_length, window=window, length=audio.shape[-1]
        )

        max_amplitude = torch.max(torch.abs(balanced_audio))
        if max_amplitude > 1.0:
            balanced_audio /= max_amplitude

        log_service.detail(f"User Content Processing: Spectral balance complete, max amplitude: {max_amplitude:.4f}", "user_content")
        return balanced_audio

    @staticmethod
    def _resample(audio_tensor: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:
        if orig_sr == target_sr:
            return audio_tensor
        return torchaudio.functional.resample(audio_tensor, orig_sr, target_sr)

    def upsample_audio(self, audio_tensor: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:
        log_service.detail(f"User Content Processing: Upsampling audio from {orig_sr}Hz to {target_sr}Hz", "user_content")
        return self._resample(audio_tensor.detach().float().cpu(), orig_sr, target_sr)

    def downsample_audio(self, audio_tensor: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:
        log_service.detail(f"User Content Processing: Downsampling audio from {orig_sr}Hz to {target_sr}Hz", "user_content")
        return self._resample(audio_tensor.detach().float().cpu(), orig_sr, target_sr)

    def _super_resolve(self, audio_48k: torch.Tensor) -> torch.Tensor:
        signal = audio_48k.reshape(-1).numpy().astype(np.float32)
        model_args = getattr(self.sr_model.models[0], 'args', None)
        one_pass_samples = int(getattr(model_args, 'sampling_rate', ENHANCED_SAMPLE_RATE) *
                               getattr(model_args, 'one_time_decode_length', 20))
        batch = np.stack([signal, signal]) if signal.shape[0] <= one_pass_samples else signal[np.newaxis, :]
        with torch.no_grad():
            restored = self.sr_model.call_t2t_mode(batch)
        if restored is None:
            raise RuntimeError("SR model returned None")
        restored = np.asarray(restored, dtype=np.float32)
        return torch.as_tensor(restored[0] if restored.ndim == 2 else restored).reshape(1, -1)

    def _run_enhancement_chain(self, audio: torch.Tensor, sr_input: int) -> torch.Tensor:
        if self.df_model is None or self.df_state is None:
            raise RuntimeError("DeepFilterNet model not initialized. Call initialize() first.")
        if self.sr_model is None:
            raise RuntimeError("SR model not initialized. Call initialize() first.")

        if audio.dim() == 1:
            audio = audio.unsqueeze(0)
        if audio.shape[0] > 1:
            audio = audio.mean(dim=0, keepdim=True)

        audio_48k = self.upsample_audio(audio, sr_input, ENHANCED_SAMPLE_RATE).contiguous()
        log_service.detail(f"User Content Processing: Applying DeepFilterNet noise reduction, shape: {tuple(audio_48k.shape)}", "user_content")
        denoised_48k = enhance(self.df_model, self.df_state, audio_48k).float().cpu()

        log_service.detail("User Content Processing: Applying SR model to restore high frequencies...", "user_content")
        restored_48k = self._super_resolve(denoised_48k)

        target_len = denoised_48k.shape[-1]
        if restored_48k.shape[-1] > target_len:
            restored_48k = restored_48k[:, :target_len]
        elif restored_48k.shape[-1] < target_len:
            restored_48k = F.pad(restored_48k, (0, target_len - restored_48k.shape[-1]))

        balanced = self.spectral_balance(restored_48k.numpy(), ENHANCED_SAMPLE_RATE)
        return torch.as_tensor(np.asarray(balanced, dtype=np.float32)).reshape(1, -1)

    def _enhance_file_sync(self, input_wav_mono: str):
        original_audio, sr_input = torchaudio.load(input_wav_mono)
        log_service.detail(f"User Content Processing: Loaded mono audio @ {sr_input}Hz, shape: {tuple(original_audio.shape)}", "user_content")
        try:
            with self._gpu_lock:
                enhanced = self._run_enhancement_chain(original_audio, sr_input)
            return enhanced, ENHANCED_SAMPLE_RATE, True
        except Exception as e:
            log_service.error(f"Vocal enhancement failed: {str(e)}\n{traceback.format_exc()}")
            return original_audio.float(), sr_input, False
        finally:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    async def _enhance_to_tensor(self, input_path: str, temp_wav_16k_mono: str):
        await asyncio.to_thread(self.convert_webm_to_wav, input_path, temp_wav_16k_mono, 16000)
        async with gpu_lease("Shoutout enhancement"):
            return await asyncio.to_thread(self._enhance_file_sync, temp_wav_16k_mono)

    def _get_keep_spans(self, original_words: List[dict], cleaned_text: str) -> List[dict]:
        def clean_word(w):
            return "".join(char for char in w.lower() if char.isalnum())

        cleaned_tokens = [clean_word(w) for w in cleaned_text.split() if clean_word(w)]
        original_tokens = [clean_word(w['word']) for w in original_words]

        matcher = difflib.SequenceMatcher(None, original_tokens, cleaned_tokens, autojunk=False)

        raw_matches = []
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag == 'equal':
                raw_matches.append({
                    "start_index": i1,
                    "end_index": i2,
                    "start_time": original_words[i1]['start'],
                    "end_time": original_words[i2 - 1]['end'],
                    "words": list(original_words[i1:i2])
                })

        if not raw_matches:
            return []

        merged_spans = [raw_matches[0]]
        for next_span in raw_matches[1:]:
            current_span = merged_spans[-1]
            gap = next_span['start_time'] - current_span['end_time']
            if gap < SPAN_MERGE_GAP_S:
                current_span['end_time'] = next_span['end_time']
                current_span['words'].extend(next_span['words'])
                current_span['end_index'] = next_span['end_index']
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

    def _recalibrate_and_stitch(self, audio: torch.Tensor, sr: int, spans: List[dict]) -> Tuple[torch.Tensor, List[dict]]:
        if not spans:
            return audio, []

        total_samples = audio.shape[-1]
        last_index = len(spans) - 1
        fade_len = int(SPAN_FADE_S * sr)
        audio_segments = []
        recalibrated_words = []
        cursor = 0

        for i, span in enumerate(spans):
            start_time = span['start_time'] - SPAN_PRE_PAD_S
            end_time = span['end_time'] + (max(SPAN_POST_PAD_S, LAST_SPAN_TAIL_S) if i == last_index else SPAN_POST_PAD_S)
            if i > 0:
                start_time = max(start_time, (spans[i - 1]['end_time'] + span['start_time']) / 2)
            if i < last_index:
                end_time = min(end_time, (span['end_time'] + spans[i + 1]['start_time']) / 2)

            start_sample = min(max(0, int(round(start_time * sr))), total_samples)
            end_sample = min(max(0, int(round(end_time * sr))), total_samples)
            if end_sample <= start_sample:
                continue

            segment = audio[..., start_sample:end_sample].clone()
            if segment.shape[-1] > 2 * fade_len > 0:
                segment[..., :fade_len] *= torch.linspace(0, 1, fade_len, device=segment.device)
                segment[..., -fade_len:] *= torch.linspace(1, 0, fade_len, device=segment.device)

            time_shift = (cursor - start_sample) / sr
            for word in span['words']:
                new_word = dict(word)
                new_word['start'] = round(max(0.0, word['start'] + time_shift), 3)
                new_word['end'] = round(max(0.0, word['end'] + time_shift), 3)
                recalibrated_words.append(new_word)

            audio_segments.append(segment)
            cursor += segment.shape[-1]

        if not audio_segments:
            return audio, []

        return torch.cat(audio_segments, dim=-1), recalibrated_words

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

    async def process_transcription_with_gpt(self, json_path: str, content_type: str, ai_service) -> dict:
        try:
            original_data = await self._load_json(json_path)
            if not original_data:
                return {}

            payload = {"transcription": original_data.get("full_transcription", "")}
            location = coarse_location((original_data.get("user_data") or {}).get("location"))
            if location and content_type == "shoutout":
                payload["location"] = location

            prompt_settings = PROMPT_SETTINGS[content_type]
            prompt = prompt_settings["prompt"].format(text=json.dumps(payload, ensure_ascii=False))

            completion = await ai_service.generate(
                messages=[
                    {"role": "system", "content": prompt_settings["system"]},
                    {"role": "user", "content": prompt}
                ],
                model=settings.GEMINI_DJ_MODEL,
                temperature=0,
                max_tokens=2000,
                response_schema=ShoutoutOpinionResponse,
                role=LLM_BACKGROUND
            )

            if not completion:
                log_service.error("User Content Enhancement: LLM returned no response")
                return {}

            processed_data = getattr(completion, 'structured_data', None)
            if processed_data is None:
                response_text = (completion.choices[0].message.content or "").strip()
                try:
                    processed_data = json.loads(response_text)
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
                f"User Content Enhancement: Processed {content_type} with category '{processed_data['transcription_metadata'].get('category', 'N/A')}'", "user_content")
            return processed_data

        except Exception as e:
            log_service.error(f"Error in process_transcription_with_gpt: {str(e)}\n{traceback.format_exc()}")
            return {}

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

    def _finalize_sync(self, enhanced_audio: torch.Tensor, sr: int, spans: Optional[List[dict]],
                       temp_final_wav: str, output_path: str) -> Tuple[float, List[dict]]:
        final_audio = enhanced_audio
        new_word_timestamps = []

        if spans:
            final_audio, new_word_timestamps = self._recalibrate_and_stitch(enhanced_audio, sr, spans)

        final_audio = self.loudness_normalize(final_audio, sr)

        if final_audio.dim() == 1:
            final_audio = final_audio.unsqueeze(0)

        final_audio = final_audio.detach().cpu().float()
        final_audio_int16 = (final_audio * 32767).clamp(-32768, 32767).short()
        torchaudio.save(temp_final_wav, final_audio_int16, sr)

        temp_mp3 = f"{output_path}.tmp"
        command = [
            'ffmpeg', '-y',
            '-i', temp_final_wav,
            '-codec:a', 'libmp3lame',
            '-ac', '1',
            '-ar', '48000',
            '-qscale:a', '2',
            '-f', 'mp3',
            '-loglevel', 'error',
            temp_mp3
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.replace(temp_mp3, output_path)
        finally:
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)
        return final_audio.shape[-1] / sr, new_word_timestamps

    async def enhance_audio(self, input_path: str, output_path: str, content_type: str,
                            json_path=None, filtered_json_path=None, ai_service=None) -> bool:
        if content_type not in PROMPT_SETTINGS:
            raise ValueError(f"Unknown content type: {content_type}")

        if not self.model_loaded:
            await self.initialize()

        original_data = await self._load_json(json_path) if json_path else None
        if original_data is None or not filtered_json_path:
            log_service.error("Missing JSON path. Skipping slicing.")

        llm_task = None
        if original_data is not None and filtered_json_path and ai_service:
            llm_task = asyncio.create_task(self.process_transcription_with_gpt(json_path, content_type, ai_service))

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
        temp_dir = os.path.dirname(output_path)
        base_name = os.path.splitext(os.path.basename(output_path))[0]
        temp_wav_16k_mono = os.path.join(temp_dir, f"{base_name}_input_16k_mono.wav")
        temp_final_wav = os.path.join(temp_dir, f"{base_name}_final.wav")
        written = []

        try:
            enhanced_audio, sr, enhanced_ok = await self._enhance_to_tensor(input_path, temp_wav_16k_mono)
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

            duration_s, new_word_timestamps = await asyncio.to_thread(
                self._finalize_sync, enhanced_audio, sr, spans, temp_final_wav, output_path
            )
            written.append(output_path)

            if transcript is not None:
                words = new_word_timestamps if spans else [dict(w) for w in original_words]
                metadata = dict(transcript.get('transcription_metadata') or {})
                metadata['total_words'] = len(words) if words else len(transcript.get('full_transcription', '').split())
                metadata['duration'] = round(duration_s, 3)
                transcript['transcription_metadata'] = metadata
                transcript['word_level_transcription'] = words
                transcript['audio_enhanced'] = enhanced_ok
                transcript['enhancement_version'] = settings.SHOUTOUT_ENHANCEMENT_VERSION if enhanced_ok else 0
                transcript['transcript_filtered'] = filtered
                await self._write_json_atomic(filtered_json_path, transcript)
                written.append(filtered_json_path)

            log_service.detail(
                f"User Content Processing: Saved {output_path} ({duration_s:.1f}s, enhanced={enhanced_ok}, "
                f"sliced={bool(spans)}, filtered={filtered})", "user_content"
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
        finally:
            for temp_file in [temp_wav_16k_mono, temp_final_wav]:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception as cleanup_error:
                        log_service.error(f"Failed to clean up {temp_file}: {cleanup_error}")

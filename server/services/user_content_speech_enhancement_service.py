"""
User Content Speech Enhancement Service

Processes user-generated audio (shoutouts/opinions) through hybrid enhancement chain.

Current State:
- Uses DeepFilterNet for noise reduction at 48kHz
- Uses ClearVoice MossFormer2 SR model for super-resolution (16kHz → 48kHz)
- Applies spectral balance for frequency flattening
- Applies proper broadcast-standard LUFS normalization (-14 LUFS, Spotify standard)
- GPT processes transcriptions to remove filler words and extract metadata
- Energy-based audio segment extraction finds natural silence points for clean cuts

Processing Pipeline:
1. Convert WebM → 16kHz mono WAV (native input)
2. Upsample 16kHz → 48kHz (for DeepFilterNet requirement)
3. DeepFilterNet (48kHz) - removes background noise
4. Downsample 48kHz → 16kHz (for SR model input)
5. MossFormer2_SR_48K (16kHz → 48kHz) - adds realistic high frequencies back
6. Spectral balance - frequency flattening
7. Loudness normalize (-14 LUFS)

TODO - Streaming Pipeline Integration:
- Currently outputs MP3 which creates wasteful double-encoding
- System already has multi-bitrate streaming pipeline (OPUS_128K/192K/256K + WebM)
- Should integrate directly with streaming encoder instead of intermediate MP3
- This would eliminate double-encoding and provide proper adaptive streaming support
- See settings.py: OPUS_128K_DIR, OPUS_192K_DIR, OPUS_256K_DIR, streaming infrastructure
"""

import subprocess
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
from typing import List, Optional
from dataclasses import dataclass
from pydantic import BaseModel, Field
from clearvoice import ClearVoice
from df.enhance import enhance, init_df

from services import log_service
from config import settings
from config.settings import BASE_DIR

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

@dataclass
class TextSegment:
    text: str
    start_time: float
    end_time: float
    confidence: float

class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    probability: float

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

class UserContentSpeechEnhancementService:
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.sr_model = None
        self.df_model = None
        self.df_state = None
        self.model_loaded = False

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
            '-ac', '1',  # Mono
            '-ar', str(target_sample_rate),
            output_path,
            '-loglevel', 'error'
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            log_service.user_content(f"User Content Processing: Converted WebM to mono WAV @ {target_sample_rate}Hz")
        except subprocess.CalledProcessError as e:
            log_service.error(f"FFmpeg conversion failed: {e.stderr.decode()}")
            raise

    def loudness_normalize(self, audio_tensor: torch.Tensor, sample_rate: int, target_lufs=-14.0) -> torch.Tensor:
        try:
            meter = pyln.Meter(sample_rate)
            audio_np = audio_tensor.cpu().numpy()

            if audio_tensor.dim() == 2:
                if audio_np.shape[0] < audio_np.shape[1]:
                    audio_np = audio_np.T

            loudness = meter.integrated_loudness(audio_np)

            if loudness <= -70.0:
                return audio_tensor

            normalized_np = pyln.normalize.loudness(audio_np, loudness, target_lufs)

            max_amplitude = abs(normalized_np).max()
            if max_amplitude > 0.99:
                normalized_np = normalized_np * (0.99 / max_amplitude)

            normalized = torch.from_numpy(normalized_np)

            if audio_tensor.dim() == 2:
                if normalized.shape[0] > normalized.shape[1]:
                    normalized = normalized.t()

            return normalized.to(audio_tensor.device)
        except Exception as e:
            log_service.error(f"Normalization failed: {e}")
            return audio_tensor

    def spectral_balance(self, audio, sr):

        log_service.user_content(f"User Content Processing: Applying spectral balance, shape={audio.shape}, sample_rate={sr}")

        audio_tensor = torch.tensor(audio, dtype=torch.float32, device=self.device)

        if audio_tensor.dim() == 2 and audio_tensor.shape[0] == 2:
            balanced_left = self.spectral_balance_mono(audio_tensor[0], sr)
            balanced_right = self.spectral_balance_mono(audio_tensor[1], sr)
            return torch.stack([balanced_left, balanced_right]).cpu().numpy()
        else:
            return self.spectral_balance_mono(audio_tensor, sr).cpu().numpy()

    def spectral_balance_mono(self, audio, sr):

        if audio.dim() == 2 and audio.shape[0] == 1:
            audio = audio.squeeze(0)

        n_fft = 4096
        hop_length = 1024
        window = torch.hann_window(n_fft, device=self.device)
        stft = torch.stft(audio, n_fft=n_fft, hop_length=hop_length, window=window, return_complex=True)

        mag = torch.abs(stft)
        power_spec = torch.mean(mag ** 2, dim=1)
        freqs = torch.linspace(0, sr / 2, n_fft // 2 + 1, device=self.device)

        power_spec_smooth = torch.nn.functional.conv1d(
            power_spec.unsqueeze(0).unsqueeze(0),
            torch.ones(1, 1, 51, device=self.device) / 51,
            padding='same'
        ).squeeze()

        avg_power = torch.mean(power_spec_smooth)
        epsilon = 1e-10
        correction = torch.sqrt(torch.clamp(avg_power / (power_spec_smooth + epsilon), epsilon))

        max_correction_db = 12
        correction = torch.clamp(correction, 10 ** (-max_correction_db / 20), 10 ** (max_correction_db / 20))

        normalized_freqs = freqs / (sr / 2)
        slope = torch.exp(-12 * normalized_freqs)
        weighting = slope
        correction *= weighting

        balanced_mag = mag * correction.unsqueeze(1)
        balanced_stft = balanced_mag * torch.exp(1j * torch.angle(stft))
        balanced_audio = torch.istft(balanced_stft, n_fft=n_fft, hop_length=hop_length, window=window)

        max_amplitude = torch.max(torch.abs(balanced_audio))
        if max_amplitude > 1.0:
            balanced_audio /= max_amplitude

        log_service.user_content(f"User Content Processing: Spectral balance complete, max amplitude: {torch.max(torch.abs(balanced_audio)):.4f}")
        return balanced_audio

    def upsample_audio(self, audio_tensor: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:

        if orig_sr == target_sr:
            return audio_tensor

        log_service.user_content(f"User Content Processing: Upsampling audio from {orig_sr}Hz to {target_sr}Hz")
        resampler = torchaudio.transforms.Resample(orig_sr, target_sr).to(self.device)

        if audio_tensor.device != self.device:
            audio_tensor = audio_tensor.to(self.device)

        upsampled = resampler(audio_tensor)
        return upsampled

    def downsample_audio(self, audio_tensor: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:

        if orig_sr == target_sr:
            return audio_tensor

        log_service.user_content(f"User Content Processing: Downsampling audio from {orig_sr}Hz to {target_sr}Hz")
        resampler = torchaudio.transforms.Resample(orig_sr, target_sr).to(self.device)

        if audio_tensor.device != self.device:
            audio_tensor = audio_tensor.to(self.device)

        downsampled = resampler(audio_tensor)
        return downsampled

    def _dynamic_confidence_blend(self, enhanced_tensor: torch.Tensor, original_tensor: torch.Tensor) -> torch.Tensor:
        min_len = min(enhanced_tensor.shape[1], original_tensor.shape[1])
        enhanced = enhanced_tensor[:, :min_len]
        original = original_tensor[:, :min_len]

        window_size = 1024
        pad_size = window_size // 2

        abs_enhanced = torch.abs(enhanced)
        abs_original = torch.abs(original)

        env_enhanced = F.avg_pool1d(
            F.pad(abs_enhanced, (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        )

        env_original = F.avg_pool1d(
            F.pad(abs_original, (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        )

        env_enhanced = env_enhanced[:, :min_len]
        env_original = env_original[:, :min_len]

        ratio = env_enhanced / (env_original + 1e-6)
        confidence_mask = torch.clamp(ratio, 0.0, 1.0)

        MAX_WET = 0.95
        mix_weight = confidence_mask * MAX_WET

        final_mix = (enhanced * mix_weight) + (original * (1.0 - mix_weight))
        final_mix = torch.clamp(final_mix, -1.0, 1.0)

        return final_mix

    def _apply_envelope_warp(self, enhanced: torch.Tensor, original: torch.Tensor) -> torch.Tensor:
        min_len = min(enhanced.shape[1], original.shape[1])
        target = enhanced[:, :min_len]
        reference = original[:, :min_len]

        window_size = 2048
        pad_size = window_size // 2
        abs_ref = torch.abs(reference)

        env_ref = F.avg_pool1d(
            F.pad(abs_ref, (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        )

        if env_ref.shape[1] > min_len:
            env_ref = env_ref[:, :min_len]
        elif env_ref.shape[1] < min_len:
            env_ref = F.pad(env_ref, (0, min_len - env_ref.shape[1]))

        ref_peak = torch.max(env_ref)
        if ref_peak > 1e-6:
            norm_env = env_ref / ref_peak
        else:
            norm_env = env_ref

        warp_mask = torch.sqrt(norm_env + 1e-6)
        return target * warp_mask

    def _match_rms(self, source: torch.Tensor, reference: torch.Tensor) -> tuple[torch.Tensor, float]:
        ref_rms = torch.sqrt(torch.mean(reference ** 2))
        src_rms = torch.sqrt(torch.mean(source ** 2))

        if src_rms > 1e-6:
            scale = ref_rms / src_rms
            gain_db = 20 * np.log10(scale.item())
            return source * scale, gain_db
        return source, 0.0

    async def _process_audio_chain(self, input_wav_mono: str, temp_dir: str) -> str:

        try:
            original_audio_16k, sr_input = torchaudio.load(input_wav_mono)
            log_service.user_content(f"User Content Processing: Loaded mono audio @ {sr_input}Hz, shape: {original_audio_16k.shape}")

            audio_48k = self.upsample_audio(original_audio_16k, sr_input, 48000)
            log_service.user_content(f"User Content Processing: Upsampled to 48kHz for DeepFilterNet, shape: {audio_48k.shape}")

            log_service.user_content("User Content Processing: Applying DeepFilterNet noise reduction...")
            if self.df_model is None or self.df_state is None:
                raise RuntimeError("DeepFilterNet model not initialized. Call initialize() first.")
            audio_48k_float = audio_48k.float()
            denoised_48k = enhance(self.df_model, self.df_state, audio_48k_float)
            log_service.user_content(f"User Content Processing: DeepFilterNet complete, shape: {denoised_48k.shape}")

            denoised_16k = self.downsample_audio(denoised_48k, 48000, 16000)
            log_service.user_content(f"User Content Processing: Downsampled to 16kHz for SR model, shape: {denoised_16k.shape}")

            log_service.user_content("User Content Processing: Applying SR model to add high frequencies (tensor mode)...")
            if self.sr_model is None:
                raise RuntimeError("SR model not initialized. Call initialize() first.")
            sr_audio_48k = self.sr_model.call_t2t_mode(denoised_16k)
            if sr_audio_48k is None:
                raise RuntimeError("SR model returned None")
            log_service.user_content(f"User Content Processing: SR model upsampled to 48kHz, shape: {sr_audio_48k.shape}")

            log_service.user_content("User Content Processing: Applying spectral balance...")
            sr_audio_np = sr_audio_48k.cpu().numpy()
            balanced_audio_np = self.spectral_balance(sr_audio_np, 48000)
            final_48k = torch.from_numpy(balanced_audio_np)

            log_service.user_content("User Content Processing: Final 48kHz mono audio ready")

            final_out_path = os.path.join(temp_dir, "temp_final_processed.wav")
            torchaudio.save(final_out_path, final_48k, 48000)

            gc.collect()
            return final_out_path

        except Exception as e:
            log_service.error(f"Vocal enhancement failed: {str(e)}\n{traceback.format_exc()}")
            gc.collect()
            return input_wav_mono

    def _get_keep_spans(self, original_words: List[dict], cleaned_text: str) -> List[dict]:
        def clean_word(w):
            return "".join(char for char in w.lower() if char.isalnum())

        cleaned_tokens = [clean_word(w) for w in cleaned_text.split() if clean_word(w)]
        original_tokens = [clean_word(w['word']) for w in original_words]

        matcher = difflib.SequenceMatcher(None, original_tokens, cleaned_tokens, autojunk=False)

        raw_matches = []
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag == 'equal':
                start_word = original_words[i1]
                end_word = original_words[i2 - 1]
                raw_matches.append({
                    "start_index": i1,
                    "end_index": i2,
                    "start_time": start_word['start'],
                    "end_time": end_word['end'],
                    "words": original_words[i1:i2]
                })

        if not raw_matches:
            return []

        merged_spans = [raw_matches[0]]
        for next_span in raw_matches[1:]:
            current_span = merged_spans[-1]
            gap = next_span['start_time'] - current_span['end_time']
            if gap < 0.8:
                current_span['end_time'] = next_span['end_time']
                current_span['words'].extend(next_span['words'])
                current_span['end_index'] = next_span['end_index']
            else:
                merged_spans.append(next_span)

        return merged_spans

    def _recalibrate_and_stitch(self, audio: torch.Tensor, sr: int, spans: List[dict]) -> tuple[
        torch.Tensor, List[dict]]:
        if not spans:
            return audio, []

        audio_segments = []
        recalibrated_words = []
        current_sample_cursor = 0
        fade_len = int(0.05 * sr)
        micro_fade_len = int(0.005 * sr)

        for i, span in enumerate(spans):
            is_last_segment = (i == len(spans) - 1)

            start_sample = int(span['start_time'] * sr)
            end_sample = int(span['end_time'] * sr)

            if is_last_segment:
                end_sample += int(0.15 * sr)

            end_sample = min(end_sample, audio.shape[-1])
            start_sample = min(start_sample, end_sample)

            if audio.dim() == 2:
                segment = audio[:, start_sample:end_sample]
            else:
                segment = audio[start_sample:end_sample]

            if segment.shape[-1] == 0:
                continue

            seg_len = segment.shape[-1]
            if seg_len > 2 * fade_len:
                fade_in = torch.linspace(0, 1, fade_len, device=audio.device)
                fade_out = torch.linspace(1, 0, fade_len, device=audio.device)

                if audio.dim() == 2:
                    segment[:, :fade_len] *= fade_in
                else:
                    segment[:fade_len] *= fade_in

                if not is_last_segment:
                    if audio.dim() == 2:
                        segment[:, -fade_len:] *= fade_out
                    else:
                        segment[-fade_len:] *= fade_out
                else:
                    if seg_len > micro_fade_len:
                        micro_fade = torch.linspace(1, 0, micro_fade_len, device=audio.device)
                        if audio.dim() == 2:
                            segment[:, -micro_fade_len:] *= micro_fade
                        else:
                            segment[-micro_fade_len:] *= micro_fade

            audio_segments.append(segment)

            base_time_offset = current_sample_cursor / sr
            span_time_shift = base_time_offset - span['start_time']

            for word in span['words']:
                new_word = word.copy()
                new_word['start'] = round(word['start'] + span_time_shift, 3)
                new_word['end'] = round(word['end'] + span_time_shift, 3)
                recalibrated_words.append(new_word)

            current_sample_cursor += segment.shape[-1]

        if not audio_segments:
            return torch.zeros((audio.shape[0], sr)), []

        final_audio = torch.cat(audio_segments, dim=-1)
        return final_audio, recalibrated_words

    async def process_transcription_with_gpt(self, json_path: str, content_type: str, ai_service) -> dict:
        try:
            async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                original_data = json.loads(content)

            prompt_settings = PROMPT_SETTINGS[content_type]
            prompt = prompt_settings["prompt"].format(text=json.dumps(original_data, ensure_ascii=False))

            completion = await ai_service.generate(
                messages=[
                    {"role": "system", "content": prompt_settings["system"]},
                    {"role": "user", "content": prompt}
                ],
                model=settings.GEMINI_DJ_MODEL,
                temperature=0,
                max_tokens=2000,
                response_schema=ShoutoutOpinionResponse
            )

            if not completion:
                log_service.error("User Content Enhancement: GPT returned no response")
                return {}

            if hasattr(completion, 'structured_data'):
                processed_data = completion.structured_data
            else:
                response_text = completion.choices[0].message.content.strip()
                try:
                    processed_data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    log_service.error(f"Failed to parse GPT response as JSON: {e}")
                    return {}

            if 'user_data' in original_data:
                processed_data['user_data'] = original_data['user_data']

            if 'timestamp' in original_data:
                processed_data['timestamp'] = original_data['timestamp']

            if 'transcription_metadata' not in processed_data:
                processed_data['transcription_metadata'] = {}

            log_service.user_content(
                f"User Content Enhancement: Processed {content_type} with category '{processed_data['transcription_metadata'].get('category', 'N/A')}'")
            return processed_data

        except Exception as e:
            log_service.error(f"Error in process_transcription_with_gpt: {str(e)}\n{traceback.format_exc()}")
            return {}

    async def enhance_audio(self, input_path: str, output_path: str, content_type: str,
                            json_path=None, filtered_json_path=None, ai_service=None):
        if content_type not in PROMPT_SETTINGS:
            raise ValueError(f"Unknown content type: {content_type}")

        if not self.model_loaded:
            await self.initialize()

        temp_wav_16k_mono = None
        temp_enhanced_wav = None
        temp_final_wav = None

        try:
            temp_dir = os.path.dirname(output_path)
            base_name = os.path.splitext(os.path.basename(output_path))[0]

            temp_wav_16k_mono = os.path.join(temp_dir, f"{base_name}_input_16k_mono.wav")
            self.convert_webm_to_wav(input_path, temp_wav_16k_mono, target_sample_rate=16000)
            log_service.user_content(f"User Content Processing: Processing {content_type} audio from {input_path}")

            temp_enhanced_wav = await self._process_audio_chain(temp_wav_16k_mono, temp_dir)
            enhanced_audio, sr = torchaudio.load(temp_enhanced_wav)

            final_audio = enhanced_audio

            if json_path and filtered_json_path and ai_service:
                async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
                    original_data = json.loads(await f.read())
                    original_words = original_data.get('word_level_transcription', [])

                processed_json = await self.process_transcription_with_gpt(json_path, content_type, ai_service)

                if processed_json and original_words:
                    clean_text = processed_json.get('full_transcription', '')
                    spans = self._get_keep_spans(original_words, clean_text)

                    final_audio, new_word_timestamps = self._recalibrate_and_stitch(enhanced_audio, sr, spans)

                    processed_json['word_level_transcription'] = new_word_timestamps
                    processed_json['transcription_metadata']['total_words'] = len(new_word_timestamps)

                    async with aiofiles.open(filtered_json_path, 'w', encoding='utf-8') as f:
                        await f.write(json.dumps(processed_json, indent=2, ensure_ascii=False))
                else:
                    log_service.warning("User Content Processing: Alignment failed, using full enhanced audio.")
            else:
                log_service.error("Missing JSON path. Skipping slicing.")

            final_audio = self.loudness_normalize(final_audio, sr)

            if final_audio.dim() == 1:
                final_audio = final_audio.unsqueeze(0)

            final_audio = final_audio.cpu()
            final_audio_int16 = (final_audio * 32767).clamp(-32768, 32767).short()

            temp_final_wav = os.path.join(temp_dir, f"{base_name}_final.wav")
            torchaudio.save(temp_final_wav, final_audio_int16, sr)

            command = [
                'ffmpeg', '-y',
                '-i', temp_final_wav,
                '-codec:a', 'libmp3lame',
                '-ac', '1',
                '-ar', '48000',
                '-qscale:a', '2',
                '-loglevel', 'error',
                output_path
            ]
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            log_service.user_content(f"User Content Processing: Enhanced audio saved: {output_path}")

        except Exception as e:
            log_service.error(f"Error processing {input_path}: {str(e)}\n{traceback.format_exc()}")
        finally:
            for temp_file in [temp_wav_16k_mono, temp_enhanced_wav, temp_final_wav]:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception as cleanup_error:
                        log_service.error(f"Failed to clean up {temp_file}: {cleanup_error}")
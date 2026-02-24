import asyncio
import os
from pathlib import Path
from typing import Optional
import torch
import torchaudio
import torch.nn.functional as F
import gc
import numpy as np
from clearvoice import ClearVoice
from services import log_service
from services.base_service import SingletonService
from config.settings import BASE_DIR

class AudioClearVoiceService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.se_model = None
        self.sr_model = None
        self.models_loaded = False
        self.lock = asyncio.Lock()
        self._initialized = True

    async def initialize(self):
        if self.models_loaded:
            log_service.upscaling("ClearVoice models already loaded")
            return

        log_service.upscaling("Loading ClearVoice Chain (Cleaner + Sparkle)...")

        original_cwd = os.getcwd()
        try:
            os.chdir(BASE_DIR)

            log_service.upscaling("  - Loading SE Model (MossFormer2_SE_48K)...")
            self.se_model = ClearVoice(
                task='speech_enhancement',
                model_names=['MossFormer2_SE_48K']
            )

            log_service.upscaling("  - Loading SR Model (MossFormer2_SR_48K)...")
            self.sr_model = ClearVoice(
                task='speech_super_resolution',
                model_names=['MossFormer2_SR_48K']
            )

            self.models_loaded = True
            log_service.upscaling("ClearVoice Chain Loaded - Ready.")
        except Exception as e:
            log_service.error(f"Failed to load ClearVoice models: {str(e)}")
            self.models_loaded = False
        finally:
            os.chdir(original_cwd)

    def _dynamic_confidence_blend(self, enhanced_tensor: torch.Tensor, original_tensor: torch.Tensor) -> torch.Tensor:
        min_len = min(enhanced_tensor.shape[1], original_tensor.shape[1])
        enhanced = enhanced_tensor[:, :min_len]
        original = original_tensor[:, :min_len]

        window_size = 1024
        pad_size = window_size // 2

        abs_enhanced = torch.abs(enhanced)
        abs_original = torch.abs(original)

        env_enhanced = F.avg_pool1d(  # pylint: disable=E1102
            F.pad(abs_enhanced.unsqueeze(0), (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        ).squeeze(0)

        env_original = F.avg_pool1d(  # pylint: disable=E1102
            F.pad(abs_original.unsqueeze(0), (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        ).squeeze(0)

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

        env_ref = F.avg_pool1d(  # pylint: disable=E1102
            F.pad(abs_ref.unsqueeze(0), (pad_size, pad_size), mode='reflect'),
            kernel_size=window_size, stride=1
        ).squeeze(0)

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

    def _process_audio_sync(self, input_path: Path, output_path: Path) -> Optional[Path]:
        temp_input_48k = None
        temp_se_out = None
        temp_blended_for_sr = None
        temp_sr_out = None

        try:
            original_audio, original_sr = torchaudio.load(str(input_path))
            target_sr = 44100

            if original_sr != 48000:
                audio_48k = torchaudio.functional.resample(original_audio, original_sr, 48000)
                temp_input_48k = input_path.with_suffix(".temp_in_48k.wav")
                torchaudio.save(str(temp_input_48k), audio_48k, 48000)
                process_path = temp_input_48k
            else:
                process_path = input_path

            log_service.upscaling("  [1/4] Running SE Model...")
            se_wav_path = self.se_model(input_path=str(process_path), online_write=False)

            temp_se_out = output_path.with_suffix(".temp_se_out.wav")
            self.se_model.write(se_wav_path, output_path=str(temp_se_out))
            se_audio_48k, _ = torchaudio.load(str(temp_se_out))
            se_audio_target = torchaudio.functional.resample(se_audio_48k, 48000, target_sr)

            if original_sr != target_sr:
                original_target = torchaudio.functional.resample(original_audio, original_sr, target_sr)
            else:
                original_target = original_audio

            log_service.upscaling("  [2/4] Blending...")
            blended_audio = self._dynamic_confidence_blend(se_audio_target, original_target)

            temp_blended_for_sr = output_path.with_suffix(".temp_blend_for_sr.wav")
            blended_audio_48k = torchaudio.functional.resample(blended_audio, target_sr, 48000)
            torchaudio.save(str(temp_blended_for_sr), blended_audio_48k, 48000)

            log_service.upscaling("  [3/4] Running SR Model...")
            sr_wav_path = self.sr_model(input_path=str(temp_blended_for_sr), online_write=False)

            temp_sr_out = output_path.with_suffix(".temp_sr_final.wav")
            self.sr_model.write(sr_wav_path, output_path=str(temp_sr_out))
            sr_audio_48k, _ = torchaudio.load(str(temp_sr_out))

            enhanced_audio = torchaudio.functional.resample(sr_audio_48k, 48000, target_sr)

            log_service.upscaling("  [4/4] Finalizing Outputs...")

            isolated_final, _ = self._match_rms(enhanced_audio, original_target)
            isolated_path = output_path.with_name(f"{output_path.stem}_ISOLATED{output_path.suffix}")
            torchaudio.save(str(isolated_path), isolated_final, target_sr)
            log_service.upscaling(f"    - Saved Debug: {isolated_path.name}")

            warped_audio = self._apply_envelope_warp(enhanced_audio, original_target)
            final_output, gain_db = self._match_rms(warped_audio, original_target)

            torchaudio.save(str(output_path), final_output, target_sr)
            log_service.upscaling(f"    - RMS Adjusted: {gain_db:+.2f} dB")
            log_service.upscaling(f"  ✓ Saved Final: {output_path.name}")

            for p in [temp_input_48k, temp_se_out, temp_blended_for_sr, temp_sr_out]:
                if p and p.exists():
                    p.unlink()

            gc.collect()
            return output_path

        except Exception as e:
            log_service.error(f"Chain Processing failed: {str(e)}")
            try:
                for p in [temp_input_48k, temp_se_out, temp_blended_for_sr, temp_sr_out]:
                    if p and p.exists():
                        p.unlink()
            except Exception:
                pass
            gc.collect()
            return None

    async def enhance_vocals(self, input_path: Path, output_path: Path) -> Optional[Path]:
        async with self.lock:
            if not self.models_loaded:
                log_service.error("ClearVoice models not loaded")
                return None

            log_service.upscaling(f"Processing Chain: {input_path.name}")
            return await asyncio.to_thread(self._process_audio_sync, input_path, output_path)

    async def unload(self):
        self.se_model = None
        self.sr_model = None
        gc.collect()
        self.models_loaded = False
        log_service.upscaling("ClearVoice Chain unloaded")
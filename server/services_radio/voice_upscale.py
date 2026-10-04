import contextlib
import io
import os
import threading
import time
import uuid
from typing import Tuple

import librosa
import numpy as np
import soundfile as sf

from config.settings import settings
from services import log_service

UPSCALE_RATE = 48000
MIN_MODEL_SECONDS = 0.25


class VoiceUpscaler:
    """Speech super-resolution for every host take (ClearVoice MossFormer2_SR_48K, owner's pick 2026-10-03).

    A fresh take is upscaled from the engine's 24 kHz to 48 kHz before the room chain, and the cache stores the
    upscaled take, so cached lines cost nothing. One take at a time on the GPU; TTS_UPSCALE=false turns it off.
    """

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self):
        if not settings.TTS_UPSCALE or self._model is not None:
            return
        from services.audio_clearvoice_service import ClearVoice
        started = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self._model = ClearVoice(task='speech_super_resolution', model_names=[settings.TTS_UPSCALE_MODEL])
        log_service.success(f"✓ Voice upscaler ready ({settings.TTS_UPSCALE_MODEL}, "
                            f"{time.perf_counter() - started:.1f}s)")

    def upscale(self, pcm: bytes, rate: int) -> Tuple[bytes, int]:
        samples = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32) / 32768
        if len(samples) == 0:
            return pcm, rate
        wide = librosa.resample(samples, orig_sr=rate, target_sr=UPSCALE_RATE) if rate != UPSCALE_RATE else samples
        workdir = settings.VOICE_UPSCALE_DIR
        path = os.path.join(str(workdir), f"{uuid.uuid4().hex}.wav")
        try:
            with self._lock:
                os.makedirs(workdir, exist_ok=True)
                sf.write(path, np.pad(wide, (0, max(0, int(MIN_MODEL_SECONDS * UPSCALE_RATE) - len(wide)))),
                         UPSCALE_RATE)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    output = self._model(input_path=path, online_write=False)
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        upscaled = np.asarray(output, dtype=np.float32).squeeze()[:len(wide)]
        return (np.clip(upscaled, -1.0, 1.0) * 32767).astype(np.int16).tobytes(), UPSCALE_RATE

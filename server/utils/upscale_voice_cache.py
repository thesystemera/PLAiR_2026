import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC

from config.settings import settings
from services_radio.voice_upscale import UPSCALE_RATE, VoiceUpscaler

VOICE_CACHES = (settings.TTS_AUDIO_DIR, settings.PARALANGUAGE_AUDIO_DIR, settings.BREATH_AUDIO_DIR)


def upscale_file(upscaler: VoiceUpscaler, path: str) -> bool:
    info = sf.info(path)
    if info.samplerate >= UPSCALE_RATE:
        return False
    samples, rate = sf.read(path, dtype='int16')
    if samples.ndim > 1:
        samples = samples.mean(axis=1).astype(np.int16)
    tags = dict(FLAC(path))
    pcm, out_rate = upscaler.upscale(samples.tobytes(), rate)
    temporary = f"{path}.upscaling.flac"
    sf.write(temporary, np.frombuffer(pcm, dtype=np.int16), out_rate, format='FLAC', subtype='PCM_16')
    audio = FLAC(temporary)
    for key, value in tags.items():
        audio[key] = value
    audio.save()
    os.replace(temporary, path)
    return True


def main():
    parser = argparse.ArgumentParser(description='Upscale cached voice takes (sentences, paralanguage, breaths) in place')
    parser.add_argument('--dirs', nargs='*', default=[str(d) for d in VOICE_CACHES])
    args = parser.parse_args()

    upscaler = VoiceUpscaler()
    upscaler.load()
    if not upscaler.ready:
        print('Upscaler is off (TTS_UPSCALE=false); nothing to do')
        return
    files = [f for d in args.dirs for f in glob.glob(os.path.join(d, '*', '*.flac'))]
    started, done = time.perf_counter(), 0
    for number, path in enumerate(files, 1):
        try:
            done += upscale_file(upscaler, path)
        except Exception as e:
            print(f'failed {path}: {e}', flush=True)
        if number % 50 == 0:
            print(f'{number}/{len(files)} checked, {done} upscaled', flush=True)
    print(f'{done} of {len(files)} takes upscaled in {time.perf_counter() - started:.0f}s')


if __name__ == '__main__':
    main()

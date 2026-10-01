import argparse
import glob
import json
import os
import random
import re
import sys
import uuid
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import librosa
import numpy as np
import requests
import soundfile as sf
from mutagen.flac import FLAC
from pydub import AudioSegment
from sklearn.cluster import KMeans

from config.settings import settings
from services_radio.tts_generation_service import sanitize_clip_title

SR = settings.TTS_SAMPLE_RATE
HOP = 240
HOSTS = ('jess', 'leo')
MIN_GAP_S = 0.08
MIN_BREATH_S = 0.06
SILENT_DBFS = -65.0
DESCRIPTION = 'between-sentence breath'


def norm(word: str) -> str:
    return re.sub(r'[^a-z0-9]', '', word.lower())


def cached_lines(min_words: int, max_words: int):
    lines = set()
    for path in glob.glob(str(settings.TTS_AUDIO_DIR / '*' / '*.flac')):
        try:
            text = (FLAC(path).get('title') or [''])[0].strip()
        except Exception:
            continue
        if min_words <= len(text.split()) <= max_words and text[-1:] in '.!?':
            lines.add(text)
    return sorted(lines)


def clusters(lines, count: int, seed: int):
    from models_global import get_sentence_encoder
    encoder = get_sentence_encoder(settings.SEMANTIC_ENCODER)
    vectors = encoder.encode(lines, normalize_embeddings=True, convert_to_numpy=True, batch_size=64)
    kmeans = KMeans(n_clusters=count, random_state=seed, n_init=4).fit(vectors)
    groups = []
    for label in range(count):
        members = np.where(kmeans.labels_ == label)[0]
        if len(members) < 2:
            continue
        centre = members[np.argmax(vectors[members] @ kmeans.cluster_centers_[label])]
        groups.append((lines[centre], [lines[i] for i in members if i != centre]))
    return groups


def render(text: str, voice: str) -> np.ndarray:
    response = requests.post(f"{settings.TTS_SERVER_URL}/tts", timeout=180, json={
        'text': text, 'voice': settings.VOICE_PREFERENCES[voice]['voice'],
        'temperature': settings.VOICE_PREFERENCES[voice]['temperature']})
    response.raise_for_status()
    return np.frombuffer(response.content, dtype=np.int16).astype(np.float32) / 32768


def boundary(words, first: str, second: str):
    ref = [norm(w) for w in f"{first} {second}".split()]
    hyp = [norm(w.word) for w in words]
    last = len(first.split()) - 1
    for tag, i1, i2, j1, _ in SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
        if tag == 'equal' and i1 <= last < i2 - 1:
            index = j1 + (last - i1)
            return words[index].end, words[index + 1].start
    return None


def gap_breath(pcm: np.ndarray, whisper, first: str, second: str):
    segments, _ = whisper.transcribe(librosa.resample(pcm, orig_sr=SR, target_sr=16000),
                                     word_timestamps=True, language='en')
    edges = boundary([w for s in segments for w in s.words], first, second)
    if edges is None:
        return None, 'boundary not matched'
    gap = pcm[int(edges[0] * SR):int(edges[1] * SR)]
    if len(gap) < SR * MIN_GAP_S:
        return None, 'no gap'
    _, voiced, _ = librosa.pyin(gap, fmin=60, fmax=400, sr=SR, frame_length=1024, hop_length=HOP)
    lo, hi = 0, len(voiced)
    voiced_at = np.where(voiced)[0]
    if len(voiced_at):
        middle = len(voiced) // 2
        left, right = voiced_at[voiced_at < middle], voiced_at[voiced_at >= middle]
        lo = left.max() + 1 if len(left) else 0
        hi = right.min() if len(right) else len(voiced)
    breath = gap[lo * HOP:hi * HOP]
    if len(breath) < SR * MIN_BREATH_S:
        return None, 'all voiced'
    level = 20 * np.log10(np.sqrt(np.mean(breath ** 2)) + 1e-9)
    if level < SILENT_DBFS:
        return None, f'silent ({level:.0f} dBFS)'
    fade = int(0.008 * SR)
    envelope = np.ones(len(breath))
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)
    return breath * envelope, f'{len(breath) * 1000 // SR} ms, {level:.0f} dBFS'


def save(breath: np.ndarray, directory: str, context: str) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f'{uuid.uuid4()}.flac')
    sf.write(path, breath, SR, format='FLAC', subtype='PCM_24')
    audio = FLAC(path)
    audio['title'] = sanitize_clip_title(context)
    audio['description'] = DESCRIPTION
    audio.save()
    return path


def main():
    parser = argparse.ArgumentParser(description='Build a breath library from the gaps between two-sentence takes')
    parser.add_argument('--out', required=True)
    parser.add_argument('--per-host', type=int, default=100)
    parser.add_argument('--tries', type=int, default=3)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    random.seed(args.seed)

    lines = cached_lines(5, 20)
    groups = clusters(lines, args.per_host, args.seed)
    print(f'{len(lines)} lines in {len(groups)} conversation clusters')

    from faster_whisper import WhisperModel
    whisper = WhisperModel(settings.WHISPER_QUALITY_MODEL, device=settings.WHISPER_DEVICE,
                           compute_type=settings.WHISPER_COMPUTE_TYPE)
    manifest = []
    for host in HOSTS:
        kept = 0
        reel = AudioSegment.silent(500, frame_rate=SR)
        for number, (first, followers) in enumerate(groups, 1):
            for attempt, second in enumerate(random.sample(followers, min(args.tries, len(followers))), 1):
                breath, note = gap_breath(render(f'{first} {second}', host), whisper, first, second)
                print(f'{host} {number:3d}.{attempt} {note} | {first[:60]}', flush=True)
                if breath is None:
                    continue
                path = save(breath, os.path.join(args.out, host), first)
                manifest.append({'host': host, 'context': first, 'next': second, 'file': path, 'note': note})
                reel += AudioSegment.from_file(path) + AudioSegment.silent(700, frame_rate=SR)
                kept += 1
                break
        reel.export(os.path.join(args.out, f'reel_{host}.wav'), format='wav')
        print(f'{host}: {kept} of {len(groups)} clusters have a breath')
    with open(os.path.join(args.out, 'manifest.json'), 'w', encoding='utf8') as f:
        json.dump(manifest, f, indent=1)


if __name__ == '__main__':
    main()

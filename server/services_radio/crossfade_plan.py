from typing import Dict, List, Optional, Tuple

import numpy as np

from config import settings

SILENCE_DB = -45.0
BODY_DROP_DB = 6.0
SMOOTH_SEGMENTS = 10
VOCAL_GUARD_MS = 300
VOCAL_WORD_MAX_MS = 1000


def _levels(segments: List[Dict]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts = np.array([s['start'] for s in segments], dtype=float) * 1000
    ends = starts + np.array([s.get('duration', 0.1) for s in segments], dtype=float) * 1000
    db = np.array([s.get('loudness', -60.0) for s in segments], dtype=float)
    return starts, ends, db


def _smoothed(db: np.ndarray) -> np.ndarray:
    width = min(SMOOTH_SEGMENTS, len(db))
    padded = np.pad(db, (width // 2, width - 1 - width // 2), mode='edge')
    return np.convolve(padded, np.ones(width) / width, mode='valid')


def _shape(features: Dict) -> Optional[Dict[str, float]]:
    segments = features.get('loudness_segments') or []
    if len(segments) < SMOOTH_SEGMENTS:
        return None
    starts, ends, db = _levels(segments)
    sounding = np.nonzero(db > SILENCE_DB)[0]
    if not len(sounding):
        return None
    first_ms, last_ms = starts[sounding[0]], ends[sounding[-1]]
    body = np.median(db[sounding]) - BODY_DROP_DB
    full = np.nonzero(_smoothed(db) >= body)[0]
    if not len(full):
        return None
    return {
        'duration_ms': float(features.get('duration', 0)) * 1000,
        'first_ms': float(first_ms),
        'last_ms': float(last_ms),
        'body_end_ms': float(ends[full[-1]]),
        'rise_ms': float(max(0.0, starts[full[0]] - first_ms)),
        'tail_ms': float(max(0.0, last_ms - ends[full[-1]])),
    }


def vocal_span(timing: Optional[Dict]) -> Tuple[Optional[float], Optional[float]]:
    if not timing or (timing.get('alignment_score') or 0) < settings.TALK_ALIGNMENT_MIN:
        return None, None
    lines = [line for line in timing.get('lyrics') or []
             if isinstance(line.get('start'), (int, float))]
    if not lines:
        return None, None
    first = min(line['start'] for line in lines)
    last = max(_line_end(line) for line in lines)
    return first * 1000, last * 1000


def _line_end(line: Dict) -> float:
    words = [w for w in line.get('words') or [] if isinstance(w.get('start'), (int, float))]
    if words:
        word = max(words, key=lambda w: w['start'])
        end = word['end'] if isinstance(word.get('end'), (int, float)) else word['start']
        return min(end, word['start'] + VOCAL_WORD_MAX_MS / 1000)
    return line['end'] if isinstance(line.get('end'), (int, float)) else line['start']


def plan(current_features: Dict, next_features: Dict,
         current_timing: Optional[Dict] = None, next_timing: Optional[Dict] = None) -> Optional[Dict]:
    out_shape, in_shape = _shape(current_features), _shape(next_features)
    if not out_shape or not in_shape:
        return None

    lo, hi, floor = settings.CROSSFADE_MIN_MS, settings.CROSSFADE_MAX_MS, settings.CROSSFADE_FLOOR_MS
    last_ms, first_ms = out_shape['last_ms'], in_shape['first_ms']
    overlap = min(hi, max(lo, out_shape['tail_ms'], in_shape['rise_ms']))
    notes = [f"outro fade {out_shape['tail_ms'] / 1000:.1f}s", f"intro build {in_shape['rise_ms'] / 1000:.1f}s"]

    _, out_vocals_end = vocal_span(current_timing)
    if out_vocals_end is not None:
        vocals_end = min(out_vocals_end, out_shape['body_end_ms'])
        room = last_ms - vocals_end - VOCAL_GUARD_MS
        if room < overlap:
            overlap = max(floor, room)
            notes.append(f"vocals until {(last_ms - vocals_end) / 1000:.1f}s before the end")
    in_vocals_start, _ = vocal_span(next_timing)
    if in_vocals_start is not None:
        room = in_vocals_start - first_ms - VOCAL_GUARD_MS
        if room < overlap:
            overlap = max(floor, room)
            notes.append(f"vocals {max(0.0, in_vocals_start - first_ms) / 1000:.1f}s into the intro")

    start_ms = last_ms - overlap - first_ms
    if start_ms < 0 or start_ms > out_shape['duration_ms'] - 1000:
        return None
    fade_in = min(overlap, max(min(floor, overlap), overlap - in_shape['rise_ms']))
    fade_out = min(overlap, max(min(lo, overlap), out_shape['tail_ms']))

    return {
        'optimal_start_ms': round(start_ms),
        'duration_ms': round(overlap),
        'fade_out_delay_ms': round(first_ms + overlap - fade_out),
        'fade_out_ms': round(fade_out),
        'fade_in_delay_ms': round(first_ms),
        'fade_in_ms': round(fade_in),
        'curve': 'equal_power',
        'confidence': 'high' if out_vocals_end is not None or in_vocals_start is not None else 'medium',
        'reason': ', '.join(notes),
    }

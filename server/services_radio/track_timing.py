import asyncio
import json
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from config.settings import settings
from services_radio import crossfade_plan

OUTRO_SPAN = (0.85, 1.0)
INTRO_SPAN = (0.0, 0.15)
SPACE_START_PCT = 0.1
SPACE_END_PCT = 0.85
SPACE_LOUDNESS_PCT = 40
VOCAL_PAD_S = 0.4
LYRIC_ALIGNMENT_MIN = 0.3
LYRIC_SPAN_MIN = 0.3
PROFILES_KEPT = 4096

Span = Tuple[float, float, float]


@dataclass(frozen=True)
class TrackTiming:
    has_features: bool
    duration_ms: float
    shape: Optional[Dict[str, float]]
    vocals: Tuple[Optional[float], Optional[float]]
    vocals_start_s: Optional[float]
    outro_quiet: Tuple[Span, ...]
    intro_quiet: Tuple[Span, ...]
    vocal_spans: Optional[Tuple[Tuple[float, float], ...]]
    song_spaces: Tuple[Span, ...]
    section_starts: Tuple[float, ...]


_profiles: "OrderedDict[str, Tuple[tuple, TrackTiming]]" = OrderedDict()
_lock = threading.Lock()


def as_windows(spans: Tuple[Span, ...], offset_ms: float = 0.0) -> List[Dict]:
    return [{'start_ms': start + offset_ms, 'end_ms': end + offset_ms, 'duration_ms': duration}
            for start, end, duration in spans]


def quiet_segments(features: Dict, lyric_data: Optional[Dict], start_pct: float, end_pct: float,
                   loudness_pct: float = 30) -> Tuple[Span, ...]:
    duration = features.get('duration', 0)
    start_time = duration * start_pct
    end_time = duration * end_pct
    relevant = [s for s in features.get('loudness_segments', []) if start_time <= s['start'] < end_time]
    if not relevant:
        return ()
    threshold = np.percentile([s['loudness'] for s in relevant], loudness_pct)
    quiet = []
    quiet_start = None
    for seg in relevant:
        if seg['loudness'] < threshold:
            if quiet_start is None:
                quiet_start = seg['start']
        elif quiet_start is not None:
            if seg['start'] - quiet_start >= 1.0:
                quiet.append((quiet_start, seg['start'], seg['start'] - quiet_start))
            quiet_start = None
    if quiet_start is not None and end_time - quiet_start >= 1.0:
        quiet.append((quiet_start, end_time, end_time - quiet_start))
    lines = (lyric_data or {}).get('lyrics')
    if lines:
        quiet = [q for q in quiet if not any(q[0] <= line.get('start', 0) < q[1] for line in lines)]
    return tuple((start * 1000, end * 1000, length * 1000) for start, end, length in quiet)


def vocal_spans(lyric_data: Optional[Dict], duration_s: float) -> Optional[Tuple[Tuple[float, float], ...]]:
    lines = [l for l in (lyric_data or {}).get('lyrics') or [] if isinstance(l.get('start'), (int, float))]
    if not lines or not duration_s:
        return None
    if (lyric_data.get('alignment_score') or 0) < LYRIC_ALIGNMENT_MIN \
            or max(l.get('end') or l['start'] for l in lines) < duration_s * LYRIC_SPAN_MIN:
        return None
    return tuple(((l['start'] - VOCAL_PAD_S) * 1000, ((l.get('end') or l['start']) + VOCAL_PAD_S) * 1000)
                 for l in lines)


def merge_spaces(spans: Tuple[Span, ...]) -> List[List[float]]:
    merged: List[List[float]] = []
    for start, end, duration in sorted(spans, key=lambda s: s[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] = merged[-1][1] - merged[-1][0]
        else:
            merged.append([start, end, duration])
    return merged


def minus_vocals(spans: List[List[float]], spans_sung: Tuple[Tuple[float, float], ...]) -> List[Span]:
    pieces = []
    for start, end, _duration in spans:
        parts = [(start, end)]
        for v_start, v_end in spans_sung:
            parts = [p for a, b in parts for p in ((a, min(b, v_start)), (max(a, v_end), b)) if p[1] > p[0]]
        pieces += [(a, b, b - a) for a, b in parts]
    return pieces


def _vocals_start_s(lyric_data: Optional[Dict]) -> Optional[float]:
    if not lyric_data or (lyric_data.get('alignment_score') or 0) < settings.TALK_ALIGNMENT_MIN:
        return None
    starts = [line.get('start') for line in lyric_data.get('lyrics') or []
              if isinstance(line.get('start'), (int, float))]
    return float(min(starts)) if starts else None


def _stamp(path) -> Optional[Tuple[int, int]]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read(path, stamp) -> Optional[Dict]:
    if stamp is None:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def build(features: Optional[Dict], lyric_data: Optional[Dict]) -> TrackTiming:
    features = features or {}
    duration_s = features.get('duration', 0) or 0
    spans_sung = vocal_spans(lyric_data, duration_s) if features else None
    spaces = ()
    if features:
        quiet = quiet_segments(features, None, SPACE_START_PCT, SPACE_END_PCT, SPACE_LOUDNESS_PCT)
        spaces = tuple(s for s in minus_vocals(merge_spaces(quiet), spans_sung or ())
                       if s[2] >= settings.STINGS_MIDTRACK_MIN_WINDOW_S * 1000)
    return TrackTiming(
        has_features=bool(features),
        duration_ms=duration_s * 1000,
        shape=crossfade_plan.shape(features) if features else None,
        vocals=crossfade_plan.vocal_span(lyric_data),
        vocals_start_s=_vocals_start_s(lyric_data),
        outro_quiet=quiet_segments(features, lyric_data, *OUTRO_SPAN) if features else (),
        intro_quiet=quiet_segments(features, lyric_data, *INTRO_SPAN) if features else (),
        vocal_spans=spans_sung,
        song_spaces=spaces,
        section_starts=tuple(section.get('start') or 0 for section in features.get('sections') or []),
    )


def _profile(track_id: str) -> Optional[TrackTiming]:
    features_path = settings.AUDIOFEATURES_DIR / f"{track_id}.json"
    lyrics_path = settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"
    stamps = (_stamp(features_path), _stamp(lyrics_path))
    if stamps == (None, None):
        return None
    with _lock:
        cached = _profiles.get(track_id)
        if cached is not None and cached[0] == stamps:
            _profiles.move_to_end(track_id)
            return cached[1]
    timing = build(_read(features_path, stamps[0]), _read(lyrics_path, stamps[1]))
    with _lock:
        _profiles[track_id] = (stamps, timing)
        _profiles.move_to_end(track_id)
        while len(_profiles) > PROFILES_KEPT:
            _profiles.popitem(last=False)
    return timing


async def profile(track_id: Optional[str]) -> Optional[TrackTiming]:
    if not track_id or track_id == "N/A":
        return None
    return await asyncio.to_thread(_profile, track_id)

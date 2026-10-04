"""Context nodes: the track on air, its audio features, the queue and the playlist view."""
from typing import Dict, Optional
from services_radio.context_node_registry import node_registry
from services.catalog_vocals import VOCALS_TEXT
from config.settings import settings


@node_registry.register(
    "track_title_artist",
    "Current track title and artist name only",
    cost="low"
)
async def get_track_title_artist(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return "CURRENT TRACK: No track playing"

    name = current_track.get('name', 'Unknown')
    artist = current_track.get('artists', 'Unknown')
    return f"CURRENT TRACK: {name} by {artist}"


@node_registry.register(
    "track_release_date",
    "Release date/year of current track",
    cost="low"
)
async def get_track_release_date(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    release = current_track.get('release_date', 'N/A')
    return f"Released: {release}"


@node_registry.register(
    "track_duration",
    "Track length/duration",
    cost="low"
)
async def get_track_duration(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    duration = current_track.get('duration', 'N/A')
    duration_sec = current_track.get('duration_seconds', 0)
    return f"Duration: {duration} ({duration_sec}s)"


@node_registry.register(
    "track_progress",
    "Playback position and progress state",
    cost="low"
)
async def get_track_progress(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    progress_pct = current_track.get('progress_percentage', 0)
    progress_sec = current_track.get('progress_seconds', 0)
    duration_sec = current_track.get('duration_seconds', 0)

    if progress_pct < 10:
        state = "Just started"
    elif progress_pct < 25:
        state = "In the early stages"
    elif progress_pct < 50:
        state = "In the first half"
    elif progress_pct < 75:
        state = "In the second half"
    elif progress_pct < 90:
        state = "Nearing the end"
    else:
        state = "Almost finished"

    return (
        f"Progress: {progress_sec}s / {duration_sec}s "
        f"({progress_pct:.1f}% complete - {state})"
    )


@node_registry.register(
    "track_style_description",
    "Musical style and genre description",
    cost="low"
)
async def get_track_style_description(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    style = current_track.get('style_description', '').strip()
    if not style:
        return ""

    return f"Style: {style}"


@node_registry.register(
    "track_vocal_info",
    "Who sings (instrumental, male, female, duet)",
    cost="low"
)
async def get_track_vocal_info(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    vocals = VOCALS_TEXT.get(current_track.get('vocals'))
    return f"Vocals: {vocals}" if vocals else ""


@node_registry.register(
    "track_lyrics_preview",
    "Short 4-line lyrics preview",
    cost="medium"
)
async def get_track_lyrics_preview(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    if current_track.get('instrumental', False):
        return ""

    preview = current_track.get('lyrics_preview', '').strip()
    if not preview:
        return ""

    if len(preview) > 200:
        preview = preview[:200] + "..."

    return f"Lyrics Preview:\n{preview}"


@node_registry.register(
    "track_tempo",
    "Tempo/BPM of current track",
    cost="low"
)
async def get_track_tempo(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    tempo = features.get('tempo', 0)
    if tempo > 0:
        return f"Tempo: {tempo:.0f} BPM"
    return ""


@node_registry.register(
    "track_key_mode",
    "Musical key and mode",
    cost="low"
)
async def get_track_key_mode(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')

    if key == 'N/A' or mode == 'N/A':
        return ""

    mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
    return f"Key: {key} {mode_str}"


@node_registry.register(
    "track_energy_dance",
    "Energy and danceability scores",
    cost="low"
)
async def get_track_energy_dance(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    energy = features.get('energy', 0)
    dance = features.get('danceability', 0)

    if energy == 0 and dance == 0:
        return ""

    return f"Energy: {energy:.2f} | Danceability: {dance:.2f}"


@node_registry.register(
    "track_loudness",
    "Loudness in decibels",
    cost="low"
)
async def get_track_loudness(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    loudness = features.get('loudness', 0)

    if loudness == 0:
        return ""

    return f"Loudness: {loudness:.1f}dB"


@node_registry.register(
    "track_time_signature",
    "Time signature",
    cost="low"
)
async def get_track_time_signature(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    time_sig = features.get('time_signature', 4)
    return f"Time Signature: {time_sig}/4"


@node_registry.register(
    "track_valence",
    "Musical positivity/valence score",
    cost="low"
)
async def get_track_valence(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    valence = features.get('valence', 0)

    if valence == 0:
        return ""

    return f"Valence (Positivity): {valence:.2f}"


@node_registry.register(
    "track_dynamic_range",
    "Dynamic range of current track",
    cost="low"
)
async def get_track_dynamic_range(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    dynamic_range = features.get('dynamic_range', 0)

    if dynamic_range > 0:
        return f"Dynamic Range: {dynamic_range:.1f}"
    return ""


@node_registry.register(
    "track_beat_count",
    "Beat count of current track",
    cost="low"
)
async def get_track_beat_count(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    beat_count = features.get('beat_count', 0)

    if beat_count > 0:
        return f"Beat Count: {beat_count}"
    return ""


@node_registry.register(
    "track_audio_features_full",
    "All audio features in one shot",
    cost="medium"
)
async def get_track_audio_features_full(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"Audio Features: {', '.join(parts)}"


@node_registry.register(
    "queue_next_track",
    "The next track coming up",
    cost="low"
)
async def get_queue_next_track(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return "NEXT TRACK: Queue is empty"

    name = next_track.get('name', 'Unknown')
    artist = next_track.get('artists', 'Unknown')
    return f"NEXT TRACK: {name} by {artist}"


@node_registry.register(
    "queue_upcoming_track",
    "The track after next",
    cost="low"
)
async def get_queue_upcoming_track(upcoming_track: Optional[Dict] = None, **_) -> str:
    if not upcoming_track or upcoming_track.get('name') == 'N/A':
        return ""

    name = upcoming_track.get('name', 'Unknown')
    artist = upcoming_track.get('artists', 'Unknown')
    return f"UPCOMING TRACK (After Next): {name} by {artist}"


@node_registry.register(
    "history_last_track",
    "The previously played track",
    cost="low"
)
async def get_history_last_track(last_track: Optional[Dict] = None, **_) -> str:
    if not last_track or last_track.get('name') == 'N/A':
        return "LAST TRACK: No previous track"

    name = last_track.get('name', 'Unknown')
    artist = last_track.get('artists', 'Unknown')
    return f"LAST TRACK (Previously Played): {name} by {artist}"


@node_registry.register(
    "queue_playlist",
    "The playlist around now: the songs just played, what's on, and the songs coming up",
    cost="low"
)
async def get_queue_playlist(session_id: Optional[str] = None, playback_service=None, **_) -> str:
    from services_radio.dj_command_executor_playback import PLAYLIST_DISPLAY, SEED_MODE_DISPLAY
    state = playback_service.get_state(session_id, simplified=False) if playback_service and session_id else None
    queue = (state or {}).get("queue") or []
    if not state or not state.get("current_track") or not queue:
        return "PLAYLIST: Nothing is playing."
    index = state.get("current_index") or 0
    span = settings.DJ_PLAYLIST_VIEW_SONGS

    def line(offset: int, track: Dict) -> str:
        params = track.get("generation_params") or {}
        genre = (track.get("derived_tags") or {}).get("primary_genre") or ""
        slot = "now" if offset == 0 else f"{offset:+d}"
        return (f"{slot:>4} '{params.get('title') or 'Untitled'}' by {params.get('artist_name') or 'Unknown'}"
                + (f" ({genre})" if genre else ""))

    rows = [line(i - index, queue[i]) for i in range(max(0, index - span), min(len(queue), index + span + 1))]
    mode = state.get("activeSeedMode") or ""
    station = state.get("station_blend") or PLAYLIST_DISPLAY.get(mode) or (
        f"{SEED_MODE_DISPLAY[mode]} radio" if mode in SEED_MODE_DISPLAY else mode)
    return (f"PLAYLIST ({station}; negative = already played, positive = coming up):\n" if station
            else "PLAYLIST (negative = already played, positive = coming up):\n") + "\n".join(rows)


@node_registry.register(
    "queue_next_details",
    "Next track with full details",
    cost="medium"
)
async def get_queue_next_details(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return ""

    name = next_track.get('name', 'Unknown')
    artist = next_track.get('artists', 'Unknown')
    duration = next_track.get('duration', 'N/A')
    style = next_track.get('style_description', '').strip()

    result = f"NEXT TRACK: {name} by {artist} | Duration: {duration}"
    if style:
        result += f"\nStyle: {style}"

    return result


@node_registry.register(
    "queue_next_audio_features",
    "Audio features of next track in queue",
    cost="medium"
)
async def get_queue_next_audio_features(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return ""

    features = next_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"NEXT TRACK Audio Features: {', '.join(parts)}"


@node_registry.register(
    "queue_upcoming_audio_features",
    "Audio features of upcoming track (after next)",
    cost="medium"
)
async def get_queue_upcoming_audio_features(upcoming_track: Optional[Dict] = None, **_) -> str:
    if not upcoming_track or upcoming_track.get('name') == 'N/A':
        return ""

    features = upcoming_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"UPCOMING TRACK Audio Features: {', '.join(parts)}"


@node_registry.register(
    "history_last_audio_features",
    "Audio features of previously played track",
    cost="medium"
)
async def get_history_last_audio_features(last_track: Optional[Dict] = None, **_) -> str:
    if not last_track or last_track.get('name') == 'N/A':
        return ""

    features = last_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"LAST TRACK Audio Features: {', '.join(parts)}"

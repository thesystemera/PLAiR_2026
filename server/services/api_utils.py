from typing import Dict, Any

def simplify_track_info(track: Dict[str, Any], catalog_service=None) -> Dict[str, Any]:
    params = track.get("generation_params", {})
    track_info = track.get("track_info", {})
    track_id = track.get("id")

    has_artwork = False
    if catalog_service and track_id:
        has_artwork = catalog_service.has_artwork(track_id)
    elif "has_artwork" in track:
        has_artwork = track["has_artwork"]

    return {
        "id": track_id,
        "title": params.get("title", "Unknown"),
        "artist_name": params.get("artist_name"),
        "style": params.get("style", ""),
        "duration_ms": track_info.get("duration", 0),
        "has_artwork": has_artwork
    }
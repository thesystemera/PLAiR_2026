"""One progress message for the long jobs a listener waits on (song generation, music uploads): the job's status shape, the same over REST and the WebSocket, and the `task_progress` event that carries it."""
from typing import Any, Dict, List, Optional


def snapshot(job_id: str, job_type: str, state: str, current_stage: Optional[str], progress_percent: int,
             title: Optional[str], tracks: List[str], total_tracks: int, error: Optional[str] = None,
             **extra: Any) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "job_type": job_type,
        "status": state,
        "current_stage": current_stage,
        "progress_percent": progress_percent,
        "title": title,
        "tracks": list(tracks),
        "total_tracks": total_tracks,
        "completed_tracks": len(tracks),
        "error": error,
        **extra,
    }


def message(status: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": "task_progress", "data": {**status, "event": event}}


async def post_outcome(session_id: str, user_id: Optional[int], status: Dict[str, Any], label: str) -> None:
    from service_registry import services
    from services.catalog_credit import listener_label
    from services_radio.conversation_service import post_activity
    if status["status"] == "completed":
        catalog = services.catalog_service
        tracks = [catalog.get_track(track_id) for track_id in status["tracks"]] if catalog is not None else []
        names = ", ".join(listener_label(track) for track in tracks if track)
        await post_activity(session_id, user_id, f"{label} ready" + (f": {names}" if names else ""))
    elif status["status"] == "failed":
        refund = " Your credit was refunded." if status.get("refunded") else ""
        await post_activity(session_id, user_id, f"{label} failed: {status.get('error') or 'unknown error'}{refund}",
                            ok=False)

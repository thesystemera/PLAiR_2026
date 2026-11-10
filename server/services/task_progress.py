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

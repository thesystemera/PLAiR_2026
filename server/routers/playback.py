from fastapi import APIRouter, HTTPException, Depends

from services.user_data_cache_service import user_data_cache
from services import log_service
from service_registry import services
from routers.deps import get_session_info
from routers.schemas import PlayRequest, SeekRequest, QueueAddRequest

router = APIRouter()

@router.post("/api/playback/play")
async def play(
        request: PlayRequest,
        session: dict = Depends(get_session_info),
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    if user and request.track_id:
        banned_ids = await user_data_cache.get_banned_ids(int(user.id))
        if request.track_id in banned_ids:
            log_service.api(f"Blocked attempt to play banned track: {request.track_id}")
            raise HTTPException(status_code=403, detail="Cannot play banned track")

    assert services.playback_service is not None
    if services.radio_mode_service is not None:
        if request.track_id:
            await services.radio_mode_service.on_user_transport(session_id, "play")
        await services.radio_mode_service.on_transfer(session_id, session["device_id"])
    success = await services.playback_service.play(
        session_id, request.track_id, user_id=user_id, device_id=session["device_id"], claim=True
    )  # type: ignore
    if not success:
        log_service.error("Playback failed to start")
        raise HTTPException(status_code=400, detail="Failed to start playback")

    if services.announcer_service:
        services.announcer_service.monitor_session(session_id)  # type: ignore

    state = services.playback_service.get_state(session_id)  # type: ignore
    track_title = state.get("current_track", {}).get("generation_params", {}).get("title", "Unknown")
    log_service.api(f"[{session_id}/{session['device_id']}] Now playing: {track_title}")
    return {"status": "playing", "state": state}

@router.post("/api/playback/pause")
async def pause(session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert services.playback_service is not None
    await services.playback_service.pause(session_id)
    log_service.api(f"[{session_id}] Playback paused (acknowledged)")
    state = services.playback_service.get_state(session_id)  # type: ignore
    return {"status": "paused", "state": state}

@router.post("/api/playback/stop")
async def stop(session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert services.playback_service is not None
    await services.playback_service.stop(session_id)
    log_service.api(f"[{session_id}] Playback stopped")
    state = services.playback_service.get_state(session_id)  # type: ignore
    return {"status": "stopped", "state": state}

@router.post("/api/playback/seek")
async def seek(request: SeekRequest, session: dict = Depends(get_session_info)):
    session_id = session["session_id"]
    assert services.playback_service is not None
    success = await services.playback_service.seek(session_id, request.position_ms)
    if not success:
        log_service.error(f"[{session_id}] Seek failed: No track playing")
        raise HTTPException(status_code=400, detail="No track playing")
    log_service.api(f"[{session_id}] Seeked to {request.position_ms}ms")
    return {"status": "seeked", "position_ms": request.position_ms}

@router.post("/api/queue/add")
async def add_to_queue(
        request: QueueAddRequest,
        session: dict = Depends(get_session_info),
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None
    track_ids_to_add = request.track_ids

    if user:
        banned_ids = await user_data_cache.get_banned_ids(int(user.id))
        track_ids_to_add = [tid for tid in track_ids_to_add if tid not in banned_ids]

        if len(track_ids_to_add) < len(request.track_ids):
            banned_count = len(request.track_ids) - len(track_ids_to_add)
            log_service.api(f"[{session_id}] Filtered out {banned_count} banned track(s) from queue")

    assert services.playback_service is not None
    added = await services.playback_service.add_to_queue(
        session_id,
        track_ids_to_add,
        request.position,
        user_id=user_id
    )  # type: ignore
    log_service.api(f"[{session_id}] Added {len(added)} track(s) to queue")
    state = services.playback_service.get_state(session_id)  # type: ignore
    return {"added": added, "queue": state["queue"]}

@router.delete("/api/queue/remove/{track_id}")
async def remove_from_queue(
        track_id: str,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None  # type: ignore
    assert services.playback_service is not None
    success = await services.playback_service.remove_from_queue(session_id, track_id, user_id=user_id)
    if not success:
        log_service.error(f"[{session_id}] Remove from queue failed: Track {track_id} not in queue")
        raise HTTPException(status_code=404, detail="Track not in queue")
    log_service.api(f"[{session_id}] Removed track from queue: {track_id}")
    state = services.playback_service.get_state(session_id)  # type: ignore
    return {"removed": track_id, "queue": state["queue"]}

@router.post("/api/queue/seed")
async def seed_radio(
        request: dict,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    category = request.get("category", "all")
    track_id = request.get("track_id")

    if category in ["favorites", "discovery"] and not user:
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to use personalized radio modes."
        )

    assert services.playback_service is not None
    await services.playback_service.seed_radio(
        session_id=session_id,
        category=category,
        track_id=track_id,
        user_id=user_id
    )

    log_service.api(f"[{session_id}] Seeded radio with category: {category}")
    state = services.playback_service.get_state(session_id)  # type: ignore
    return {"status": "seeded", "category": category, "queue": state["queue"], "activeSeedMode": state.get("activeSeedMode")}

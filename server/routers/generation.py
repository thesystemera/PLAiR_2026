from fastapi import APIRouter, HTTPException, Depends

from services import log_service
from service_registry import services
from routers.deps import get_session_info
from routers.schemas import GenerateRequest

router = APIRouter()

def _owns_job(job_id: str, session: dict) -> bool:
    assert services.suno_generation_queue_service is not None
    user = session["user"]
    user_id = int(user.id) if user else None
    return services.suno_generation_queue_service.is_job_owner(job_id, session["session_id"], user_id)

@router.post("/api/generate")
async def generate_music(
        request: GenerateRequest,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]

    if not user:
        log_service.warning(f"[{session_id}] Guest user attempted to generate music")
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to generate music. Please create an account or sign in."
        )

    assert services.catalog_service is not None
    assert services.suno_generation_queue_service is not None
    original_params = None
    source_track_id = None
    if request.generation_type == 'similar':
        source_track_id = request.source_track_id
        if not source_track_id:
            raise HTTPException(status_code=400, detail="source_track_id required for similar generation")
        track = services.catalog_service.get_track(source_track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Source track not found")
        original_params = track.get("generation_params")
        if not original_params:
            raise HTTPException(status_code=400, detail="Source track has no generation parameters")
        log_service.api(f"Starting 'Similar' generation from track {source_track_id}")
    elif not request.user_request:
        raise HTTPException(status_code=400, detail="user_request required for new generation")
    else:
        log_service.api(f"Starting 'New' generation: {request.user_request}")

    job_id, error_msg, usage = await services.suno_generation_queue_service.request_generation(
        session_id=session_id,
        user=user,
        user_request=request.user_request if original_params is None else None,
        source_track_id=source_track_id,
        original_params=original_params
    )
    if error_msg:
        raise HTTPException(status_code=429, detail=error_msg)
    return {"status": "started", "job_id": job_id, "generation_usage": usage}

@router.get("/api/generation-jobs/{job_id}")
async def get_generation_job_status(
        job_id: str,
        session: dict = Depends(get_session_info)
):
    assert services.suno_generation_queue_service is not None
    if not _owns_job(job_id, session):
        raise HTTPException(status_code=404, detail="Job not found")
    status = services.suno_generation_queue_service.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")

    return status

@router.get("/api/generation-jobs")
async def get_all_generation_jobs(
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    assert services.suno_generation_queue_service is not None
    jobs = services.suno_generation_queue_service.get_all_jobs(session_id)

    return {
        "jobs": jobs,
        "count": len(jobs)
    }

@router.delete("/api/generation-jobs/{job_id}")
async def cancel_generation_job(
        job_id: str,
        session: dict = Depends(get_session_info)
):
    assert services.suno_generation_queue_service is not None
    if not _owns_job(job_id, session):
        raise HTTPException(status_code=404, detail="Job not found")
    success = await services.suno_generation_queue_service.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found")

    return {"status": "cancelled", "job_id": job_id}

from fastapi import APIRouter, HTTPException, Depends

from services.rate_limit_service import rate_limit_service
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

async def _reserve(user_id, session_id: str, user, batch_count: int) -> dict:
    allowed, error_msg, usage = await rate_limit_service.reserve_generations(
        user_id=user_id,
        session_id=session_id,
        user=user,
        cost=batch_count
    )
    if not allowed:
        log_service.warning(f"[{session_id}] Generation limit reached: {error_msg}")
        raise HTTPException(status_code=429, detail=error_msg)
    return usage

@router.post("/api/generate")
async def generate_music(
        request: GenerateRequest,
        session: dict = Depends(get_session_info)
):
    session_id = session["session_id"]
    user = session["user"]
    user_id = int(user.id) if user else None

    if not user:
        log_service.warning(f"[{session_id}] Guest user attempted to generate music")
        raise HTTPException(
            status_code=401,
            detail="You must be logged in to generate music. Please create an account or sign in."
        )

    generation_type = request.generation_type
    batch_count = request.batch_count
    source_track_id = request.source_track_id

    assert services.catalog_service is not None
    assert services.suno_generation_queue_service is not None
    if generation_type == 'similar':
        if not source_track_id:
            raise HTTPException(status_code=400, detail="source_track_id required for similar generation")

        track = services.catalog_service.get_track(source_track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Source track not found")

        original_params = track.get("generation_params")
        if not original_params:
            raise HTTPException(status_code=400, detail="Source track has no generation parameters")

        usage = await _reserve(user_id, session_id, user, batch_count)
        reservation = rate_limit_service.reservation_stamp()

        log_service.api(
            f"Starting 'Similar' generation from track {source_track_id}: "
            f"{batch_count} batches (~{batch_count * 2} tracks)"
        )

        try:
            job_ids, _ = await services.suno_generation_queue_service.start_generation_job(
                session_id=session_id,
                original_params=original_params,
                batch_count=batch_count,
                user_id=user_id,  # type: ignore
                source_track_id=source_track_id,
                reservation=reservation
            )
        except Exception:
            await rate_limit_service.refund_generations(user_id, session_id, batch_count, reservation)
            raise

        return {
            "status": "started",
            "job_ids": job_ids,
            "batch_count": batch_count,
            "expected_tracks": batch_count * 2,
            "generation_type": "similar",
            "generation_usage": usage,
            "source_track": {
                "id": source_track_id,
                "title": original_params.get("title")
            }
        }

    else:
        user_request = request.user_request
        if not user_request:
            raise HTTPException(status_code=400, detail="user_request required for new generation")

        usage = await _reserve(user_id, session_id, user, batch_count)
        reservation = rate_limit_service.reservation_stamp()

        log_service.system("=" * 60)
        log_service.system(f"USER REQUEST: {user_request}")
        log_service.system("=" * 60)

        log_service.api(
            f"Starting 'New' generation: {batch_count} batches (~{batch_count * 2} tracks)"
        )

        try:
            job_ids, _ = await services.suno_generation_queue_service.start_generation_job(
                session_id=session_id,
                original_params={},
                batch_count=batch_count,
                user_id=user_id,  # type: ignore
                source_track_id=None,
                user_request=user_request,
                reservation=reservation
            )
        except Exception:
            await rate_limit_service.refund_generations(user_id, session_id, batch_count, reservation)
            raise

        return {
            "status": "started",
            "job_ids": job_ids,
            "batch_count": batch_count,
            "expected_tracks": batch_count * 2,
            "generation_type": "new",
            "generation_usage": usage
        }

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

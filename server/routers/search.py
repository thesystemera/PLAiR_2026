from fastapi import APIRouter, Depends

from services.user_data_cache_service import user_data_cache
from services import log_service
from database import User
from service_registry import services
from routers.deps import get_session_info, get_current_user, RateLimit, enforce_rate_limit
from routers.schemas import SearchRequest

router = APIRouter()

@router.post("/api/search/semantic")
async def semantic_search(
        request: SearchRequest,
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        _rate_limit=Depends(RateLimit("search"))
):
    session_id = session["session_id"]
    user_id = int(current_user.id) if current_user else None  # type: ignore
    use_ai_analysis = bool(request.use_ai_analysis) and current_user is not None
    if use_ai_analysis:
        enforce_rate_limit("search_ai_user", f"user:{user_id}")

    banned_ids = set()
    if current_user:
        banned_ids = await user_data_cache.get_banned_ids(int(current_user.id))  # type: ignore

    assert services.vector_search_service is not None
    results = await services.vector_search_service.search(
        query=request.query,
        n_results=request.n_results or 10,
        instrumental=request.instrumental,
        vocal_gender=request.vocal_gender,
        use_ai_analysis=use_ai_analysis,
        banned_ids=banned_ids if banned_ids else None
    )

    if results:
        track_ids = [track["id"] for track in results[:request.n_results or 10]]
        assert services.playback_service is not None
        await services.playback_service.add_to_queue(session_id, track_ids, user_id=user_id)
        if track_ids:
            await services.playback_service.play(session_id, track_ids[0], user_id=user_id)
        log_service.api(f"Search → Queue: '{request.query}' - {len(track_ids)} tracks added, playing first")

    results = results[:request.n_results]

    assert services.catalog_service is not None
    for track in results:
        track["has_artwork"] = services.catalog_service.has_artwork(track["id"])  # type: ignore

    return {"results": results, "count": len(results)}

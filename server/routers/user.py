from fastapi import APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from services.profile_picture_service import profile_picture_service
from services import log_service
from config import settings
from database import get_db, User
from service_registry import services
from routers.deps import get_current_user, read_upload_limited
from routers.schemas import UserProfileUpdate

router = APIRouter()

@router.get("/api/user/profile")
async def get_user_profile(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert services.user_profile_service is not None
        return await services.user_profile_service.get_profile(int(current_user.id), db)  # type: ignore
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.put("/api/user/profile")
async def update_user_profile(
        updates: UserProfileUpdate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    changes = updates.model_dump(exclude_unset=True)
    try:
        assert services.user_profile_service is not None
        result = await services.user_profile_service.update_profile(
            int(current_user.id),  # type: ignore
            changes,
            db
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result

@router.post("/api/user/profile-picture")
async def upload_profile_picture(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert profile_picture_service is not None
        contents = await read_upload_limited(file, settings.MAX_PROFILE_PICTURE_BYTES)
        result = await profile_picture_service.upload_profile_picture(
            int(current_user.id), contents, file.filename or "upload.jpg", db  # type: ignore
        )
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log_service.error(f"Profile picture upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload profile picture")

@router.get("/api/user/{user_id}/profile-picture")
async def get_profile_picture(
        user_id: int,
        db: AsyncSession = Depends(get_db)
):
    assert profile_picture_service is not None
    file_path = await profile_picture_service.get_profile_picture_path(user_id, db)

    if not file_path:
        raise HTTPException(status_code=404, detail="Profile picture not found")

    return FileResponse(file_path, media_type="image/jpeg")

@router.get("/api/user/{user_id}/profile-picture/pack")
async def get_profile_picture_pack(
        user_id: int,
        db: AsyncSession = Depends(get_db)
):
    assert profile_picture_service is not None
    file_path = await profile_picture_service.get_pack_path(user_id, db)

    if not file_path:
        raise HTTPException(status_code=404, detail="Profile picture pack not found")

    return FileResponse(file_path, media_type="image/jpeg")

@router.delete("/api/user/profile-picture")
async def delete_profile_picture(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert profile_picture_service is not None
        result = await profile_picture_service.delete_profile_picture(int(current_user.id),  db)  # type: ignore
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

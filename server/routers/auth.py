from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from services import log_service
from services import auth_service
from database import get_db, User, WeatherData
from service_registry import services
from routers.deps import get_current_user, enforce_auth_rate_limit
from routers.usage import is_admin_user, usage_stats_visible
from routers.schemas import RegisterRequest, LoginRequest, AudioQualityRequest, UsernameUpdateRequest, ManageUserDataRequest

router = APIRouter()

@router.post("/api/auth/register")
async def register(request: RegisterRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    enforce_auth_rate_limit(http_request, request.username)
    try:
        user = await auth_service.register_user(db, request.username, request.password)
        if not user:
            log_service.error(f"Registration failed: Username '{request.username}' already exists")
            raise HTTPException(status_code=400, detail="Username already exists")

        token = auth_service.create_access_token({"sub": str(user.id)})
        log_service.api(f"User registered: {user.username}")
        return {
            "user": {"id": user.id, "username": user.username},
            "token": token
        }
    except ValueError as e:
        log_service.error(f"Registration validation failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log_service.error(f"Registration failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

@router.post("/api/auth/login")
async def login(request: LoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    enforce_auth_rate_limit(http_request, request.username)
    try:
        user = await auth_service.authenticate_user(db, request.username, request.password)
        if not user:
            log_service.error(f"Login failed: Invalid credentials for '{request.username}'")
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token = auth_service.create_access_token({"sub": str(user.id)})
        log_service.api(f"User logged in: {user.username}")
        return {
            "user": {"id": user.id, "username": user.username},
            "token": token
        }
    except HTTPException:
        raise
    except Exception as e:
        log_service.error(f"Login failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

@router.post("/api/auth/refresh")
async def refresh_token(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"token": auth_service.create_access_token({"sub": str(current_user.id)})}

@router.get("/api/auth/me")
async def get_me(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    weather_result = await db.execute(
        select(WeatherData)
        .where(WeatherData.user_id == user.id)
        .order_by(WeatherData.timestamp.desc())
    )
    weather_data = weather_result.scalar_one_or_none()

    return {
        "id": user.id,
        "username": user.username,
        "audio_quality": getattr(user, "audio_quality", "auto"),
        "tier": getattr(user, "tier", "basic"),
        "subscribed": getattr(user, "subscribed", False),
        "is_admin": is_admin_user(user),
        "usage_stats_visible": usage_stats_visible(user),
        "tts_muted": getattr(user, "tts_muted", False),
        "fps_enabled": getattr(user, "fps_enabled", False),
        "video_clips_enabled": getattr(user, "video_clips_enabled", False),
        "visual_quality": getattr(user, "visual_quality", "high"),
        "persona": user.persona,
        "profile": user.profile,
        "shoutout_interests": user.shoutout_interests,
        "profile_picture": user.profile_picture,
        "location": user.location,
        "timezone": user.timezone,
        "weather_description": weather_data.description if weather_data else None,
        "weather_timestamp": weather_data.timestamp.isoformat() if weather_data else None
    }

@router.put("/api/auth/audio-quality")
async def update_audio_quality(
        request: AudioQualityRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        assert services.user_profile_service is not None
        return await services.user_profile_service.update_audio_quality(
            int(current_user.id),  # type: ignore
            request.audio_quality,
            db
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/auth/update-username")
async def update_username(
        request: UsernameUpdateRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        assert services.user_profile_service is not None
        result = await services.user_profile_service.update_username(
            int(current_user.id),  # type: ignore
            request.username.strip(),
            db
        )
        return {"status": "success", **result}
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/manage_user_data")
async def manage_user_data(
        request: ManageUserDataRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    action = request.action

    if action == "delete_conversations":
        assert services.user_profile_service is not None
        await services.user_profile_service.delete_conversations(int(current_user.id), db)  # type: ignore
        log_service.api(f"User {current_user.username} deleted conversation history")

        assert services.websocket_service is not None
        await services.websocket_service.broadcast_to_session(str(int(current_user.id)), {  # type: ignore
            "type": "conversation_cleared",
            "data": {}
        })

        return {"success": True, "message": "Conversation history deleted successfully"}

    elif action == "reset_persona":
        assert services.user_profile_service is not None
        await services.user_profile_service.reset_persona(int(current_user.id), db)  # type: ignore
        log_service.api(f"User {current_user.username} reset persona, profile, and shoutout interests")
        return {"success": True, "message": "Persona, profile, and shoutout interests reset successfully"}

    else:
        raise HTTPException(status_code=400, detail="Invalid action")

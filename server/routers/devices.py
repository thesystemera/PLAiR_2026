from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services import log_service
from database import get_db, User
from service_registry import services
from routers.deps import get_session_info, get_current_user
from routers.schemas import ActivateDeviceRequest, RenameDeviceRequest
from routers.ws import DEVICE_ID_PATTERN

router = APIRouter()

@router.get("/api/devices")
async def get_user_devices(
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_device_id = session["device_id"]
    device_name = session["device_name"]
    device_type = session["device_type"]
    session_id = str(int(current_user.id))  # type: ignore

    try:
        assert services.device_management_service is not None
        await services.device_management_service.register_or_update_device(
            db=db,
            user_id=int(current_user.id),  # type: ignore
            device_id=current_device_id,
            device_name=device_name,
            device_type=device_type,
            set_active=False
        )
        log_service.api(f"Auto-registered/updated device: {device_name} ({current_device_id})")
    except Exception as e:
        log_service.warning(f"Failed to register device (will retry): {e}")

    try:
        removed = await services.device_management_service.cleanup_duplicate_devices(int(current_user.id),  db)  # type: ignore
        if removed > 0:
            log_service.system(f"Auto-cleaned {removed} duplicate devices for user {current_user.username}")
    except Exception as e:
        log_service.warning(f"Failed to cleanup duplicate devices: {e}")

    assert services.websocket_service is not None
    online_device_ids = list(set(services.websocket_service.get_online_device_ids(session_id)) | {current_device_id})

    try:
        devices = await services.device_management_service.get_user_devices(
            user_id=int(current_user.id),  # type: ignore
            db=db,
            online_device_ids=online_device_ids,
            only_online=True
        )
    except Exception as e:
        log_service.warning(f"Failed to get devices: {e}")
        devices = []

    for device in devices:
        device["is_current"] = device["device_id"] == current_device_id

    return {
        "devices": devices,
        "current_device_id": current_device_id
    }

@router.post("/api/devices/activate")
async def activate_device(
        request: Optional[ActivateDeviceRequest] = None,
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    session_id = session["session_id"]
    requester_device_id = session["device_id"]
    target_device_id = request.device_id if request and request.device_id else requester_device_id
    is_self_claim = target_device_id == requester_device_id

    if not DEVICE_ID_PATTERN.match(str(target_device_id)):
        raise HTTPException(status_code=400, detail="Invalid device id")

    assert services.websocket_service is not None
    if not is_self_claim and services.websocket_service.get_connection(session_id, target_device_id) is None:
        raise HTTPException(status_code=409, detail="Device is not online")

    device_id = str(target_device_id)
    device_name = session["device_name"] if is_self_claim else device_id

    if current_user:
        assert services.device_management_service is not None
        device = await services.device_management_service.register_or_update_device(
            db=db,
            user_id=int(current_user.id),  # type: ignore
            device_id=device_id,
            device_name=session["device_name"],
            device_type=session["device_type"],
            set_active=True
        )
        if device:
            device_name = str(device.display_name) if device.display_name else str(device.auto_name)

    assert services.playback_service is not None
    if services.radio_mode_service is not None:
        await services.radio_mode_service.on_transfer(session_id, device_id)
    await services.playback_service.transfer_playback(
        session_id,
        device_id,
        play=True if is_self_claim else None,
        requester_device_id=requester_device_id,
    )

    log_service.api(f"Device activated: {device_name} ({device_id}) by {requester_device_id}")

    return {
        "status": "activated",
        "device_id": device_id,
        "device_name": device_name
    }

@router.put("/api/devices/{device_id}/name")
async def rename_device(
        device_id: str,
        request: RenameDeviceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert services.device_management_service is not None
    device = await services.device_management_service.rename_device(
        user_id=int(current_user.id),  # type: ignore
        device_id=device_id,
        new_name=request.new_name,
        db=db
    )

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    log_service.api(f"Device renamed: {device_id} -> {request.new_name}")

    return {
        "status": "renamed",
        "device_id": device_id,
        "device_name": request.new_name
    }

@router.delete("/api/devices/{device_id}")
async def remove_device(
        device_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert services.device_management_service is not None
    removed = await services.device_management_service.remove_device(
        user_id=int(current_user.id),  # type: ignore
        device_id=device_id,
        db=db
    )

    if not removed:
        raise HTTPException(status_code=404, detail="Device not found")

    session_id = str(int(current_user.id))  # type: ignore
    assert services.websocket_service is not None
    ws_connection = services.websocket_service.get_connection(session_id, device_id)
    if ws_connection:
        try:
            await ws_connection.close()
        except (ConnectionError, OSError):
            pass
        services.websocket_service.unregister_connection(session_id, device_id)  # type: ignore

    log_service.api(f"Device removed: {device_id}")

    return {"status": "removed", "device_id": device_id}

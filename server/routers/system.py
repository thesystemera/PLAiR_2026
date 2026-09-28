import time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import User
from routers.deps import require_admin
from services.asset_integrity_service import asset_integrity_service

router = APIRouter()


class AssetDoctorScanRequest(BaseModel):
    repair: Optional[bool] = None
    track_ids: Optional[List[str]] = Field(default=None, max_length=500)
    reset_failures: bool = False


@router.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": time.time()
    }


@router.get("/api/admin/asset-doctor")
async def get_asset_doctor_report(_admin: User = Depends(require_admin)):
    return await asset_integrity_service.status()


@router.post("/api/admin/asset-doctor/scan")
async def trigger_asset_doctor_scan(request: AssetDoctorScanRequest, _admin: User = Depends(require_admin)):
    cleared = 0
    if request.reset_failures:
        if request.track_ids:
            for track_id in request.track_ids:
                cleared += asset_integrity_service.reset_failures(track_id)
        else:
            cleared = asset_integrity_service.reset_failures()
    if not asset_integrity_service.trigger_scan(repair=request.repair, track_ids=request.track_ids):
        raise HTTPException(status_code=409, detail="An asset scan is already running")
    return {"status": "started", "failures_cleared": cleared}

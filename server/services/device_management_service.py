from typing import Optional, List, Dict
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from database.models import UserDevice
from services import log_service

class DeviceManagementService:

    def __init__(self):
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        self._initialized = True
        log_service.success("✓ Device management service initialized")

    async def register_or_update_device(
        self,
        db: AsyncSession,
        user_id: int,
        device_id: str,
        device_name: str,
        device_type: str,
        set_active: bool = True
    ) -> UserDevice:

        if set_active:
            await db.execute(
                update(UserDevice).where(UserDevice.user_id == user_id).values(is_active=False)
            )

        result = await db.execute(
            select(UserDevice).where(
                UserDevice.user_id == user_id,
                UserDevice.device_id == device_id
            )
        )
        device = result.scalar_one_or_none()

        if device:
            device.is_active = set_active  # type: ignore
            device.last_active = datetime.now(timezone.utc)  # type: ignore
        else:
            device = UserDevice(
                user_id=user_id,
                device_id=device_id,
                auto_name=device_name,
                display_name="",
                device_type=device_type,
                is_active=set_active,
                last_active=datetime.now(timezone.utc)
            )
            db.add(device)

        await db.commit()
        await db.refresh(device)
        return device

    async def cleanup_duplicate_devices(self, user_id: int, db: AsyncSession) -> int:
        # pylint: disable=E1102
        result = await db.execute(
            select(UserDevice.device_id, func.count(UserDevice.id).label('count'))  # pylint: disable=E1102
            .where(UserDevice.user_id == user_id)
            .group_by(UserDevice.device_id)
            .having(func.count(UserDevice.id) > 1)  # pylint: disable=E1102
        )
        duplicates = result.all()

        removed_count = 0
        for device_id, _count in duplicates:  # noqa: F841
            result = await db.execute(
                select(UserDevice)
                .where(UserDevice.user_id == user_id, UserDevice.device_id == device_id)
                .order_by(UserDevice.last_active.desc())
            )
            devices = result.scalars().all()

            for device in devices[1:]:
                await db.delete(device)
                removed_count += 1

        if removed_count > 0:
            await db.commit()

        return removed_count

    async def get_user_devices(
        self,
        user_id: int,
        db: AsyncSession,
        online_device_ids: Optional[List[str]] = None,
        only_online: bool = True
    ) -> List[Dict]:

        result = await db.execute(
            select(UserDevice)
            .where(UserDevice.user_id == user_id)
            .order_by(UserDevice.last_active.desc())
        )
        all_devices = result.scalars().all()

        if online_device_ids is None:
            online_device_ids = []

        if only_online:
            devices = [d for d in all_devices if d.device_id in online_device_ids]
        else:
            devices = all_devices

        return [
            {
                "device_id": d.device_id,
                "device_name": str(d.display_name) if d.display_name else str(d.auto_name),  # type: ignore
                "device_type": d.device_type,
                "is_active": d.is_active,
                "is_online": d.device_id in online_device_ids,
                "last_active": d.last_active.isoformat() if d.last_active else None  # type: ignore
            }
            for d in devices
        ]

    async def rename_device(
        self,
        user_id: int,
        device_id: str,
        new_name: str,
        db: AsyncSession
    ) -> Optional[UserDevice]:

        result = await db.execute(
            select(UserDevice).where(
                UserDevice.user_id == user_id,
                UserDevice.device_id == device_id
            )
        )
        device = result.scalar_one_or_none()

        if device:
            device.display_name = new_name  # type: ignore
            await db.commit()
            await db.refresh(device)

        return device

    async def remove_device(
        self,
        user_id: int,
        device_id: str,
        db: AsyncSession
    ) -> bool:

        result = await db.execute(
            select(UserDevice).where(
                UserDevice.user_id == user_id,
                UserDevice.device_id == device_id
            )
        )
        device = result.scalar_one_or_none()

        if not device:
            return False

        await db.delete(device)
        await db.commit()
        return True

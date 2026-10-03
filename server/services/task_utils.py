import asyncio
from typing import Coroutine, Optional, Set

from services import log_service

_background_tasks: Set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task):
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log_service.error(f"[TASK] Background task '{task.get_name()}' failed: {type(exc).__name__}: {exc}")


def spawn(coro: Coroutine, name: Optional[str] = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


async def safe_background_task(coro, task_name="background_task"):
    try:
        await coro
    except Exception as e:
        log_service.error(f"{task_name} failed with exception: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")


def pending_task_count() -> int:
    return len(_background_tasks)

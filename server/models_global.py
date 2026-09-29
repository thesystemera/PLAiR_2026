import torch
from services import log_service
import asyncio
import functools
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


async def initialize_semantic_encoder():
    from config.settings import settings
    await asyncio.to_thread(get_sentence_encoder, settings.SEMANTIC_ENCODER)
    log_service.system(f"✓ Semantic encoder {settings.SEMANTIC_ENCODER} loaded on {device}")

def get_device():
    return device

_sentence_encoders = {}


def get_sentence_encoder(name: str):
    encoder = _sentence_encoders.get(name)
    if encoder is None:
        from sentence_transformers import SentenceTransformer
        encoder = _sentence_encoders[name] = SentenceTransformer(name, device=str(device or "cpu"))
    return encoder


_gpu_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-embed")

def get_gpu_executor() -> ThreadPoolExecutor:
    return _gpu_executor

async def run_on_gpu_executor(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_gpu_executor, functools.partial(fn, *args, **kwargs))
    return await loop.run_in_executor(_gpu_executor, fn, *args)

class GPUOutOfMemoryError(RuntimeError):
    pass

def is_cuda_oom(error: BaseException) -> bool:
    oom_type = getattr(torch.cuda, "OutOfMemoryError", None)
    if oom_type is not None and isinstance(error, oom_type):
        return True
    message = str(error).lower()
    return "cuda out of memory" in message or "cuda error: out of memory" in message

def raise_if_cuda_oom(error: BaseException, lane: str):
    if isinstance(error, GPUOutOfMemoryError):
        raise error
    if is_cuda_oom(error):
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        raise GPUOutOfMemoryError(f"{lane}: GPU out of memory") from error

_gpu_lease_lock: asyncio.Lock | None = None
_gpu_lease_holder: str | None = None

def _get_gpu_lease_lock() -> asyncio.Lock:
    global _gpu_lease_lock
    if _gpu_lease_lock is None:
        _gpu_lease_lock = asyncio.Lock()
    return _gpu_lease_lock

def gpu_lease_holder() -> str | None:
    return _gpu_lease_holder

@asynccontextmanager
async def gpu_lease(lane: str):
    global _gpu_lease_holder
    lock = _get_gpu_lease_lock()
    wait_started = time.monotonic()
    async with lock:
        waited = time.monotonic() - wait_started
        if waited > 1.0:
            log_service.system(f"[GPU] {lane} acquired GPU lease after {waited:.1f}s wait")
        _gpu_lease_holder = lane
        try:
            yield
        finally:
            _gpu_lease_holder = None

import asyncio
import contextvars
import functools
import os
from concurrent.futures import ThreadPoolExecutor

VOICE_THREADS = max(2, int(os.getenv("TTS_VOICE_THREADS", "8")))
_executor = ThreadPoolExecutor(max_workers=VOICE_THREADS, thread_name_prefix="tts_voice")


async def voice_thread(func, *args, **kwargs):
    context = contextvars.copy_context()
    call = functools.partial(context.run, func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_executor, call)

import sys
import time

import config
import llama_patches  # noqa: F401

import numpy as np
from orpheus_cpp import OrpheusCpp

TEXT = ("Yo, yo, yo! PLAiR dot FM, you are locked in! That was Nine Inch Nails, "
        "and trust me, we are just getting warmed up.")


def main():
    engine = OrpheusCpp(n_gpu_layers=-1, verbose=False, lang="en")
    llm = engine._llm
    prompt = f"<|audio|>leo: {TEXT}<|eot_id|><custom_token_4>"

    for _ in llm(prompt, max_tokens=20, stream=True, temperature=0.8):
        pass

    t0 = time.perf_counter()
    tokens = 0
    for _ in llm(prompt, max_tokens=400, stream=True, temperature=0.8, top_p=0.9, min_p=0.1):
        tokens += 1
    llm_s = time.perf_counter() - t0
    print(f"LLM only: {tokens} tokens in {llm_s:.2f}s = {tokens / llm_s:.1f} tok/s "
          f"(real-time needs ~82 tok/s) flash_attn={config.FLASH_ATTN}")

    codes = [np.random.randint(0, 4096, size=(1, n), dtype=np.int64) for n in (4, 8, 16)]
    names = [x.name for x in engine._snac_session.get_inputs()]
    feed = dict(zip(names, codes))
    engine._snac_session.run(None, feed)
    t0 = time.perf_counter()
    runs = 50
    for _ in range(runs):
        engine._snac_session.run(None, feed)
    per = (time.perf_counter() - t0) / runs * 1000
    print(f"SNAC decode ({config.SNAC_DEVICE}): {per:.1f} ms per call; one call per 7 tokens "
          f"-> {per * 82 / 7:.0f} ms of decode per second of audio")

    t0 = time.perf_counter()
    samples = 0
    for _sr, chunk in engine.stream_tts_sync(TEXT, options={"voice_id": "leo", "temperature": 0.8, "top_p": 0.9,
                                                            "min_p": 0.1, "max_tokens": 2000, "pre_buffer_size": 0}):
        samples += chunk.size
    total = time.perf_counter() - t0
    audio = samples / config.SAMPLE_RATE
    print(f"End-to-end: {audio:.1f}s audio in {total:.1f}s = RTF {total / audio:.2f}")


if __name__ == "__main__":
    sys.exit(main())

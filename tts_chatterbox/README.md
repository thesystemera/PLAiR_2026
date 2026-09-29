# PLAiR TTS engine (Chatterbox-Turbo)

The DJ voice engine. It runs as its own process with its own venv; the backend starts and stops it (`server/services_radio/tts_engine_bootstrap.py`) and talks to it over HTTP. It replaced Orpheus (`tts_server/`, kept in the repo but no longer loaded) on 2026-09-30. Research and measurements: `docs/TTS_ENGINE_RESEARCH.md`.

## API (same contract as the Orpheus engine)

| Endpoint | Description |
|---|---|
| `POST /tts` | `{"text", "voice", "temperature?", "top_p?", "seed?"}`. Streams raw PCM s16le, 24 kHz, mono as it is made. Headers `X-Job-Id`, `X-Sample-Rate`. |
| `POST /abort` | Cancels every queued and running job. |
| `POST /abort/<job_id>` | Cancels one job. |
| `GET /health` | `{"status", "workers", "active_jobs", "queue_depth", "voices", "sample_rate", "engine"}`. |

Voices: `jess` is the model's built-in voice; every `voices/<name>.wav` (a clean 10 s+ reference clip) is another voice (`leo`, `station`). Adding a voice is dropping in a WAV and restarting. Changing a voice means purging that host's cached clips.

## How it runs on the Quadro P6000

Pascal has no fast fp16, so the stock Hugging Face loop runs at ~49 tokens/s. Two changes make it fast without changing the maths:

- **`batch_t3.py`**: the GPT-2 speech-token model decodes up to 8 sentences at once, one CUDA graph per (slot count, KV length bucket), static KV cache, same sampling order as `T3.inference_turbo` (temperature, top-k 1000, top-p, repetition penalty). Each voice's 376-token conditioning prefix is computed once and reused. Verified token-for-token against the stock model (greedy). 1 slot 124 tok/s, 8 slots 727 tok/s (25 tok/s is real time).
- **`batch_vocoder.py`**: S3Gen (flow + HiFT) turns tokens into audio for every pending chunk in one batched call, any mix of voices. `s3_patches.py` replaces a mask helper that forced a GPU sync with an identical GPU-only version.
- **`engine.py`**: one scheduler loop owns the GPU. Sentences join free slots as they arrive; audio streams in growing chunks (20, 40, 80, 160 tokens), each re-vocoded from the start of the line with fixed noise and a 20 ms crossfade. First chunks and streams with under 1 s buffered are served before more tokens are generated. A length cap of `max(5 s, 3 s + 1 s/word)` stops runaway takes.

Measured (P6000, mixed male/female): first audio 0.41 s for one sentence; 8 at once 1.0 s first audio and ~6.4x real time total (Orpheus: ~1.1x).

The Perth watermark that stock Chatterbox adds is not applied.

## Setup (Windows)

```bat
C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe -m venv tts_chatterbox\.venv
tts_chatterbox\.venv\Scripts\python.exe -m pip install -r tts_chatterbox\requirements.txt
```

PyTorch must stay on a CUDA 12.x wheel (cu124): newer wheels dropped Pascal (sm_61). The model (`ResembleAI/chatterbox-turbo`) downloads to the Hugging Face cache on first start. Settings come from the repo-root `.env`: `TTS_SERVER_PORT` (8090), `TTS_CUDA_VISIBLE_DEVICES` (0 = P6000). Logs: `data/logs/tts_server.log`.

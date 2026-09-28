# PLAiR Orpheus TTS server

Local replacement for ElevenLabs. It runs as a separate process with its own venv, and the backend talks to it over HTTP.

- **Model:** Orpheus-3B finetune, quantised to GGUF. The default is Q4_K_M; `TTS_QUALITY` also accepts `FASTEST` (Q2_K) and `BEST` (Q8_0).
- **Runtime:** llama.cpp with CUDA, plus the SNAC 24 kHz decoder on ONNX Runtime.
- **Origin:** ported from LifeSpan's voice engine.

## API

| Endpoint | Description |
|---|---|
| `POST /tts` | Body `{"text", "voice", "temperature?", "top_p?", "min_p?", "max_tokens?", "seed?"}`. Streams raw PCM s16le, 24 kHz, mono. The response header `X-Job-Id` names the job. `seed` fixes the LLM sampling for reproducible takes; without it every request gets a fresh seed. |
| `POST /abort` | Cancels queued and running jobs. |
| `POST /abort/<job_id>` | Cancels one job. A running job stops within one token. |
| `GET /health` | Returns `{"status": "ok" \| "loading", "workers", "active_jobs", "queue_depth", "voices"}`. |

Voices: `tara`, `leah`, `jess`, `leo`, `dan`, `mia`, `zac`, `zoe`.

Emotion tags work inline in the text: `<laugh>`, `<chuckle>`, `<sigh>`, `<gasp>`, `<cough>`, `<sniffle>`, `<groan>`, `<yawn>`.

## Configuration

All settings come from the repo-root `.env`. Defaults are in `config.py`.

| Key | Default | Notes |
|---|---|---|
| `TTS_SERVER_PORT` | `8090` | Binds to `127.0.0.1` only. |
| `TTS_CUDA_VISIBLE_DEVICES` | falls back to `CUDA_VISIBLE_DEVICES` | Uses PCI order: `0` = Quadro P6000, `1` = Quadro RTX 6000. |
| `TTS_NUM_WORKERS` | `2` | Number of streams generated in parallel. All streams share one model copy and one llama.cpp context (one KV sequence each); about 5 GB VRAM in total at Q4 with 2 streams. The backend's `TTS_ENGINE_SLOTS` defaults to this value. |
| `TTS_QUALITY` | `AVERAGE` | Accepts `FASTEST`, `AVERAGE` or `BEST`. |
| `TTS_FLASH_ATTN` | `true` | |
| `TTS_SNAC_DEVICE` | `cuda` | Set to `cpu` only for debugging; CPU decoding roughly doubles the RTF. |

## Setup (Windows)

Install order matters. The numerical packages must be installed before llama-cpp-python is compiled. If they're installed or swapped afterwards, throughput silently halves. This lesson came from LifeSpan.

```bat
C:\Users\info\AppData\Local\Programs\Python\Python311\python.exe -m venv tts_server\.venv
tts_server\.venv\Scripts\python.exe -m pip install -r tts_server\requirements.txt
tts_server\.venv\Scripts\python.exe -m pip install --no-deps orpheus-cpp==0.0.3

set CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61;75 -T cuda="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.5"
set FORCE_CMAKE=1
tts_server\.venv\Scripts\python.exe -m pip install llama-cpp-python==0.3.22 --no-binary llama-cpp-python --no-cache-dir
```

Notes on the build:
- `-T cuda=...` points CMake at the CUDA MSBuild integration that ships with the toolkit. Without it, VS Build Tools fails with "No CUDA toolset found".
- The build takes about 20–40 minutes.
- Architectures `61;75` cover both the P6000 (Pascal) and the RTX 6000 (Turing).

**cuDNN pin (Pascal):** `nvidia-cudnn-cu12` is pinned to 9.10.2.21. cuDNN 9.26, which onnxruntime-gpu 1.26 pulls in by default, fails every SNAC conv on the Quadro P6000 (sm_61) with `CUDNN_BACKEND_API_FAILED`. If pip upgrades it, reinstall the pin with `--no-deps`.

The model files download to the Hugging Face cache on first run.

## How the engine runs

One thread drives llama.cpp for all streams, a second thread runs SNAC:

- **LLM thread.** Each job gets its own KV sequence and its own sampler chain (the same chain, seed derivation and prompt-prefix reuse that `Llama.create_completion` uses). Every step decodes one token for each active stream and samples it. Two streams are decoded in a single `llama_decode` call only while both need the same KV size and that size is at most 512 cells; otherwise each stream gets its own call. With flash attention on this GPU, a batched step at a larger KV size splits the attention reduction differently and changes the last bits of the logits. The rule keeps every stream's tokens bit-identical to what the old one-model-per-worker server produced.
- **SNAC thread.** It receives each 28-code window (the same windows `orpheus_cpp` built), decodes it and streams the PCM. The LLM therefore never waits for SNAC.
- **Streaming path.** Custom-token text is read from a per-token piece cache, which replaces the O(n²) re-detokenisation inside `create_completion`.

The SNAC decoder contains 20 `RandomNormalLike` noise layers. The audio is therefore stochastic by design: the same tokens decoded twice differ at about 30 dB SNR. To compare two builds sample for sample, fix both the LLM seed (`seed`) and ONNX Runtime's seed (`onnxruntime.set_seed`).

## Performance (Quadro P6000, Q4_K_M)

Real time needs ~82 tokens/s (7 tokens per 2048-sample frame at 24 kHz). Time per generated token, single stream:

| Stage | Before | Now |
|---|---|---|
| llama.cpp decode (GPU, ~90 % busy) | 12.0 ms | 12.0 ms |
| Sampling chain (156k-token vocab, CPU) | 1.1 ms | 1.1 ms |
| SNAC (6.6 ms per 7 tokens) | 0.9 ms, serial | overlapped in its own thread |
| Python streaming overhead | 0.9 ms, grows with length | ~0.1 ms |
| Tokens/s, one stream | ~66 | ~74 |
| RTF, one stream | ~1.35 | ~1.2–1.3 |
| Two concurrent streams, combined audio-s per s | ~0.75–0.87 | ~0.95–1.15 |
| RTF per stream while two run | ~1.9–2.6 | ~1.5–2.1 |
| First audio while two run | ~770 ms | ~580 ms |

The decode is kernel-bound: the GPU is ~90 % busy, clocks are at maximum, and the CPU thread count makes no difference. CUDA graphs are compiled in, but ggml disables them before Ampere. Patching that on and rebuilding could save at most the ~10 % idle gap, so it has not been tried. Flash attention off (`TTS_FLASH_ATTN=false`) gives no end-to-end gain and changes the tokens.

Profile the raw components with `tts_server\.venv\Scripts\python.exe tts_server\profile_engine.py`. It loads its own model copy, so check free VRAM first.

## Running

The backend starts this server automatically: see `server/services_radio/tts_engine_bootstrap.py`.

To manage it yourself, run `tts_server\start_tts_server.bat` and set `TTS_SERVER_EXTERNAL=true` in `.env`.

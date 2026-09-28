# AGENTS.md

Agent guidance for this repository lives in **[CLAUDE.md](CLAUDE.md)** — read it in full before making changes. It is the single maintained source; this file only exists so tools that look for `AGENTS.md` find it.

Non-negotiables, in brief:

- **Stack:** React + Vite client (`client/`), FastAPI backend (`server/`), PostgreSQL 18 (port 5433, 4 databases), Google Gemini via `google-genai`, local Orpheus TTS engine (`tts_server/`, replaces ElevenLabs). Config comes from the repo-root `.env`.
- **Frontend state:** engines report to `UIStateContext` via `reportEngineStatus()`; components read state and never prop-drill it. Use `api.js` for backend calls, `safeStorage` for localStorage, `useWebSocketSubscribe()` for socket events, `useViewport()` for responsive state.
- **Playback:** backend `playback_state.py` is the source of truth; never auto-advance when `active_device_id` is set; devices default to inactive and activate only on an exact `active_device_id === deviceId` match.
- **GPU:** everything runs on the Quadro P6000 via `CUDA_VISIBLE_DEVICES`; never hard-code GPU indexes; no float16 for Whisper.
- **Machine safety:** port 8000 is public (nginx → plair.live); test on `HOST=127.0.0.1 PORT=8011`. Never run `external_components/restart_all.bat` casually — it kills every python/node/nginx/java process on a shared host.
- **Style:** no explanatory code comments; use relative paths when editing on Windows.

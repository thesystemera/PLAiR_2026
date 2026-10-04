# GPU benchmarks for the lit artwork and the glass

Headless Chrome on a real graphics card, phone-sized, with per-pass GPU timing, main-thread traces and pixel checks.
Built on 4 Oct 2026 while speeding up the lit artwork (`docs/HANDOVER_2026-10-04_LIT_ARTWORK.md`).

**Pick the P6000, never the RTX 6000.** The display runs on the RTX 6000, so Chrome would take it by default. Find the
P6000's adapter LUID with `python list_gpus.py` (it changes on reboot) and pass it as `--luid high,low` (or `LUID=` for
`pagerun.mjs`). Every script checks the WebGL renderer string when given `--gpu P6000` and aborts on the wrong card.

| Script | What it does |
|---|---|
| `gpubench.mjs` | Phone emulation (412x915 at 2.625x), modes `light` / `tilt` / `tilt+light` / `still`, per-pass GPU ms via timer queries (scene, capture, probe, tiles, Now Playing), stalls (`getBufferSubData` etc.), optional `--trace 1` (main thread / GPU process per frame), `--profile 1` (JS hot spots), `--shots dir --shotlight 1`. `--uncapped 1` keeps the GPU at full clock (needed for fair GPU timings). `--cpu 4` approximates a slow phone CPU. |
| `togglebench.mjs` | Desktop size: closes and opens a panel (`--panel queue`), records every frame, traces and profiles. `--light 1` forces the light on. |
| `pagerun.mjs` | Opens one page (e.g. `identity.html`), traces main-thread GPU waits, prints a JS expression. `SWIFTSHADER=1` uses the software GPU. |
| `identity.html` | Renders real covers with the live shader (full pass, cache + re-light) and the original shader (`/old/depthArtShader.js`, from `git show <rev>:client/src/lib/depthArtShader.js` plus `lightProbe.js` into an `old/` folder here) and counts differing pixels; times the full pass with and without the depth bound. `?ids=a,b,...` |
| `pyramid.html` | Checks the max-depth pyramid level by level against a CPU reduction. `?id=...` |
| `serve.mjs` | `node serve.mjs <dir> <port>`: serves a client build or this folder, proxies `/api`, `/ws`, `/track` to the backend, `/src/...` from `client/src`. |

Build a client to test without touching production: `npx vite build --outDir <scratch dir> --emptyOutDir`, then
`node serve.mjs <scratch dir> 4103` and pass `--urls http://127.0.0.1:4103,https://plair.live` to compare with live.

Notes: headless Chrome is not frame-capped when `--uncapped 1` is given, and at 60 fps the P6000 drops to low clocks
(per-draw times look 5x higher), so compare builds under the same settings, alternating, several times. Chrome is
killed by its profile path (`killchrome.mjs`): `child.kill()` leaves the browser running.

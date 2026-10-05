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

## The owner's phone over USB (5 Oct 2026)

`adb forward tcp:9222 localabstract:chrome_devtools_remote` (adb from Google's platform-tools; set `ADB=<path to adb.exe>`),
PLAiR open in Chrome on the phone and in front (a hidden tab stops its frame loop and the scripts time out).

| Script | What it does |
|---|---|
| `phone.mjs busy <s> <label>` | GPU busy % from the Adreno driver (`/sys/class/kgsl/kgsl-3d0/gpubusy`, readable without root) plus rAF fps. Busy % saturates near 100 and the GPU clock moves with load, so compare variants back to back. |
| `phone.mjs gpu <s> <label>` | GPU ms of the scene's capture and main pass, timed with 1-pixel `readPixels` around them (`finish()` returns early in Chrome). `REPEAT=n` draws the main pass n times per frame to keep the clock up. |
| `phone.mjs trace <s> [n]` | Chrome trace from the browser target: busy ms/s per thread, top events. `PASSES=<label>` prints Viz render passes per frame instead (each CSS mask over a composited scroller is one extra panel-sized pass every frame). |
| `phone.mjs css '<rules>'`, `eval`, `layers`, `reload`, `tap x,y` | Live CSS ablation, page eval, composited layer list, reload, trusted tap (unlocks audio after a reload). |
| `canvastest.mjs '<json>'` | Bare full-screen WebGL canvas in a new tab (`dpr`, `alpha`, `desync`, `overlay`), GPU busy while it clears every frame. |
| `drawcount.js` | `node phone.mjs eval "$(cat drawcount.js)"`: WebGL calls per canvas per second. |

Page hooks for ablation: `__plairScene.skip / force / uniforms {name: value} / timing`, `__plairQuality.override({ level, sceneDpr, glassTaps, ... })`.

Measured on the Redmi Note 13 4G (Adreno 610), High, Radio tab, music playing: Chrome on Android throttles main-thread
frames to 60 Hz unless the screen is touched (`ThrottleMainFrameTo60Hz`); the background canvas changing every frame
makes Chrome re-composite the whole 1080x2400 screen (canvas clearing only, no scene: 66% busy vs 12% with the canvas
still); masks: 93 -> 85% busy, fps 55 -> 60; main scene pass ~11 ms GPU. `desynchronized: true` cut the compositing
cost to ~2% but showed half-drawn tiles (specks), so it is not usable.

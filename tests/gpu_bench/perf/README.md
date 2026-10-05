# Phone performance scripts (5 Oct 2026)

Node scripts that drive PLAiR on the owner's phone over USB through Chrome DevTools. How and why to use them:
`docs/NEXT_SESSION_PERFORMANCE.md` section 5.

Setup: `adb forward tcp:9222 localabstract:chrome_devtools_remote`. Some scripts hard-code the adb path of an
old scratchpad or `E:/AI_RADIO/client/dist`; adjust before running.

- `cdp.mjs` - shared helper: attach to the plair.live tab, `send`, `evaluate`, `sleep`.
- `interleave.mjs <panel> '<variants json>' <rounds> [reload]` - interleaved A/B under simulated hand-held tilt; medians.
- `tiltsurvey.mjs <panels> <seconds>` - fps per panel under simulated tilt, with the renderer's draw-mode counts.
- `slope.mjs <panel> '<variants json>' [reload]` - slope method (extra passes) for GPU cost per stage.
- `soak.mjs <rounds>` - fresh reload, then motion on Now Playing, watching for WebGL context loss.
- `catwhy2.mjs <px/s>` - catalog fling, classifies every blank tile per frame (no data / no pack / decoding / ready not drawn).
- `fadeab.mjs` - skips with the theme transitions on vs off.
- `renders2.mjs <panel>` + `renderhook.js` - React re-render counts after a skip, charged to the root of each cascade.
- `cpuprof.mjs`, `cpucallers.mjs` - CPU profile of a skip, top functions and who calls the layout-forcing getters.
- `loaf.mjs` - long-animation-frame entries after a skip.
- `skiptrace.mjs` - Chrome trace of a skip, summarised in memory (write large traces to E:, never to C:).

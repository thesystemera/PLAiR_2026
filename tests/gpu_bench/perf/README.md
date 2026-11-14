# Phone performance scripts (5 Oct 2026)

Node scripts that drive PLAiR on the owner's phone over USB through Chrome DevTools. How and why to use them:
`docs/NEXT_SESSION_PERFORMANCE.md` section 5.

Setup: `adb forward tcp:9222 localabstract:chrome_devtools_remote`. Some scripts hard-code the adb path of an
old scratchpad or `E:/AI_RADIO/client/dist`; adjust before running.

- `state.mjs` - what the phone's tab is doing right now (quality level and levers, scene thread, cover stats, canvases); changes nothing.
- `survey.mjs [panels] [seconds]` - per panel: frames per second and slow frames at rest, then under simulated tilt.
- `ablate.mjs <panel> [rounds] [rest]` - frames per second with coarse covers, flat covers and no scene, interleaved. Frame rate moves in steps of one screen refresh (8.3 ms at 120 Hz), so a saving that does not cross a step shows nothing here: use `budget.mjs` / `frame.mjs` for milliseconds.
- `budget.mjs <panel> [rounds] [extra]` - GPU ms per frame of the scene's main pass and of the covers (full and coarse) by the slope method.
- `frame.mjs <panel> '<json configs>' [rounds]` - settled average frame time for any set of renderer / scene switches plus optional page code (live CSS), interleaved; load a stage with extra copies so the frame is GPU-bound, then compare.
- `gpucaps.mjs` - the phone GPU's float render-target support and whether the coarse cover targets are complete.
- `surfaces.mjs [panel]` - on-screen elements whose CSS makes Chrome draw through an off-screen texture (mask, backdrop-filter, filter, opacity, rounded clips over a canvas).
- `vizframe.mjs [panel]` - one compositor frame: render passes with their rectangles, and the layers.
- `cssab.mjs <panel> '<json {label: css rules}>' [rounds] [extra]` - GPU cost of page styling: the frame is loaded so it is GPU-bound, each set of CSS rules is injected in turn and the frame time compared with "as is".
- `toggle.mjs [button title] [rounds] [panel]` - opens and closes a player panel (default "Manage devices") and prints frames drawn, average and worst frame and long frames.
- `touchfps.mjs [panel] [seconds] [x,y]` - frames per second without and with a synthetic finger moving on the screen (Chrome holds page frames to 60 Hz unless the screen is touched).
- `copycost.mjs [size] [rounds] [targets]` - cost of WebGL-to-2D-canvas copies, the way covers reach the screen (10 small covers: about 0.7 ms a frame).
- `scenecopy.mjs [rounds]` - cost of copying out of the live background canvas (about 1.7 ms a frame for the first copy; why edge-fade strips fed from the scene were rejected).
- `fillcheck.mjs <out dir> [dark]` + `fillcheck_diff.py <dir>` - pixel check on the phone of the mask-free fills against the masked originals; the diff script compares any `*_old.png` / `*_new.png` pair.
- `tab.mjs open <url> | front <part> | close <part> | list` - tabs in the phone's Chrome. The owner runs PLAiR as an installed app, so a second tab is confusing for them: prefer deploying.
- `cdp.mjs` - shared helper: attach to the plair.live tab (`PAGE=<part of another address>`), `send`, `evaluate`, `sleep`, and `openPanel`, which never taps the tab that is already open (that toggles Radio between Talk and Text and the Catalog between tracks and shoutouts; a script did exactly that to the owner's setting on 14 Nov).
- `interleave.mjs <panel> '<variants json>' <rounds> [reload]` - interleaved A/B under simulated hand-held tilt; medians.
- `tiltsurvey.mjs <panels> <seconds>` - fps per panel under simulated tilt, with the renderer's draw-mode counts.
- `slope.mjs <panel> '<variants json>' [reload]` - slope method (extra passes) for GPU cost per stage.
- `ctxloss.mjs '<variants json>' <rounds> [panel]` - reload, then interleaved switches under simulated tilt, logging every WebGL context created or lost; stops at the first real loss. Run `adb logcat` alongside: Chrome logs the GPU reset reason there.
- `stages.mjs '<stage,stage>' <rounds> [extra passes]` - GPU ms per cover pass on Now Playing by the slope method (forced full pass + N extra passes, fixed tilt, fixed light level, settles after each shader swap), for shader variants from `edits.mjs` (live source edits, no build); interleaved rounds, medians.
- `identity.mjs '<stage,stage>' <out dir> [panel]` - reads the largest on-screen cover's own canvas with tilt and lights frozen and compares every pixel to the first shot (mean, max, % of pixels off by >2 / >8), saving PNGs and x8 diff images. Put `reference` first to score variants against a 96-step march.
- `edits.mjs` - the shader variants both use (`as is`, `lights off`, `no parallax`, `reference`, ...).
- `soak.mjs <rounds>` - fresh reload, then motion on Now Playing, watching for WebGL context loss.
- `catwhy2.mjs <px/s>` - catalog fling, classifies every blank tile per frame (no data / no pack / decoding / ready not drawn).
- `fadeab.mjs` - skips with the theme transitions on vs off.
- `renders2.mjs <panel>` + `renderhook.js` - React re-render counts after a skip, charged to the root of each cascade.
- `cpuprof.mjs`, `cpucallers.mjs` - CPU profile of a skip, top functions and who calls the layout-forcing getters.
- `loaf.mjs` - long-animation-frame entries after a skip.
- `skiptrace.mjs` - Chrome trace of a skip, summarised in memory (write large traces to E:, never to C:).

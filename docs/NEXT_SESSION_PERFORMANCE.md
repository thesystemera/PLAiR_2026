# Next session: 60 fps on the owner's phone (start here)

Paste this as the first message of a new session. Read `CLAUDE.md` first (section 8 covers the cover system).

## The goal (owner's words, 5 Oct)

A steady **60 fps on the current settings (High), while the phone is held and moving and the catalog is
scrolled**, with no loss of visual flair: no resolution cuts, no fps caps, no removing effects, the dynamic theme
fade stays. Optimise the work, never the look.

## 1. FIRST: the page goes black (WebGL context loss)

On 5 Oct, after about a minute of interleaved A/B runs on the phone (`tests/gpu_bench/perf/interleave.mjs`
toggling `skipCopy` / light), **both WebGL contexts died together**: the background scene
(`__plairScene.eval('return scene.renderer.getContext().isContextLost()')` -> true) and the cover renderer
(`__plairArt.stats().textureMB` -> 0). The owner saw "everything black, no graphs, no background". Once it
also left `window.__plairScene` undefined. A reload fixes it; on one reload the scene came up in its static
fallback ("WebGL2 unavailable").

- It happened twice. The first time I had been recompiling shaders and recreating the cover context with
  debug switches; the second time I had not. A 72 s soak with motion only and no switches
  (`perf/soak.mjs`) did NOT lose context. So it may be the debug switches, or something new in today's build.
- New today and suspect: the cover atlas is now a regular canvas with `preserveDrawingBuffer: true`
  (`depthArtRenderer.createAtlas`, commit f1dffda); half-precision shading (`MEDIUMP_SHADING`); one march step
  per device pixel on High (`QualityContext` high `parallaxStepPx: 1`); covers drawn ahead of the viewport
  (720edba); four pack sizes at native size (more texture memory: ~100-110 MB in `__plairArt.stats()`).
- Both contexts dying at once points at the GPU process (out of memory, or a GPU hang and reset), not one
  context. Measure Chrome's GPU process memory while reproducing:
  `adb shell dumpsys meminfo --package com.android.chrome` (the `privileged_process` entry: `GL mtrack`,
  `EGL mtrack`; 371 MB was seen on 5 Oct morning). My quick watcher (`memwatch.sh` in the old scratchpad)
  parsed the columns wrong; redo it. Also watch for its pid changing (a restart).
- Reproduce, find the cause, fix it before anything else. Until then, every fps number taken after a run of
  switches is suspect: **always confirm covers are drawing (`frames > 0` in the profile, `textureMB > 0`) and
  the scene context is alive before trusting a high fps.** I reported a false "60 fps everywhere" once because
  nothing was rendering.

## 2. What was done on 5 Oct (all pushed, live)

| Commit | What |
|---|---|
| 2fb2a75 | One cover system: every cover and avatar draws from server-prepared packs (256/512/768/1024, rendered ahead, backfilled at startup); each cover measures its own size and loads the pack that covers it; one cache per size; no frontend resizing, no plain-JPEG path anywhere; packs decoded in `packDecodeWorker`; theme colours as CSS variables; gradient + note placeholder back; `TrackArtCrossfade` for the player and Now Playing (outgoing cover frozen during the fade). |
| 9dc9667 | Queue `AnimatePresence` `presenceAffectsLayout={false}` (every queue render recreated every row's presence context); narrow engine selectors. |
| e9e1556 | Pub/sub everywhere: every `useUISelector` selects only the fields it reads; `useRadioUI` removed (`useRadioButton`); `check:ui-state` fails on a whole-object selector. Re-renders after a skip 1773 -> 1165. |
| 720edba | Covers drawn ahead of the viewport by scroll speed (10 frames of motion, 0.5-2 viewports, closest first, 4 per frame); placeholder fades to the cover in `--dur-quick`. |
| 8d8dd3a | Parallax redraw threshold 0.1 -> 0.5 device px (sensor jitter forced a full pass every frame). At rest: Now Playing 29 -> 59 fps, Catalog 32 -> 54. |
| f1dffda | Atlas as a regular canvas (transferring an OffscreenCanvas frame cost a fresh buffer per frame); half-precision lighting/colour maths; High marches one step per device pixel (pixel diff within the noise floor). Under motion: Catalog ~35 -> ~47, Now Playing ~33 -> ~37-46. |

Also, outside git: nginx now 301-redirects `www.plair.live` to `plair.live` (the plair block of
`C:\nginx\conf\nginx.conf`; a backup is in `data/nginx.conf.bak_2026-10-05` or the old scratchpad). The owner's
"missing photo" was the phone signed into the typo account `thhesystemera` (user 7) on `www`.

## 3. Where the time goes (measured on the phone, interleaved A/B)

Phone: Redmi Note 13 4G, Adreno 610, High quality, DPR 2.88, **always at thermal status 3** (the owner's normal
use; do not blame or report it, design the test around it).

- **At rest**: ~59 fps on every panel.
- **Under hand-held motion** (DevTools orientation wobble, below): Radio ~60, Queue ~56, Catalog ~47,
  Now Playing ~36-46.
- **The background scene is not the bottleneck** (the owner was right: it renders behind every panel and
  Radio holds 60 under motion). The **lit covers** are: covers flat -> 60 everywhere.
- Under motion every visible cover re-runs the full parallax pass every frame. Now Playing's cover is ~1M px
  (989x989). **One extra full pass costs ~14-17 ms** on this GPU (slope method). Breakdown of the full pass on
  Now Playing (forced full every frame, interleaved): lighting ~4.4 ms, march steps ~3.4 ms (since reduced by
  one step per pixel), copying the drawn cover into its own canvas ~3.9 ms (measured before the atlas change;
  re-measure, the clean re-measure was ruined by the context loss).
- Not worth it (measured): explicit `textureLod` sampling (no change, removed); split passes (cache + relight)
  instead of the single full shader (no gain); half the march steps (+2 fps only).
- **After a skip** (first second): JS ~600 ms busy, of which React re-renders (Queue rows 577 of 1165: every
  queue change gives every row a new `layoutKey` so framer can animate the slide; Player 254 for the
  waveform; Now Playing 100; Catalog 97), forced layouts (framer `PopChild.getSnapshotBeforeUpdate` reading
  `offsetHeight` in the Queue's `popLayout` exit: 88 ms; the cover renderer's `checkVisibility`: 39 ms), GC
  ~45-170 ms. **Any change to a CSS variable on `:root` costs one full style recalc, 58-100 ms**, even an unused
  one; the theme sets 69 of them once per track. The theme colour fade itself (CSS transitions) costs only
  3-10 fps in that first second: keep it.

## 4. Next steps, in order

1. **Fix the context loss** (section 1).
2. **Draw the Now Playing cover straight into its own canvas** (its own WebGL context, no per-frame copy).
   Needs persistent A/B slots in `TrackArtCrossfade` (two canvases that live for the panel's lifetime, the
   track swapping between them) or every track change compiles shaders and makes a context. Keep it inside the
   one renderer (a second "surface" type), not a second renderer.
3. **Lighting cost** (~4.4 ms on the big cover): per-pixel loop over 4 lights; look for maths that can move
   per vertex or per draw without changing the image, and verify with a pixel diff (method below).
4. **Skip jank**: land the theme colours in a different frame from the track/queue commit (one full restyle
   instead of stacking); the Queue's `popLayout` forced layout; Queue rows re-rendering for `layoutKey`.
5. **Catalog draw-ahead**: owner says "much better but not perfect". Tune `AHEAD_*` in `depthArtRenderer` and
   the warm set; measure with `perf/catwhy2.mjs` (classifies every blank tile per frame).
6. Later (owner's call): tilt re-centring (the baseline is set 1 s after page load, so a page loaded flat has
   forward/back stuck at the limit when held).

## 5. How to measure (learned the hard way)

Scripts are in `tests/gpu_bench/perf/` (Node, CDP over USB; `cdp.mjs` is the shared helper). Setup:
`adb forward tcp:9222 localabstract:chrome_devtools_remote` (adb from Google platform-tools). Some scripts
hard-code the old scratchpad's adb path or `client/dist`; fix the path when you run them.

- **Simulate a held phone**: `DeviceOrientation.setDeviceOrientationOverride` at ~30 Hz, **centred on the
  calibrated pose** (beta 0 / gamma 0 when the page loaded flat), ±10° at 0.6-0.8 Hz (`perf/interleave.mjs`,
  `perf/tiltsurvey.mjs`). A wobble around 40° saturates one axis and lies. Lying flat, the phone flatters
  every number.
- **Interleave A/B** (A B A B A B, median per variant, 3-4 s each): drift and the music-driven lighting make
  single runs useless (`perf/interleave.mjs <panel> '[["label", {renderer switches}], ...]' <rounds>`).
- **GPU cost**: no timer queries on Adreno 610 and `gl.finish()` returns early. Use the slope method: force a
  pass every frame and add N extra copies of one stage (`__plairArt.set({ bench: { forceFull, extraFull,
  extraCopy } })`); ms per extra copy = that stage's GPU cost.
- **Visual identity**: fix the tilt (override), freeze lighting (`__plairLight.debug({ off: true })`), force a
  full pass, screenshot the cover region per variant with `Page.captureScreenshot`, diff in Python; compare
  against two shots of the same variant (the noise floor: controls over the cover reflect the moving
  background).
- **Main thread**: long-animation-frame entries (`perf/loaf.mjs`), CPU profile with callers mapped to the
  built bundle (`perf/cpuprof.mjs`, `perf/cpucallers.mjs`).
- **React re-renders in production**: inject a DevTools-style `__REACT_DEVTOOLS_GLOBAL_HOOK__` with
  `Page.addScriptToEvaluateOnNewDocument` (after `Page.enable`) and count fibers with PerformedWork that are
  new since the previous commit, charged to the root of each cascade (`perf/renders2.mjs` + `renderhook.js`).
- **Skips**: the Next button fires on `pointerdown` (`.click()` does nothing). `adb input` is blocked on this
  Xiaomi; use `Input.synthesizeScrollGesture` for touch flings (3000-5000 px/s).
- Renderer switches (`__plairArt.set`): `lit`, `redrawShiftPx`, `skipCopy`, `skipDraw`, `forceSplit`,
  `stepScale`, `stepPx`, `shader: { mediumpShading }` (recompiles: debug only, possibly involved in the
  context loss), `bench`. Diagnostics: `__plairArt.stats()`, `.views()`, `.blank()` (why each on-screen cover
  is blank, clip-aware), `.drawDelays()`; renderer profile: `window.__plairProfile = {}` then read `.tiles`.
  Scene: `__plairScene.set({ skip })`, `__plairScene.eval(code)`. Light: `__plairLight.debug({ off, level })`.

## 6. How to work with the owner (5 Oct)

- Short updates, no excuses, no claims without a number from the phone. Say before any test that changes what
  they see (skips, panel switches, reloads); they watch the phone live.
- Optimise through the pub/sub (narrow selectors), never by gating work on panel visibility: desktop shows
  every panel and a mobile panel must be current when swiped to.
- Look at the maths first; build measurement rigs only to confirm. Never leave a polling loop running on the
  phone (a forgotten 25 ms scene poll halved later readings).
- Never switch shaders or recreate contexts on the phone while the owner uses it.
- C: was full on 5 Oct (Docker's `docker_data.vhdx` 158 GB); Postgres lives on C:. Write traces and large files
  to E:, not the scratchpad on C:.

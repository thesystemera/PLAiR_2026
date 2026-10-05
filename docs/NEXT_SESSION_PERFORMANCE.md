# Next session: 60 fps on the owner's phone (start here)

Paste this as the first message of a new session. Read `CLAUDE.md` first (section 8 covers the cover system).

## The goal (owner's words, 5 Oct)

A steady **60 fps on the current settings (High), while the phone is held and moving and the catalog is
scrolled**, with no loss of visual flair: no resolution cuts, no fps caps, no removing effects, the dynamic theme
fade stays. Optimise the work, never the look.

## 1. SOLVED (5 Oct, late): the page went black (WebGL context loss)

Both blackouts (23:04:35 and 23:12:40) and a third on purpose (23:43:45) were **the `skipCopy` bench switch**,
not the shipped renderer. With the copy skipped nothing reads the cover atlas, so Chrome never submitted its
GPU work; seconds of full passes piled up and went to the GPU in one submission when the copy resumed. The
Adreno driver took that as a hang and reset the GPU. Phone log at the moment: `GLES2DecoderPassthroughImpl:
Context reset detected after MakeCurrent` -> `Restarting GPU process due to unrecoverable error. Context was
lost.` -> `GPU process exited unexpectedly`. GPU process memory was flat (128 MB GL), so not memory.

- Fix: `skipCopy` now calls `gl.flush()` every frame. Same switching test afterwards: 12 switches, no loss
  (before: lost within 2 rounds, 3 runs out of 3). `tests/gpu_bench/perf/ctxloss.mjs` is the test (it logs
  every WebGL context the page creates or loses; the app's own startup check loses one on purpose).
- **Rule for bench switches:** any switch that skips the consumer of GPU work must flush, or the "saving" it
  measures is just deferred work (that's why "no copy" read ~60 fps).
- After a GPU reset Chrome blocks WebGL for the site: the lost contexts are never restored and new ones fail.
  A reload lifts the block a moment later; context creation during the first ~2 s of that load can still fail,
  and then the scene stays in its static fallback and the covers stay off for that page load (seen twice).
  Not fixed; only matters after a reset.
- Still true: **always confirm covers are drawing (`textureMB > 0`) and the scene context is alive before
  trusting a high fps.**

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
  one step per pixel). Copying the drawn cover into its own canvas costs nothing measurable (interleaved with
  the flushed `skipCopy`, 6 rounds: with copy 35-43 fps, without 34-43); the earlier ~3.9 ms and "no copy ->
  60 fps" readings were unsubmitted GPU work (section 1).
- Not worth it (measured): explicit `textureLod` sampling (no change, removed); split passes (cache + relight)
  instead of the single full shader (no gain); half the march steps (+2 fps only).
- **After a skip** (first second): JS ~600 ms busy, of which React re-renders (Queue rows 577 of 1165: every
  queue change gives every row a new `layoutKey` so framer can animate the slide; Player 254 for the
  waveform; Now Playing 100; Catalog 97), forced layouts (framer `PopChild.getSnapshotBeforeUpdate` reading
  `offsetHeight` in the Queue's `popLayout` exit: 88 ms; the cover renderer's `checkVisibility`: 39 ms), GC
  ~45-170 ms. **Any change to a CSS variable on `:root` costs one full style recalc, 58-100 ms**, even an unused
  one; the theme sets 69 of them once per track. The theme colour fade itself (CSS transitions) costs only
  3-10 fps in that first second: keep it.

## 3b. Cost of one cover pass, measured 6 Oct (Now Playing, 989x989, lights held on)

`tests/gpu_bench/perf/stages.mjs`, 3 interleaved rounds agreeing within 0.2 ms. Start of 6 Oct: 13.5 ms per pass:
lighting 4.3, refinement (5 bisection reads) 2.5, march 2.3, depth-bound lookup and setup ~2.8, edge fill 1.1,
fixed cost (one read + write) 0.6. Catalog tiles run the same shader; 4-6 tiles on screen are about as many pixels
as one Now Playing cover.

Shipped 6 Oct, each checked with `identity.mjs` (tilt and lights frozen, every pixel compared):
- The march starts at the depth bound instead of stepping through the empty steps above it: same image (max 2-5
  levels on a handful of pixels), -1.1 ms.
- Secant refinement with one extra read replaces 5 bisection reads: against a 96-step reference, pixels off by >8
  went 0.36% -> 0.53% (Now Playing) and 0.67% -> 0.78% (Catalog), -2 ms. Plain secant (no read) was -2.5 ms but
  doubled the error.
- Rejected: fewer march steps (x0.5: -0.4 ms, 1-5% of pixels off by >8); mediump light uniforms (2x slower).
- Now ~10.5 ms per pass. Left: lighting 4.3, bound/setup ~2.8, march ~2, fill ~1.1.

## 4. Next steps, in order

1. ~~Fix the context loss~~ (done, section 1). ~~Draw Now Playing straight into its own canvas~~ (dropped:
   the copy costs nothing measurable, section 3).
2. **The full parallax pass itself** is the cost under motion (covers flat -> 60 everywhere).
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
  `stepScale`, `stepPx`, `shader: { mediumpShading }` (recompiles: debug only), `bench`. `skipDraw` leaves
  nothing to submit; any new switch that skips the copy must flush (section 1). Diagnostics: `__plairArt.stats()`, `.views()`, `.blank()` (why each on-screen cover
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

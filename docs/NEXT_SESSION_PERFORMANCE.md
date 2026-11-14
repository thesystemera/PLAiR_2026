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

## 3c. Tilt and lighting fixes (6 Oct, late)

- **Tilt** (`UIStateContext`): the phone's pose is a quaternion from alpha/beta/gamma (no gimbal jitter when held
  upright; a left/right turn of an upright phone registers); parallax = rotation from a rolling-average pose
  (`HOLD_AVERAGE_MS` 3 s, advanced every frame, not per event: Chrome only sends events when the angles change),
  full at `FULL_TILT_DEGREES` 20, rotated into screen axes (`screen.orientation.angle`), time-based smoothing
  (50 + 60 ms). Chrome 154 on Android has `DeviceOrientationEvent.requestPermission`, so the old code waited for
  a tap before listening: the listener is now always added at once (the tap request stays for iOS). Check:
  `__plairArt.tilt()`.
- **Scroll tilt**: list covers tilt by their place in their own `[data-scroller]` (below its header padding):
  top row about -0.8 to -1.3, middle about 0, bottom +1.3 (`SCROLL_TILT` 1.3). Current-track covers
  (`TrackArtCrossfade`: Now Playing, player) take phone motion only. Check: `__plairArt.views()` (px, py).
- **Lighting colour**: light colours were divided by luma (a saturated blue light came out ~14x too strong); now
  by the peak channel.
- **Normal sign bug (the white wash, 6 Oct)**: since the packs (d34e0f2, 5 Oct) the server stored the normal's y
  and z (channels 1, 2) and the shader rebuilt x as sqrt(1 - y^2 - z^2), so x lost its sign: every slope was lit as
  facing right (56% face left on a typical cover). With the lights on the right, relief, glints and rim all fired
  everywhere and covers washed out (saturation 0.44 -> 0.32 lit). Packs now hold x (red), depth, y (blue) as
  documented and the shader rebuilds z (always positive). Packs moved to `artwork_thumbs/pack_xy` (and
  `profile_pack_xy.jpg`) so all re-render; client URLs `?v=4`; offline downloads carry `packVersion` and stale
  ones are re-downloaded at start (`cacheManager.refreshStalePacks`). The old `artwork_thumbs/pack` folder is
  unused. The owner's rim (screen blend 0.35) is kept: with correct normals it costs 7.5% saturation under
  strong cyan lights (it should take on the light's colour); a tint variant was tried and dropped.
- **Lighting cost**: lights off saves 5.3 of 9.0 ms (landscape layout). Working out the per-light screen terms
  once per cover saved nothing, so per-area lighting is not worth building. Next: split the 5.3 ms (normal read
  at the hit point, the 4-light loop, the specular power) with `stages.mjs` edits.
- Bound setup with the texture sizes as uniforms: -0.35 ms (4%), not done yet.

## 3d. Phone results, 7 Oct morning (tilt pinned at 0.6/0.4, light level 0.6, `stages.mjs`, 3-4 rounds)

- Now Playing full pass: ~11.6 ms. Lighting is 4.6 ms of it: ~0.94 ms per light (specular ~0.25, rim ~0.2,
  direction/falloff/relief ~0.5) plus the normal read at the hit point (~1.8 ms with its maths).
- Texture sizes as uniforms: kept (-0.35 ms).
- Depth from the R8 pyramid: no gain on the phone (9.24 -> 9.19 ms, noise); removed.
- Runtime-length march loop: no gain (+0.14 ms).
- Lights computed per cover (screen terms constant): -2.0 ms, but that changes the look. Doing the same per vertex
  of a 24x24 grid with the values carried as varyings made the pass 22.4 ms vs 13.1 ms (Adreno pays heavily for
  varyings, and the grid alone cost ~1.4 ms): rejected and reverted.
- Rig fixes: `edits.mjs` applies an edit only where its text exists and aborts if one matches nothing or if the
  renderer falls back to its WebGL1 shader (earlier light variants had silently fallen back and read 1.7x slower;
  the 6 Oct "lights per cover saves nothing" result was the same bug). `__plairArt.set({ tilt: { x, y } })` pins
  the tilt for tests (the rolling-average tilt drifts back to centre while a pose is held).
- Frame under motion: cover pass + ~13 ms of scene and compositing. Next lever: the scene.

## 3e. Frame budget on the phone, 14 Nov (High, music playing, scene on the main thread, Chrome 157)

Frames per second (`survey.mjs`): Radio 60 and Queue 59 at rest and under tilt; Catalog 47 at rest, 30-46 under tilt;
Now Playing 51 at rest, 40-44 under tilt. Covers flat or scene off: 57-60 on both (`ablate.mjs`).

GPU milliseconds, everything forced every frame (`budget.mjs`, `frame.mjs`, tilt pinned, lights held):

- Now Playing: frame 28 ms. Covers 12.7 (coarse 7.6), scene main pass 4.0-5.0, scene capture pass about 5.5
  (`phone.mjs gpu`, runs every frame while music plays), CSS masks 2.1, `backdrop-blur` on the "100% AI" badge and the
  notice chip 1.0, the rest (screen compositing, page work) about 2-3.
- Catalog: frame 35 ms. Covers 18.7 (coarse 10.3), everything else about 16.
- **Everything except the covers is already 15-16 ms, the whole 60 fps budget.** That is why even the cheap relight
  pass at rest drops frames, and why coarse covers alone show no change in fps (18-19 ms still misses 16.7).
- Copying covers from the atlas into their canvases costs nothing (7 copies = 1 copy), again. The tab buttons'
  `brightness` filter costs nothing.
- Chrome draws 4 render passes per frame on every panel (`vizframe.mjs`). On Now Playing the cc frame has 6: the
  panel's edge-fade mask (1076x1812, redrawn whenever the cover changes), the tab bar's mask, the two backdrop blurs,
  the profile tab button, and the screen (`surfaces.mjs` lists the CSS).
- **Keep It Smooth has a dead band.** It lowers a lever only when frames average over 1.5 refresh intervals (25 ms,
  under 40 fps, at Chrome's 60 Hz) and restores it under 1.11. This phone sits at 44-51 fps on Catalog and Now Playing,
  so coarse covers never came on (the stored step was 0); my forced tests pushed it on. Not changed yet.

## 3f. What was done on 14 Nov, and what was rejected

Corrections to 3e: the scene's capture pass costs nothing (skipping it or running it four times gives the same frame;
the 5.5 ms was the readPixels sync itself). A `Keep It Smooth` change was dropped: the owner does not want art
resolution used as a lever. 120 Hz is the benchmark, not 60; a moving synthetic touch lifts Chrome's 60 Hz limit
(`touchfps.mjs`: Radio 66 fps, because the fixed part of a frame was about 11 ms).

Shipped (all checked on the phone):

- Compositing: header backdrops and the tab bar are fills instead of masks, the notice pill, scroll indicator, count
  pills and text composer lost their backdrop blur, Now Playing's origin badge uses a blurred copy of the cover.
  Render passes per frame 4 -> 2 on every panel. Pixel checks: within 1-6 levels of the masked originals on a dark
  pattern (`fillcheck.mjs`).
- Player panels (Devices, stream quality, Rendering) opening and closing (`toggle.mjs`, frames in 900 ms):
  14-26 frames, worst 100-184 ms at the start of the day. Three causes, fixed in turn: the player's height went through
  UIState on every frame (now `lib/playerInset.js`; 31-39 frames); the virtual lists recomputed rows and re-decoded
  covers on every frame of the resize (108 JPEG decodes in 5 s; 38-48 frames); the tab bar and transport bar were
  re-rasterised inside their parents' layers (own layers now; 45-53 frames on Radio, Queue and Now Playing).
  The Catalog stays at 31-36 frames: every visible cover's scroll tilt changes as the viewport shrinks, so all of them
  run the full parallax pass each frame.
- The share video renders in a worker as a background job (section in `CLAUDE.md`).

Rejected, with the number:

- Edge-fade strips copied from the scene instead of the scroller's CSS mask: 41.8 ms against 40.7 ms for the mask under
  the same load. The first copy out of the live WebGL canvas costs about 1.7 ms a frame, a whole-canvas copy 3.5
  (`scenecopy.mjs`). Copies between small off-screen canvases are cheap (`copycost.mjs`), the live scene canvas is not.
- Drawing Now Playing's cover straight into its own canvas: the copy is about 0.7 ms per megapixel, not worth a second
  path.

Still the biggest costs, untouched: the cover pass (12.7 ms on Now Playing, 18.7 on the Catalog), the scene's main
pass (4-5 ms) and the scroller's mask (2.5-2.9 ms).

## 3g. Shader pass, 14 Nov evening (same picture required)

**Cover shader: nothing left to take with the same picture.** Now Playing, 989x989, full resolution, `stages.mjs`:
12.1-13.2 ms a pass (it varies 2-3 ms with the cover, so compare inside one round only; the script prints the song).
Tried, 2 rounds each, noise about 0.4 ms:

- Lighting rewritten with the same maths and fewer vectors (`light algebra`): -0.17. Normal rebuilt without
  `normalize` / `length`: +0.5. Glint computed only where it can be positive: +0.8.
- Depth-bound setup in integers (`floatBitsToInt` for the level, shifts for the cells): -0.3.
- Explicit LOD on the fill's reads: -0.7 once, and no change when it was tried on 6 Oct.
- The edge fill wrapped in a variable-bound loop to force a real branch: +0.7 to +3.8.
- The edge fill is honest work, not a compiler artefact: 21.8% of the big cover's pixels take the 8-read fill path
  (`trailshare.mjs`; 0.7-42% on Catalog tiles). Removing the fill saves 2.5-3.0 ms, its gate alone costs 0.5-0.8, the
  four gradient reads 0-0.2.
- The depth bound is worth about 10 ms a pass (lights off: 8.7-10.5 ms with it, 20.6 without).

**Around the cover pass: the atlas.** Chrome copies the whole atlas canvas every frame a cover is drawn (0.7 ms per
megapixel), and the atlas only ever grew: 3840x1024 after one visit to the Catalog, on every panel. Now Playing's
frame under the same load: 42.6 ms with it, 41.0 ms at 1280x1024 (`atlassize.mjs`). Now the atlas is 1024 wide (wider
only for a wider cover), covers are packed tallest first, and its height follows the frame's covers: it grows at once
and shrinks after 90 draws at more than 1.5x the need. Phone: Catalog and Now Playing 1024x1472 (no resize between
them), Queue 1024x192, Radio 1024x128. Resizing a WebGL canvas blocks the page 4-6 ms per dimension on the phone
(`atlasresize.mjs`; both: about 10 ms; assigning the same size: 0.2 ms), which is why only the height moves.

**Scene main pass: 4.0-4.4 ms -> Radio 3.2, Queue 2.7, Now Playing 2.6** (`scenepass.mjs`, slope with 8 extra passes).

- The panel-interior shader (84% of the phone's screen) carried the radio button's code for every pixel: without it
  the pass is 1.0 ms cheaper. The interior cells under the button's bounding box draw with a third program that keeps
  it (`sceneGlassButtonFragmentShader`), so the picture is the same.
- The interior now reaches the panels' edges (only the four corner squares stay with the full shader), and cells no
  panel or button can reach draw with the backdrop shader: another 0.2-0.7 ms.
- Not done: a constant panel-count loop (-0.2 to -0.4 ms) needs one program per panel count.
- Pixel check (`tests/gpu_bench/scene_split_check.js`: the same uniforms drawn with the split and with the full shader
  on every pixel): on the phone 1,343-1,927 of 660,912 pixels differ by one level (one pixel by two, once); the same
  split drawn twice is identical. Before the change the old split differed from the full shader the same way
  (P6000, phone size: 1,182-1,294 of 418,304 before, 529-905 after).

**Frames per second, today's build against the morning's behaviour, interleaved (`oldnew.mjs`, 3 rounds):** Catalog
under tilt 40.2 -> 44.0, Now Playing under tilt 41.5 -> 44.0, at rest 56.6 -> 57.9. Under touch (`touchfps.mjs`)
Radio 66 -> 70. Frame rates measured an hour apart on the phone are not comparable (the same build read 39 and 34 on
the Catalog); only interleaved runs are.

Rig: `edits.mjs` stages now run at full resolution by default (they forced coarse covers on, and left them on after a
run); `coarse` is its own stage.

Still the biggest costs: the cover pass (12-14 ms on Now Playing: lighting 4.4, edge fill 2.5-3, march and bound
4-5), the scroller's mask (2.5-2.9 ms) and the scene (2.6-3.2 ms).

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
6. ~~Tilt re-centring~~ (done 6 Oct, section 3c).

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

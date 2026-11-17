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

## 3h. Share video on the phone (14-15 Nov)

First full run on the phone (6:14 song, 720x1280 at 30 fps): 9.6 minutes (0.65x realtime), 201 MB (4.3 Mbps for
1 Mbps asked), the app at 34-52 fps meanwhile. Why (`sharebench.mjs`, 20-second clips):

- The loop waited on Chrome's GPU process twice per frame. Render plus a 1-pixel read: 30-38 ms a frame, and 29-32 ms
  with a flat shader, so it is the round trip, not the shader (about 6 ms). `new VideoFrame(canvas)` plus encode of a
  still picture: 42 ms. Together 61-75 ms a frame.
- The encoders are not the limit: fed frames from memory in a worker (`encbench_i420.mjs`) the hardware encoder does
  54 frames/s in High profile and 79 in Main, the software one 25. In variable mode the hardware encoder spends
  about 5 Mbps whatever is asked; in constant mode it holds 1, 2 and 3 Mbps exactly at the same speed.
- Resting the live visuals while frames render: 0.43x -> 0.52x on the same clip.

Built on 15 Nov (`lib/videoFrameReadback.js`): frames are packed as NV12 on the GPU and read back through pixel-pack
buffers with fences, several in flight, and the encoder runs at a constant bitrate with a key frame every 2 s.
Desktop (P6000, headless): 10.4x, as before (the desktop was never limited by this); a decoded frame against the
rendered one: 38.8 dB for the old settings (1 Mbps variable, key frame every second, 1.25 Mbps real), 39.5 dB at
1 Mbps constant, 41.6 at 1.5, 43.3 at 2; colour shift under 1.3 levels in every case.

On the phone (16 Nov) the fenced buffers were no faster than about 0.74x. A CPU profile of the render worker
(`workerprof.mjs`) put 82% of its time inside `getBufferSubData`: it blocks 10-12 ms even after the fence has
passed (25-30 ms in the busy worker). `readbacktest.mjs` tried every way of doing it (buffer usage hints, one buffer
reused, `clientWaitSync` or `getSyncParameter`, waiting extra tasks or frames after the fence): all the same, and a
plain `readPixels` into memory blocks about 6 ms. Chrome on this phone has no read that does not block; a read costs
about 10 ms whatever its size plus about 3.5 ms per frame. So frames share a read: each is packed into its own rows
of one tall target and one `readPixels` brings back as many as fit under `MAX_TEXTURE_SIZE` (4 on the phone at
8192, 8 on desktop). The pixel-pack buffers and fences are gone.

Phone, 20 s clips (`sharebench.mjs`): render plus read 2.82x realtime; with the encoder 1.75x in High profile and
2.0-2.2x in Main (`avc1.4d001f`). A whole 3:17 song with the Share window closed: 99 s (1.99x), 1.62 Mbps, encode
15.7 ms a frame, drawing 0.6. Picture against the rendered frame, Main profile, encoded on the phone: 44.5 / 46.2 /
46.6 dB at 1 / 1.5 / 2 Mbps on a calm frame, 38.4 / 41.8 / 43.1 dB on a busy one; High at 1.5 Mbps is 0.5 dB better
and 20% slower. Shipped: Main, 1.5 Mbps constant (`VIDEO_CODEC`, `VIDEO_BITRATE`).

What it cost and what is open: the desktop renders at 5-6x with the shared read against 10.4x with the fenced
buffers (one read is about 16 ms on the P6000, and the scene is heavier since the 15-16 Nov rework); nobody waits
on that. With the Share window left open the phone renders at about 1.7x, not 2.0x; not looked into yet. The
encoder is now the limit (15.7 of about 17 ms a frame).

ResizeObserver loop errors (74 in the server log on 14 Nov): the player's size observer wrote the panel insets inside
its callback (the 14 Nov afternoon change). Now the height is passed on after framer-motion's render step: 104 errors
-> 0 for four panel toggles in headless Chrome, panels within 6 px of the player for two frames of a 418 px move.

## 3i. Pressing Next (17-18 Nov)

Measure with `perf/skipseries.mjs [skips] [panel] [warm-up]`: single skips vary by 150 ms, a series of ten is
steady to about 20 ms. It prints the frame time lost in the 1.5 s after the press, the worst frame, and the long
frames split into script (by source), end-of-frame style and layout, and the rest. `skipprof.mjs` gives the CPU
profile of one skip as a call tree with snippets of the built file (`SELF=40` for self time with native calls
charged to their callers, `ENTRIES=1` for app functions by inclusive time), `skipstyle.mjs` the style
recalculations and what invalidated them, `renders3.mjs` every React commit with the roots of its cascades
(`TYPES=1`: the components in the biggest commit and which props changed).

Baseline, phone, Now Playing tab: 515 ms lost, worst frame 159 ms, 5.7 frames over 50 ms; about 700 ms of
main-thread work in 1.6 s (profile). What it was made of and what was done:

- **Two restyles of the whole page.** Changing any custom property on `<html>` restyles every element: 70 ms for
  2,576 elements, timed alone with an unused variable. The theme wrote its variables in a layout effect and two
  accent variables in a later passive effect, so a track change did it twice; the profile showed them as 91 ms
  inside framer-motion's `measureScroll` and 88 ms inside the cover renderer's `checkVisibility` (whoever reads
  layout first pays). Now one write. The one that remains is the worst frame of a skip (80-100 ms).
- **The player's waveform** was 420 bar elements (two layers of about 210) rebuilt at every track change. Now two
  canvases drawn from the same bar data (`drawWaveform` in `Player.jsx`; bar edges on whole device pixels, the
  played layer's glow as a canvas shadow). Against a screenshot of the old one the bars differ by about one
  level (`waveshot.mjs`, `wavediff.py`). The page went from 2,362 to about 1,950 elements.
- **The scene's cover analysis** (depth bands, depth grid, detail grid, points of interest) ran on the main thread
  in the decode worker's reply: 63-104 ms in one task. It now runs in the worker (`coverAnalysis` in
  `scene/focus.js`, asked for with `decodePack(blob, { analysis: true })`): 11-14 ms left in the reply.
- **React.** 23 commits and 1,087 component renders per skip, in two big passes (the instant step at the press
  and the server's state about 100 ms later) that each re-rendered the Player, Queue, Now Playing and the
  Catalog. The Catalog re-rendered because it selected the queue and the current track for its cards: each card
  now subscribes to its own two flags. The Player re-rendered about 75 components four times: its panels'
  `memo` never held (five handlers of `useDevicePicker` and two of `useBitratePicker` were new on every
  render), `GenerationQueuePanel` had no `memo`, the icon buttons got new icon elements; fixed, and the
  transport and side-control blocks are memoised. Now about 780 renders a skip, the Player 27-32 per render.
- **IndexedDB.** Every consumer read the downloaded track's whole record itself; lookups within three seconds now
  share one read (`cacheManager.getCachedTrack`).

After: 410-425 ms lost, worst frame 121-131 ms, 3.7-4.5 frames over 50 ms. Long frames per skip: script about
160-180 ms (React 70-125), end-of-frame style and layout 95-115, the rest (paint, commit) 80-90.

Tried and not kept:

- `content-visibility: auto` on the panels: skips only the farthest panel (Chrome counts the neighbours as near
  the screen); no change. `content-visibility: hidden` on the four off-screen panels: 482 -> 408 ms and the
  worst frame 131 -> 107, but the cover renderer's `checkVisibility` / `getBoundingClientRect` on covers inside
  hidden panels then force their style (its frame went 38 -> 76 ms). Doing it properly means every measurer
  knows which panels are hidden: the owner's call.
- Switching the colour fades off (injected CSS): 450 -> 428 ms, median 426 -> 360. They stay.

Left, biggest first: the one page restyle (it scales with the number of elements: the Catalog's 16 cards are 638
of them, the User panel 311); the colour fade (about 800 elements restyled per frame for 0.7 s); the two React
passes (the Queue's rows with their `layout` / `popLayout` wrappers are half of each); the cover's texture uploads
(`texImage2D`, 10-40 ms); the Queue's follow-the-song timeout, whose 5-72 ms is the pending style work it forces.

Second pass, 18 Nov (the React passes; `renders3.mjs` with `TYPES=all`, headless Chrome in the phone layout): one
press re-rendered 596 components, the Player root seven times (about 29 each: the track, play state, crossfade
flag, features, cache badge and the buffered bar's once-a-second state all lived in the root), Now Playing four
times (about 31 each: features, lyrics and analytics arrived through the root) and the Queue's header and every
row's `AnimatePresence` wrappers although two rows changed. Now each part subscribes for itself (`QueueRow`,
`QueueModes`, `PlayButton`, `BufferedFill`, `CurrentAudioFeatures`, `CurrentLyrics`, `TrackAnalytics`; the Player
selects the track's fields, not the object): 363 renders a press, the Player 28 + 5 + two small play-button
updates, Now Playing two passes. The pass where the server's queue arrives still re-renders every Queue row: the
rows glide to their new places (`layout`), and framer needs each one rendered to measure it. Measured on desktop
only (`skipcpu.mjs`: CPU profile of the 1.5 s after the press, old and new builds pressed in turn, 18 skips
each): script 181 -> 162 ms a skip, busy time 542 -> 528. Frame-time series on desktop with the CPU slowed six
times (`CPU=6 skipseries.mjs`) swing by 150 ms between rounds and show nothing either way.

**On the phone the gain is too small to measure** (builds switched in turn by copying each into `client/dist` and
reloading): `skipseries.mjs 10`, frame time lost a skip, old 404 ms (and 643 right after the app was opened), new
422 and 449; `skipcpu.mjs 7`, script in the 1.5 s after the press, old 446 and 372 ms, new 406 and 458. Two
ten-skip series of the same build differ by about 50 ms, so nothing smaller shows, and the 233 renders removed
were cheap ones: fewer renders is not fewer milliseconds here. Kept for the pattern, and because the Player no
longer re-renders once a second while a song buffers. Do not promise frame time from a render count again.

## 3j. The Catalog: scrolling and picking a track (17 Nov)

The owner felt a regression after the 17 Nov UI commits (f5f0546 -> d150f05: shared blocks, one theme builder,
700 ms colour fades, arrival fades on covers). Measured on the phone with the builds switched in turn (copy each
build into `client/dist`, reload): **no difference between the build before them (1d40f3a), the 15 Nov build and
the current one** on any measure below. The Catalog has been this slow all along; it had never been measured.

- **Read the phone's own state before trusting absolute numbers.** During this session Android reported thermal
  status 3 (`adb shell dumpsys thermalservice`), the fast cores were capped at 2.2 of 2.8 GHz
  (`/sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq` against `cpuinfo_max_freq`) and another app held
  1.4 cores (`adb shell top -n 1 -b -m 12`): Next on Now Playing lost 715-800 ms where the day before it lost
  404-449. Comparisons taken in turn stay valid; absolute figures do not. A page that is not in front returns no
  frames at all (`document.visibilityState`), and the phone may be a remote (`Playing on another device`).
- **Scrolling** (`catalogpick.mjs SCROLL=1800`, `scrollab.mjs`: a synthetic finger at 2500 px/s, about 12 rows a
  second): 15-16 frames per second as is; the same with the arrival fades off (they cost nothing measurable);
  25 with the covers flat or not redrawn at all (lit covers redraw every frame while they move: about 9 fps);
  27 with the scene off as well. So the floor is the page's own work to bring a row on screen: about 13 slow
  frames a second, one per row, about 40 ms of main thread for a row of two cards (React mount, style, layout,
  paint, layerize). Trace (`scrolltrace.mjs`): the page's main thread 680 ms/s busy, Chrome's GPU main thread
  590-720 ms/s.
- **Forced layouts are not extra work here.** 84-99 of about 110 layouts in a five-second scroll are forced by
  script (each new cover measuring itself, 260-300 ms; the scroller; the cover renderer's `measure` and
  `applyScrolls`). Taking the cover's read away (covers of one list sharing a measured size) moved the same
  layout to the next reader: 126 layouts and 259 ms against 119 and 242, no change in frame rate. Reverted.
- **The cover atlas** resizes three times in such a scroll and once per skip (about 25 ms each). Growing with
  25% spare and shrinking after 6 s halves the resizes (6 -> 3 over a scroll and three skips) but leaves a
  larger atlas for Chrome to copy every frame. Not a provable gain; reverted.
- **Picking a track** (`catalogpick.mjs`, 5-6 picks a series): about 590-670 ms of frame time lost in the 1.5 s
  after the tap with the phone cool, 715-850 with it limited; first answer on the card 20 ms, playing mark and
  player 140-210 ms (the server's answer). Lit covers off or the scene off: no change. The 700 ms colour fade
  costs about 140 ms of it (775 -> 635 with the fade off); switching transitions off only in the off-screen
  panels saves about 25 (most of the fading elements are on screen). A whole-page restyle costs the same on old
  and new builds (45-66 ms for about 2,000 elements, timed directly; a trace with invalidation tracking reports
  112 ms, which is the tracer's own overhead).
- **The scratch sound** was the one real fault found, and is fixed (CLAUDE.md section 6).

What would move the Catalog, all larger than a fix and for the owner to choose: recycle the rows of the virtual
list instead of unmounting and mounting cards (the mount is the floor; an update is far cheaper); hold the covers
still while the list moves fast (16 -> 25 fps, a change of look); a shorter colour fade (up to 140 ms a pick, a
change of look).

## 3k. Card reuse in the virtual list, and covers that are there when they scroll in (17 Nov)

Built after 3j, at the owner's request ("any and all optimisations for the virtual scroller"; "I'd scroll and
scroll back and all of it was gone"). What it is: CLAUDE.md section 14 (`recycle`, and the arrival rule).

- **Card reuse, phone, builds in turn** (`catalogpick.mjs SCROLL=1800`, `scrolltrace.mjs`, `scrollab.mjs`):
  layouts in a five-second scroll 128 -> 65 (250 -> 167 ms), elements restyled 4,087 -> 2,338, the page's main
  thread 663 -> 607 ms/s, React's tasks inside the long frames 128 -> 28 ms/s. Frame rate: 13.7 and 14.8 with
  reuse against 16.0 and 15.8 without (inside the noise, and not a gain); with covers not redrawn and the scene
  off, 23.6 and 24.0 against 25.6 and 22.4. So mounting cards was not the floor that 3j took it for.
- **What is left with reuse and the graphics frozen** (`scrolltrace.mjs`): the page 718 ms/s (script 193,
  waiting on the GPU process 78, layerize 77, style 55, paint 37, layout 33, prepaint 33), Chrome's compositor
  thread 471, its GPU main thread 536, the compositor's GPU thread 492, viz 276. The scroll is bound by the
  compositor and GPU side: 20-35 cover canvases, each its own layer, plus the raster of the cards.
- **Texture uploads** (timed inside the app on the phone): colour 0.5 ms, maps 1.2-1.3 ms (uploaded as RGB from
  an RGBA bitmap, which Chrome repacks on the CPU), about 1.9 ms a cover, 56 covers in the test scroll. A CPU
  profile reports 92 ms/s for the same calls: the profiler inflates native calls, time them directly.
- **Getting it to reuse at all** took three tries, worth knowing: keys by `index % pool` never reuse (the node
  that held the slot has gone by the time the index wraps); slots handed over in the same render do, but only
  if a row leaves and a row arrives in the same step (the old window grew at the bottom and shrank at the top
  at different scroll positions, so every row was 2 mounts and 2 unmounts: 48 cards built in a 3,600 px scroll
  down and back, 10 with a fixed row count); and the children must keep their DOM order (CSS `order`), or
  React re-inserts every card when one moves to the front.
- **Covers on screen while scrolling** (`coverwatch.mjs`). Phone, build before the change: on the way down 22%
  not shown and 30% still fading in; on the way back 9% and 40%. Desktop: 17-44% still fading in before, 0%
  after, on both legs, two rounds each. The phone numbers for the changed build are in "Back on the phone"
  below; the reuse numbers in the first bullet were taken before the row-height bug was found and are replaced
  there too.
- **Checked on desktop**: after scrolling, flinging and reversing three times, and after every sort mode, each
  card's canvas matched its own song's pack (flat covers, 12x12 comparison, the title on screen as the truth:
  the fibers behind DOM nodes can be the stale alternate and gave false alarms), no console errors.

### Back on the phone (17 Nov, later)

- **Reuse had broken the list, live for about an hour.** The Catalog measured its row height as the distance
  from the grid's first child to the child `itemsPerRow` places after it. With reuse the DOM order is slot
  order, so after a scroll those two were three rows apart: row height 656 instead of 219, the list three times
  too tall, and after down, back, down no card on screen until the list was scrolled back to the top. Found
  because `coverwatch.mjs` counted 2.5-4 covers a frame on screen where the old build had 6; it now reports
  "no card there". Fix: the row height is a card's height plus the grid's row gap (no DOM order). Rule in
  CLAUDE.md section 14. The desktop check above missed it because the measure only runs when the first child
  is resized or a page of tracks arrives, and the slots happened to be in order then.
- **Reuse, measured again with the list working** (builds in turn, `catalogpick.mjs 0 0` with `SCROLL=1800`,
  `scrolltrace.mjs`): frame rate over eight legs each 17.7 with reuse, 18.7 without (15.4-23.9 and 13.9-23.7:
  no difference). The page's main thread 549-609 ms/s with reuse, 563-629 without. Layouts 116 -> 61
  (233 -> 140 ms) and elements restyled 2,736 -> 1,956 with reuse, so the saving in layout is real and is spent
  elsewhere. GPU main thread 607-643 ms/s either way.
- **Covers that are there** (`coverwatch.mjs 6000 2500`, a seven-screen scroll at fling speed straight after a
  reload, then twice more; share of the covers on screen that are fully there, builds in turn):

  | build | first way down | first way back | again, down | again, back |
  |---|---|---|---|---|
  | before 3ee5d0a | 27-28% | 17-22% | 14-19% | 19-21% |
  | fix and cache budget, reuse off | 38-43% | 26-59% | 59-89% | 83-96% |
  | fix and cache budget, reuse on (shipped) | 33-44% | 69-100% | 75-94% | 87-98% |

  On the short test (1,800 px, three rounds after a warm-up) the build before had 47-69% there on both legs,
  the shipped one 100%. Reuse was taken out when the frame rate showed nothing and put back when the first way
  back measured 69-100% (four runs) against 26-59% (three runs). Why reuse helps there is not pinned down: a
  reused card keeps its sized canvas, a new card is mounted and its canvas resized (3 a frame) before the
  renderer can draw it ahead.
- **The cover cache** (one build, the limit switched live, same scroll): 8 idle covers (as it was) 41-49% there
  on the way back, 19-21% not shown, 30-38% fading in; 32 covers 97-100% there; 64 covers 98%. The GPU
  process's GL memory (`dumpsys meminfo`) 119-133 MB with 8, 180 MB with 24 idle covers held; the phone has
  5.7 GB and had 1.8 GB available. Shipped as a budget in bytes, `IDLE_TEXTURE_BYTES` 64 MB (about 27 covers at
  512, 12 at 768), most recently left the screen first, and at most 8 idle covers that are still loading (so a
  fling does not upload covers nobody will see). `__plairArt.set({ idleMB })` changes it live.
- **The fade stays (owner, the same evening).** The "fully there" numbers above were taken with covers drawn
  off screen appearing at once (3ee5d0a). The owner saw that live and rejected it ("you killed the fade effect,
  it looks yuck"): covers fading in while the list scrolls is the look, and what bothered him on the way back
  was blank cards (the cache), not fades. That rule is removed: every cover fades in from the moment it is
  drawn. So judge the way back by "cover not shown" (blank), not by "fully there". He also asked why a fast
  scroll showed nothing, then the placeholder, then the cover: the placeholder faded in after a 90 ms delay
  over 500 ms. It is now there from the card's first frame.
- **Not changed: the first way down** (27-44% there in every build). Those covers have never been loaded:
  fetch, decode in the worker, upload (2 a frame at about 17 frames a second), draw.
- **Swapping builds on the live site**: emptying `client/dist` from a script was refused by the safety check
  (rightly: a relative glob). Copy the other build over it instead (stale hashed files are harmless, the
  manifest and `index.html` are replaced) and finish with `npm run build`.

Open, in order of likely worth: the first way down (uploads per frame are counted per frame, so a slow frame
rate also slows the covers; a budget in time would not, but uploads load the GPU process, which is the busy
one, so it needs measuring). The maps could be uploaded as RGBA (no repack, 0.7 ms a cover). The real limit on
the frame rate is one compositor layer per cover.

## 3l. What the screen shows in a fling, and what cover lighting costs (17 Nov, evening)

- **The fling is smooth on screen; it is the page that is slow.** Trace of a Catalog fling on the phone (viz
  and cc events over 6.9 s, pauses between the gestures included): the display compositor drew and swapped
  86 frames a second, while the page's own frames (`requestAnimationFrame`, `BeginMainFrame`) ran at 15 a
  second. **Every "fps" for a scroll in sections 3j and 3k, and in the notes before them, is the page's frame
  rate, not the screen's.** The list glides at the compositor's rate; what updates 15 times a second is what
  the page draws: each cover's tilt and lighting (the tilt follows the cover's place in the viewport,
  `scrollTiltFor`, so every visible cover is re-marched as it moves) and the rows that arrive. Count
  `Display::DrawAndSwap` in a trace to know what the owner sees; count rAF to know how often covers update.
- **Why 15.** With lit covers the page waits on the GPU: about 8 covers a frame at about 2.5 ms each, on the
  same GPU that composites 86 frames a second. Flat covers: 25 page frames a second, and then the page's own
  work per frame (about 40 ms) is the limit. So in a fling the cover pass decides how often covers update.
- **Cover pass by stage, Now Playing, full resolution** (`stages.mjs`, 2 rounds, same song inside a round):
  as is 11.3-11.7 ms; lights off 7.3-7.8 (lighting 3.7-4.3); one light of four 8.3-8.4 (about 1 ms a light);
  no edge fill 8.8. Per-pixel pieces of the light sum taken out one at a time save 0.4-0.7 each (the half
  vector, the falloff, both); the normalisation alone nothing. Light vectors as per-cover values varying
  linearly across the cover: no saving (11.8). Per-cover values, the same for the whole cover and different per
  light: 9.1 and 10.2 (-1.3 to -2.5). The same with one constant for all four lights 8.1-8.6, which only shows
  the compiler folding four lights into one. So the cost is per light and per pixel, and rewriting the maths
  does not move it (also 3g). What would: fewer lights (about 1 ms each, a change of look) or one direction
  per cover per light (-1.3 to -2.5 ms, no gradient of light across a cover).
- **Wrong lead, for the record:** the edge fill "costing every pixel like an unused path" was already tested
  on 14 Nov (3g: 21.8% of a big cover's pixels take it, it is honest work). I told the owner it had never been
  tried; it had.

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

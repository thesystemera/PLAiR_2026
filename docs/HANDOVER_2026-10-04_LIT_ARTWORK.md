# Handover: lit 3D artwork and the frame-rate drop (4 Oct 2026)

## Update 5 Oct 2026, night: where it stands (start here)

The owner's phone (Redmi Note 13 4G, Adreno 610, Android 15, 120 Hz set, no battery saver) still shows no
noticeable gain. The owner is getting a faster phone. Owner's rules from this session: optimise for every GPU (not
for one phone), profile on High, never cap or gate frame rate, no telemetry sent to the server, don't make the owner
the test instrument.

Live now:
- No frame-rate caps anywhere. The weak-GPU tiers capped the scene and artwork at 30 fps; that cap was the reason the
  phone sat at exactly 60 (the counter showed "screen 120 Hz"). Fixing it took the phone from 59 to ~76 fps.
- Visual Quality is one setting, High / Mid / Low / Auto (`QualityContext`); only Auto adapts, against the screen's
  measured refresh (`lib/screenRefresh.js`, which runs only for Auto or the FPS counter).
- FPS counter line 2: `main = scene js + art js + other`, plus GPU timers where the browser exposes them (the
  phone's Chrome does not).
- Light probe computed on the CPU (`lib/backgroundProbe.js`) instead of a GPU readback. Under GPU load the readback
  blocked the main thread 457-511 ms of every second (single stalls 95-218 ms); now 0. The CPU copy of the colour
  maths must follow any change to the background shader's grading/transform.
- Background effects run every frame (no 60 Hz gate); three.js skips matrix/cull/sort work for its static quads.

Measured per frame on High, 4x CPU throttle, phone viewport (`bash tests/gpu_bench/ablate.sh`, `PACED=1`):
main thread ~8 ms on Radio, ~8.5 ms on Playing. Of that: Chrome's own lifecycle (style, prepaint, layer update,
commit, observers, animation servicing) ~5 ms; background scene ~1.9 ms (three.js + react-three-fiber ~1.3 ms of it);
lit artwork + light ~1-2 ms (one player thumbnail on Radio already costs ~0.9 ms: atlas snapshot, 2D drawImage per
tile); CSS transitions ~0.7 ms (mostly the player progress bar); React 0.05 ms. Switching lit art off also cut
Chrome's GPU-process time by about a third.

Tried and rejected: progress bar as a Web Animation (Chrome then restyles it every frame: worse); per-device
frame telemetry to the server (owner said no).

Leads, biggest first (none started):
1. Draw all lit artwork inside the scene's WebGL context (no tile canvases, no per-frame snapshot/copies, fewer
   composited layers). Biggest structural win; must keep DOM order, clipping, rounded corners and fades.
2. Replace three.js/r3f for the background with plain WebGL (same shaders): ~1.3 ms at 4x CPU.
3. Chrome lifecycle per frame: fewer composited layers and observers (ResizeObserver/IntersectionObserver run every
   frame), fewer separate rAF loops.
4. GPU fill of the main pass on weak GPUs: profile it on the P6000 with `--viewport 3840x2160x1.5 --uncapped 1`
   (GPU-bound, stable timers) and `--uniforms` ablations (glass blur, refraction, chromatic, lyrics).

Bench options added (`tests/gpu_bench/gpubench.mjs`): `--viewport WxHxDPR` (desktop, GPU-bound when large),
`--quality high|medium|low|auto`, `--fullscreen 1`, `--fps 1`, `--css '<rules>'`, `--uniforms 'name=value,...'`,
`--block '<url pattern>'`, `--invalidations N`, `--savetrace <prefix>`, `--ttop N`; profiles print JS time per bundle.
An unminified build (`npx vite build --minify false --outDir <dir>`, served by `serve.mjs`) gives readable profiles.

## Update 5 Oct 2026: what was measured and changed (all live)

Measured with `tests/gpu_bench/` (headless Chrome pinned to the P6000, phone emulation, GPU timer queries, traces,
pixel checks against the original shader). Every change below keeps the picture identical (within 1/255) unless noted.

| Change | Measured |
|---|---|
| Light-only frames re-light a cached parallax result (colour RGBA8 + hit position RG32F) instead of re-marching; cache built after 2 tilt-still draws | Now Playing GPU per lit frame 0.070 -> 0.026 ms; tile ~33 -> ~20 us/draw (P6000) |
| Lighting maths: pow(x,24) as multiplies, squared distances, closed-form half vector | identical but for 5-27 px off by 1/255 per 512x512 |
| March skips steps that cannot hit: max-depth pyramid built on the GPU per image, 4 texel reads bound the ray | depth reads 18 -> 6-12 per pixel; full pass 7-30% faster on a software GPU, neutral on the P6000 |
| Light probe: persistent pixel buffer, reads only while lit art is on screen and the song's light is up, never while panels resize; still 15/s | main-thread stalls 0 while the light is off; during a panel toggle 54-96 -> 3-15 ms |
| Glass follows panels every frame (ResizeObserver on every glass panel, measured in the callback) instead of timers at 100/350 ms | glass no longer jumps on resize |
| Now Playing canvas resizes once the size settles, not every frame of a panel animation | 123-143 ms per toggle -> one 20-45 ms resize |
| Tiles: own visibility check instead of IntersectionObserver; scroll re-measures only tiles in the scrolled element; Now Playing reads its rect on scroll/resize/250 ms; viewport size cached from resize events | IO 0.5-0.6 ms/frame gone; getBoundingClientRect 0.64-0.84 -> 0.14 ms/frame (4x CPU throttle) |
| Light tracking: no per-frame sort or allocations | 0.25-0.34 -> ~0.05 ms/frame (4x throttle), identical output |

Not possible: a non-blocking WebGL readback in Chrome (every `getBufferSubData`, `createImageBitmap(canvas)` and
per-tile `transferToImageBitmap` waits on the GPU process; tested). Removing the probe stall entirely would mean
drawing the artwork in the scene's own WebGL context.

Still open, biggest first:
1. Panel open/close re-lays out and repaints the panel contents every frame of the `width` animation (~20-25 ms per
   frame on desktop). Fix needs the owner's OK: contents at their final width, revealed by the sliding edge.
2. Per-canvas cost in Chrome for each tile that changes in a frame (~2-3 ms/frame on Catalog at 4x throttle) and the
   atlas snapshot + copy (~0.3 ms/frame even for one tile).
3. Measure on the owner's phone over USB: how often tilt really changes per frame (decides the cache's value there).

## The owner's report

1. **Frame rate dropped hard (to about half or a quarter) since the Now Playing artwork started being lit by
   light sources captured from the background canvas and projected onto baked normal maps.** Before the lighting,
   Now Playing (depth parallax only) ran fast. This is the main problem. The goal is the full effect at full
   quality, smoother (target: 120 fps on the owner's phone). Do not dial effects down or cap redraw rates; find real
   efficiencies that keep the look identical.
2. **The frosted-glass look of the panels may be gone.** Not verified either way (see "Frosted glass").
3. **Up/down tilt on Now Playing stopped working on the owner's phone** (left/right still works). Not explained.
   The parallax code path was moved into a shared shader file unchanged; nobody has found the cause.

The work was done fast and in many small steps; treat every claim below as needing measurement.

## What was built (all live)

| Piece | Files |
|---|---|
| One depth shader (parallax occlusion mapping, unchanged from the original Now Playing, plus lighting) | `client/src/lib/depthArtShader.js` |
| Now Playing artwork (own WebGL context; now also loads a normal map and lights it) | `client/src/components/ParallaxArtwork.jsx` |
| Light capture: the background is drawn small for the glass panels; a pass shrinks that to a 16x16 grid, read back to the CPU (`readRenderTargetPixelsAsync`) every 66 ms; the 4 brightest spots that stand out become tracked lights | `client/src/components/AudioReactiveCanvas.jsx` (probe pass, `buildLightCurve`), `client/src/lib/lightProbe.js` |
| Light level that eases in and out with the song (loudness over 4 s ranked within the song; loudest third brings the light in), kick flashes only while lit | `AudioReactiveCanvas.jsx`, `lightProbe.js` |
| Every other cover / avatar (catalog, shoutouts, queue, player, Now Playing history, User panel, review/reply avatars, nav avatar, modal background cover): one shared WebGL context renders all visible tiles into an atlas and `drawImage`s each into its own 2D canvas | `client/src/lib/depthArtRenderer.js`, `client/src/components/DepthArt.jsx` (`DepthArt`, `TrackArt`, `ProfileArt`, `DepthArtBridge`) |
| Normal maps: deepMirror's method (Sobel on depth + photo detail layer signed by depth curvature), baked from Depth Anything's full-precision depth at 1024 px; 2265 covers in `D:/catalog/artwork_normals_v2` (rebake: `server/utils/bake_normal_maps.py`); served at `/api/artwork/{id}/normal`, `/normal/thumb/{size}`, `/api/user/{id}/profile-picture/normal` | `server/services/normal_map_service.py` |
| Depth thumbnails and profile-picture depth maps | `server/services/artwork_thumbnail_service.py`, `server/services/profile_picture_service.py`, routes in `routers/media.py`, `routers/user.py` |
| Setting **3D Lit Artwork** (on/off, per device kind) | `settingsState.litArtwork` |

Debug hooks in the page: `window.__plairLight.read()` (light level, lights, kick), `.kick(1)`,
`.debug({ off, level, kick })`; `window.__plairProfile = {}` makes the tile renderer and Now Playing record their
draw time (with `gl.finish()`) into that object.

## What changed per frame (the likely causes of the drop)

1. **Now Playing redraws every frame while the light is on.** Before, it redrew only when the tilt changed. The
   detected lights move whenever the background animates, so with the light up it is a full parallax + lighting
   draw at device resolution on every frame.
2. **The GPU readback for the light capture.** `getBufferSubData` makes the main thread wait for the GPU to finish
   everything queued (trace: `CommandBufferHelper::Finish` ~70 ms per frame in the software-GPU simulation).
   Reading less often (250 ms, or never) did not change the simulated frame rate, but this was never measured on
   real hardware, where a CPU/GPU sync point is a classic cause of lost frames.
3. **Lit tiles redraw every frame while the light is on or the phone tilts** (catalog, queue, player, ...), and each
   one is copied from the atlas into its own 2D canvas, which the compositor then has to draw.
4. **The lighting maths** per pixel: one normal-map fetch and a loop over 4 lights (diffuse, specular `pow`, rim).
5. The plain `<img>` stays under every lit canvas (composited twice). A change hiding it was **reverted at the
   owner's request** (`031f826`) because it was suspected of the frosted-glass loss; it was not proven either way.

Already in place (no change to the look): nothing redraws when nothing visible changes (light off and no tilt),
light changes are ignored while the light level is 0, tiles hidden by opacity/visibility or off screen are skipped.

## Benchmark

`node tests/perf_depth_art.mjs [--url https://plair.live] [--cpu 4] [--seconds 8] [--isolate 1] [--shots dir]`

Headless Chrome, phone screen 412x915 at 2.625x, Android user agent, CPU slowed 4x, simulated tilt
(`deviceorientation`), light held fully on (`__plairLight.debug({ level: 1 })`), quality tier forced to the top,
welcome popup dismissed. Reports fps, p50/p95 frame time, tile/Now Playing draw ms per frame, with 3D Lit Artwork
on and off, on the Catalog and Now Playing tabs. `--isolate 1` adds still / tilt-only / light-only runs.

It runs Chrome with **SwiftShader (software GPU)**, so the numbers exaggerate compositing and copying costs and are
**noisy on this shared machine** (the effect-off baseline swung between 9 and 24 fps between runs). Use it for
before/after comparisons run back to back, several times each.

**Hardware GPU:** remove `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader` from the launch
flags. Chrome will pick a graphics card; per the GPU rules it must not run on the Quadro RTX 6000 (index 1, the
owner's other projects), so check `chrome://gpu` in that profile first. **Best of all: the owner's phone over USB**
(`chrome://inspect` → Performance panel, GPU track), which is the real target.

Last simulated results (lit, tilting, light on, before the revert), for reference only:

| Tab | Effect on | Effect off |
|---|---|---|
| Catalog | 5–7 fps | 9–13 fps |
| Now Playing | about 10 fps | 9–13 fps |

## What to do next

1. **Measure before vs after the lighting work on the same hardware.** Build the client at `a9628f6` (the last commit
   before this work) and at `HEAD`, serve both against the same backend, and run the benchmark on each (hardware GPU
   or the phone). This answers how much the lighting costs and confirms or clears the frosted-glass report
   (compare screenshots of the panels on the same track).
2. **Remove the CPU/GPU sync of the light capture** without changing the look, for example by finding the lights on
   the GPU in the same context that uses them (pass a tiny probe texture between contexts as an `ImageBitmap`), or by
   reading back with no `Finish`. Measure on hardware.
3. **Now Playing:** the shader runs the full parallax march every frame when only the light moves. Cache the
   parallax result (colour + hit UV) in a texture while the tilt is unchanged and run only the lighting on top of
   it; the output is identical.
4. **Tiles:** same idea; and consider fewer composited canvases.
5. **Frosted glass:** check against `a9628f6` before touching anything.
6. **Up/down tilt on Now Playing:** reproduce with the phone over USB (`gyroscopeRef` values in DevTools).

To roll the visuals back to before this work, revert the depth-art / light commits from `28e2554` to `a1fb1f3`
(see `git log a9628f6..HEAD`); keep the settings commits (`7d9763d`, `eca9ad9`, `21e93a2`), which are separate and
tested.

## Settings (done and tested, separate from the above)

Every Settings-panel option is saved per device kind (`windows-pc`, `android-phone`, `iphone`, ...) on the device
and in `users.device_settings`; old account columns were migrated and dropped; 494 stale device rows pruned.
`docs/SETTINGS.md`, test `tests/device_settings_test.py` (6/6 pass against the live server).

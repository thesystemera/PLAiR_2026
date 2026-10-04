# Handover: lit 3D artwork and the frame-rate drop (4 Oct 2026)

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

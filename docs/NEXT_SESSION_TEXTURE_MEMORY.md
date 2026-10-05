# Next session: texture memory on the owner's phone (start here)

Paste this as the first message of a new session.

---

You're picking up PLAiR performance work on the owner's phone. Read `CLAUDE.md` and the top of
`docs/HANDOVER_2026-10-04_LIT_ARTWORK.md` first, then `tests/gpu_bench/README.md` (phone section).

## The owner's hypothesis (test this first, nothing else)

GPU texture memory is the main problem, mostly from images that are bigger than they are shown. The old
adaptive quality scaler helped because it shrank images, but it went so low the thumbnails were unusable.
The goal is resolution matched to display size: catalog tiles are small and can use smaller images than
Now Playing, with no visible loss. Do not cut or simplify any stylised effect (lit 3D artwork, frosted glass,
background shader, masks). Only isolated, measured efficiency changes, one at a time: measure before, change
one thing, measure after, keep it only if it helps, then the next.

## What we know (5 Oct 2026, Redmi Note 13 4G, Adreno 610, ~5.7 GB RAM, Chrome 154)

- Chrome's GPU process held 371 MB of GL memory (`adb shell dumpsys meminfo --package com.android.chrome`,
  "GL mtrack" of the privileged process) while scrolling the catalog; the page process was swapping; Chrome's
  GPU process crashed once (`SystemInfo.getInfo` -> `processCrashCount`), after which Chrome blocked WebGL for
  the site and everything fell back to flat images.
- Catalog scroll: 10-18 fps portrait, 8-11 landscape; it gets worse the longer you scroll (memory). 3D Lit
  Artwork off: 34-37 fps portrait. Tile render resolution (`parallaxDpr`) made no difference, so it is not
  pixel shading.
- Per catalog tile today: artwork thumb (`lib/mediaCache.js` `pickThumbSize`: 512 on this phone), depth thumb
  and normal thumb at the same size, uploaded by `lib/depthArtRenderer.js` (colour RGBA, depth LUMINANCE,
  normal RGB, plus a max-depth pyramid); its own 2D canvas at device pixels (~520x520); a parallax cache
  (RGBA8 + RG32F, ~3 MB) once the tile is still. Up to 48 idle tile texture sets are kept
  (`MAX_IDLE_TEXTURES`). The shared atlas canvas grows to fit every tile drawn in a frame (about
  4096x3840 = ~63 MB for ~45 tiles) and `transferToImageBitmap` keeps a second buffer. The plain `<img>` under
  every lit canvas is decoded too (Chrome's image decode cache).
- Media caches keep up to 300-400 blobs in memory per type (`maxMemoryItems`).

## Suggested order (each one isolated and measured)

1. Measure the baseline: GPU memory (GL mtrack) after opening Catalog and after 20 s of scrolling, plus scroll
   fps, plus a profile. Check the phone is cool first (see below).
2. Catalog thumbnails sized to the tile: today `pickThumbSize` picks one size for every artwork thumb, depth
   and normal map in the app. Make the size depend on where the image is shown (catalog/queue/player tiles vs
   Now Playing). Compare 512 vs 384/256 for the catalog by screenshot at 1:1 on the phone and by memory; the
   server already serves `thumb/256|512|768` (`server/services/artwork_thumbnail_service.py`, check whether
   other sizes are cheap to add). Depth and normal maps can probably go smaller than the colour image.
3. The atlas: cap its size and draw in several smaller groups per frame instead of growing to 4096x3840.
4. Idle texture sets and media-cache memory limits: lower them if memory falls without visible cost.
5. Only then look at Chrome's raster of the catalog cards (~480 ms/s of GPU-process time while scrolling).

## Tools (all in `tests/gpu_bench/`, phone over USB)

- `adb forward tcp:9222 localabstract:chrome_devtools_remote`; adb is in Google platform-tools (download into
  your scratchpad; the owner's PC has no system adb). Set `ADB=<path to adb.exe>`.
- `bash catalog_bench.sh <label>`: reload, open Catalog (works in portrait and landscape; `open_catalog.sh`
  makes sure it is the track Catalog, not Shoutouts), 3 continuous downward scroll runs, then a JS profile.
- `node phone.mjs profile <s>`, `trace <s>` (`SCROLL=1` scrolls during it), `eval`, `css`, `reload`, `tap`.
- For readable profile names, put an unminified build live for the session (`npm run build -- --minify false`)
  and the normal build back at the end.
- fps alone is too noisy for small changes: judge by profile cost and GPU memory.

## Cautions

- Heat: `adb shell dumpsys thermalservice`. Thermal Status 2+ or skin above ~42 C means throttling, and every
  number is wrong (on 5 Oct, status 3 after an hour of tests while charging; everything read ~9 fps). Ask the
  owner to unplug and let it cool; keep test runs short.
- Chrome on Android holds page frames to 60 Hz unless the screen is touched. The FPS counter's "screen" figure
  is the throttled page loop.
- The background scene can run in a Web Worker (`plair_scene_thread=worker`), but stays on the main thread by
  default: on this phone the worker scene took the GPU from the UI.
- nginx serves `client/dist` directly, so every `npm run build` is live. Commit and push each verified win.
- Keep replies short: what you measured, what changed, the number before and after.

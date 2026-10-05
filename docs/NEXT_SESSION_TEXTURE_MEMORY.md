# Next session: finish unifying the covers (start here)

Paste this as the first message of a new session. Read `CLAUDE.md` first.

## The owner's design (fixed, do not drift from it)

- Every cover and avatar in the app is ONE packed image per cover: colour on the left, and on the right the normal's x
  in red, depth in green, the normal's y in blue. One download, one decode, drawn by ONE shared renderer
  (`client/src/lib/depthArtRenderer.js`) through ONE component (`client/src/components/DepthArt.jsx`: `TrackArt`,
  `ProfileArt`).
- "3D Lit Artwork" off is only a switch inside that same renderer: it draws the pack's colour half flat. There is no
  plain-JPEG path, no `<img>` fallback, no "flat image then it turns 3D".
- Covers never visibly load: they are loaded ahead of the scroll in both directions and appear the moment they are
  drawn (no fade), like a plain image used to.
- Picture quality stays exactly as it was (catalog at its tile size, Now Playing at full resolution). The finish line
  does not move: only changes that keep the picture and measure better.

## Done and live (5 Oct 2026, commits 5377d71 .. f0d2467)

- Idle texture sets 48 -> 8; textures sized to each tile (queue 192 px, player 128, catalog 512). Phone GPU memory with
  Catalog open 152 -> 95 MB, after 20 s of scrolling 351 -> 174 MB.
- Packs: `GET /api/artwork/{id}/pack/{size}` and `/api/user/{id}/profile-picture/pack`
  (`server/services/artwork_thumbnail_service.py` `render_pack` / `ensure_pack`). JPEG q90 4:4:4, ~114 KB vs 128 KB for
  the old three files; depth error ~1/255. Shader reads depth from green and rebuilds the normal (`PACKED_NORMALS`).
- Catalog, queue, player thumbnail, profiles, modals, User lists: all `TrackArt` / `ProfileArt`, canvas only.
  `useArtworkThumb`, the `artwork_thumb` cache, the thumbnail prefetcher and the separate depth/normal thumb routes are
  deleted. `lib/artworkPrefetcher.js` now loads packs ahead of the virtual scroller and warms the nearest 32 on the GPU
  (`depthArtRenderer.warm`). Offline downloads save the pack (`packBlob`).
- Covers scrolling in draw within one frame (measured 1-28 ms from on screen to drawn): on-screen covers upload before
  warmed ones, and canvases are sized while still just off screen. Only the first open after a page load waits ~1 s.
- Debug: `__plairArt.stats()`, `__plairArt.views()` (state of each on-screen cover), `__plairArt.drawDelays()`, `__plairArt.set({ maxIdle })`.

## Still to do, in this order

1. **One shared cover-with-crossfade component** for anything that changes track (Player thumbnail
   `components/Player.jsx` `TrackArtwork`, Now Playing big cover `components/NowPlaying.jsx`). Two layers; the new
   track's `TrackArt` draws in the back layer, and only when it reports drawn (`onLoad`) do the layers crossfade
   (duration-theme), with a time limit so it never hangs. The old A/B code in both files swaps after 50 ms
   regardless of readiness; delete it.
2. **Now Playing on the shared renderer.** It uses the same shader (`createDepthArtPrograms`); only its strength
   differs (intensity 0.05 vs the renderer's 0.1). Add a per-cover intensity to `depthArtRenderer.attach` (the
   `intensity` uniform is set once at setup today, and `pixelsPerUnit` / zoom use the constant). Full resolution: add
   pack size 1024 on the server (`THUMBNAIL_SIZES`) and a large pack cache on the client.
3. **Delete the old Now Playing path**: `components/ParallaxArtwork.jsx`, the enriched-artwork cache and its UIState
   plumbing (`useEnrichedArtwork`, `preloadEnrichedArtwork`, `getEnrichedArtworkUrl`, the enriched preload in the
   current/next effect), `normal_full` cache, `cacheManager._downloadEnrichedArtwork` (offline keeps `packBlob`), and
   the `/api/artwork/{id}/enriched` and `/api/artwork/{id}/normal` routes once nothing calls them. `useArtwork` (the
   plain full-size artwork) stays only for theme colours and the media session; no cover draws from it.
4. Run `scripts/check-quality.ps1`, update the file map in `CLAUDE.md`, commit, deploy.

## Pending owner decision

The backend has not been restarted since the pack fallback change (tracks without depth/normal maps yet get a pack
with flat maps, and the old depth/normal routes are removed). Another session has uncommitted music-chain edits in
`server/services/audio_headroom.py`, `audio_master_service.py`, `suno_service_orchestrator.py` (also
`server/config/settings.py`, `server/utils/reprocess_catalog_audio.py`); a restart puts them live. Ask the owner before
restarting.

## Measured facts (so they are not re-measured)

- Catalog scroll on the owner's phone: lit on ~12-16 fps, lit off ~33-35. Loading and uploading textures costs nothing
  measurable; the cost is redrawing every visible tile each frame while scrolling (tilt follows scroll position), split
  across the WebGL render and the copy into each tile's canvas. Filling an extra 300 MB of GPU memory did not change fps
  or cause stalls. The 3 am build and today's build scroll the same (17-20 vs 18-23 fps in fresh tabs).
- Old auto quality on this phone was the "low" tier (Adreno 610 on the weak list): its resolutions gave ~+2 fps at most.

## Working rules learned the hard way this session

- Answer the owner's design points as design decisions; don't reply with fps numbers to an aesthetics point.
- Don't switch renderer parts off live on the owner's phone while they use it (it left blank covers); say before any
  test that changes what they see. Check `document.visibilityState` first.
- Stage files by name; never `git add -A` on folders other sessions are editing.
- Phone tools: `tests/gpu_bench/` (`phone.mjs`, `mem_bench.sh`, `cover_state.js`, `lit_toggle.js`); adb from Google
  platform-tools in the scratchpad; the DevTools forward drops when Chrome restarts (`adb forward tcp:9222
  localabstract:chrome_devtools_remote`).

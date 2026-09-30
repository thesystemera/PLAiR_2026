# Offline Mode Architecture - Complete Documentation

**Last Updated:** 28 September 2026
**Status:** Complete. When PLAiR can't be reached (no internet, or the server is down), the device keeps playing from its downloads, and it hands back to the server without a cut when the server returns. Tested in headless Chrome against a mock backend. Not yet tested on a real phone.

---

## Current state (28 September 2026)

### What the listener sees
- **Server drops mid-song:** the song keeps playing. About 2-4 s later a pill appears at the top, `Offline · playing your downloads (N)` (guests too). If the song is downloaded, playback quietly moves to the downloaded copy (120 ms crossfade of identical audio at the same position). If it isn't, it plays to the end of what was already buffered, and when the buffer runs dry (1.5 s of stall) it skips to a download.
- **While offline:** next, previous, seek, pause, tapping a track in the catalog, adding to the queue and Seed Radio all work on the downloads. The catalog shows only downloads, or `No downloads yet` when there are none. Voice search, the DJ, Radio Mode and shoutouts show a short friendly message instead of an error, and Radio Mode talk breaks pause. The "Failed to load tracks" toast and the red "Disconnected from server" toast no longer fire during an outage (the pill is the status).
- **Server comes back:** the socket reconnects immediately (it no longer waits out its backoff, which could be 30 s). The device hands its current song and position to the server; the audio is not reloaded or re-seeked. The pill disappears, and likes made offline are sent to the server.
  - If **another device on the account is playing** when the server returns, this device finishes its current song and then steps aside (becomes a remote), so the two devices don't fight.
  - If this device was only a **remote** before the outage and nobody pressed play on it, it simply follows the server again.
- **Reloading while offline** (even with nginx down) works: the service worker serves the whole app from its cache, and the downloads play.
- **Signed-in users stay signed in** through outages and offline reloads (only a real 401/403 or WebSocket close 4401 signs out).

### How it works (files)
- `NetworkContext.jsx`: server-down detection with hysteresis (2 failed `/api/health` probes 1.5 s apart; back after 2 successes 3 s apart; more after flapping; browser `offline` confirmed for 2 s). It publishes `audioState.offlineMode` (the SSOT) and emits `api.reportServerLost()` / `api.reportServerRecovered()`. A 502/503/504, a network error, or a WebSocket that closes abnormally triggers an immediate probe (`api.reportServerTrouble`).
- `PlaybackContext.jsx`, **local mode** (`localRef`):
  - `enterLocalMode()` runs when `offlineMode` turns on. It resets pending command state, ends any armed or on-air talk break (resuming music from a hold), marks this device locally active (`audio.setActiveDevice(true)` + `reportEngineStatus({ isActiveDevice: true })`), and starts `offlineBackend.startLocalSession()`.
    - It keeps the current track if the engine is already playing it or it is downloaded. Otherwise it starts a download: playing if this device was playing, paused if it was a remote.
    - If the current track is downloaded but still streaming, `engine.handleOfflineTransition()` swaps to the downloaded copy.
  - `applyLocalState()` applies a local queue/state without touching the engine's current track. `getTrackSource()` returns downloads only in local mode.
  - Auto-advance calls `offlineBackend.advanceTo()`. `next`/`previous`/`seek`/`togglePlay`/`playTrack`/`addToQueue`/`removeFromQueue`/`seedRadio` act locally.
  - A stream that stalls for 1.5 s (`STARVED_SKIP_MS`) while offline skips to the next download. A download that fails to load is skipped (max 5 in a row, then pause).
  - Server snapshots that arrive in local mode are stashed, not applied. Heartbeats and claim-on-open are suspended.
  - `handBack()` runs when `offlineMode` turns off and a snapshot from the reconnected socket is available:
    - **not used locally** → apply the server state;
    - **another online device is active and we're playing** → `yielding`: drop the preloaded next track, finish the song, then `stepAside()` (apply the server state, so this device becomes a remote);
    - **otherwise** → `claim` (only if we aren't already the server's active device) + `play {track_id}` + `seek {position_ms}` (+ `pause` if paused). `handoverRef` holds back snapshots until the first of those commands is acknowledged, so a stale "other device is active" snapshot can't silence this device mid-hand-back. Any transport (next/prev/play/pause/seek) during `yielding` takes over instead.
- `WebSocketContext.jsx`: the old fake-offline simulation (it called `offlineBackend.pause/seek`, which don't exist) is gone. `send()` returns `'offline'` while `offlineMode` is on. Queued playback and talk-break messages are dropped when the server is lost (so stale commands aren't replayed), and the socket reconnects immediately on recovery.
- `audioEngine.js`:
  - `handleOfflineTransition()` now keeps the current slot's metadata. It used to drop `duration_ms`, which stopped the next crossfade.
  - It only swaps when the current source is an incomplete stream, and it does so seamlessly.
  - New helpers: `isCurrentSourceComplete()`, `isNextSourceComplete()`, `dropNext()`.
  - A stream chunk that fails is retried every 2 s (up to 3 minutes) while the buffer keeps playing, so a short server blip no longer kills the song.
- `offlineAPI.js`:
  - friendly offline answers for voice search, the DJ, Radio Mode (`getRadioMode`/`updateRadioMode`) and shoutouts (`getShoutout`, stats, replies, reply upload, delete);
  - calmer offline DJ lines.
- UI:
  - `OfflineNotice` in `components/AppBridges.jsx` (a sticky notice through `showNotice`, visible to guests; count = playable downloads from `cacheManager.getStorageInfo().offlineTrackCount`);
  - `Catalog.jsx` empty states and toast grace;
  - `Shoutouts.jsx` offline empty state;
  - `MediaSearch.jsx` voice search message;
  - `Radio.jsx` DJ message;
  - `RadioModeSettings.jsx` "talk breaks are paused" note;
  - `PreferencesContext.jsx` Radio Mode error text;
  - all pop-ups share the one notice stack (`NoticeStack`), so nothing overlaps the offline notice;
  - `App.jsx` 4 s grace before the disconnect toast.
- `StorageContext.jsx` refreshes on `cacheManager.onChange`, so the pill count updates as downloads land.
- **Service worker** (`public/sw.js`, caches `plair-static-v5` / `plair-dynamic-v4`):
  - A Vite plugin in `vite.config.js` writes `asset-manifest.json` at build time (every `/assets/*` file, icons, interface sounds). The SW precaches it on install, and again when the page posts `PRECACHE` after load (`main.jsx`); that pass also prunes old `/assets/` entries, so updates keep working.
  - Lookups use `ignoreVary` (the page's module-script requests otherwise miss precached entries).
  - Navigations are network-first with a 4 s fallback to the cached shell, and a 5xx serves the cached shell.
  - `/api/*` (including `/api/stream/`) is no longer intercepted: no duplicate audio cache. The old `plair-audio-v2` cache is deleted on activate.
  - Cached media answers `Range` requests with proper 206 responses (Safari needs this).
- **Background downloads** (`backgroundDownloader.js`):
  - They no longer require Chrome's Network Information API (`networkQuality === 'excellent'`), so Safari, iOS and Firefox download liked tracks too.
  - They skip when offline, IndexedDB is unavailable, Data Saver is on, or Chrome reports `saveData`, cellular, or 2g/3g.
  - `cacheManager.canMakeRoomFor()` pauses downloads (30 min) when the cache is full of liked tracks, instead of evicting one liked track to download another.
  - Liking a track, or the server coming back, wakes the downloader at once (it used to sleep up to 60 s).

### Storage and downloads (decided by the owner, 28 Sep)
- **Same experience on iPhone as on Android:** the download space is sized from the browser's own quota on every platform: `navigator.storage.estimate()`, half the quota, at most 2 GB, at least 200 MB where the quota allows (`cacheManager._sizeFromQuota`). Without an estimate it falls back to 500 MB on iOS and 2 GB elsewhere. The old fixed 50 MB iOS cap is gone.
- Once there are downloads, the app asks the browser to keep them (`navigator.storage.persist()`, `cacheManager.requestPersistence`). It skips this on Firefox, which would show a prompt. iOS may still clear a site's storage after about 7 days without use unless it was added to the Home Screen; that is a platform rule.
- **Wi-Fi vs mobile data:** Safari and Firefox don't expose the connection type, and the owner accepts that as a platform limitation. Background downloads measure their own speed and pause for 15 minutes after a download slower than 1 Mbps. They still skip Data Saver, `saveData`, cellular (Chrome) and 2g/3g.

### How to test
- `npx eslint src --quiet` and a scratch build: `npx vite build --outDir <scratch>`. Never build into `client/dist` for tests.
- The end-to-end harness used on 28 Sep (it was in the session scratchpad, so copy it if you want to keep it) had four parts:
  - a FastAPI mock backend with health, catalog, auth, preferences, `/api/stream/{id}/webm` with HEAD + Range, and `/ws/playback` with `state_epoch`/`version`/`acks`/`claim`;
  - `vite preview` of a scratch build with `/api` + `/ws` proxied to the mock;
  - a raw-CDP driver;
  - headless Chrome with `--headless=new --disable-gpu --disable-software-rasterizer --mute-audio --autoplay-policy=no-user-gesture-required`.
- All **51 checks** passed. They covered:
  - server down mid-song (pill in ~3 s, no gap);
  - a stream running dry, then a skip to a download;
  - local next/previous;
  - a downloads-only catalog;
  - a smooth hand-back (play + seek, no jump);
  - an offline reload with web and server both down;
  - a flapping server (no gaps, only 2 mode changes);
  - signed-in, with a liked track auto-downloaded, a seamless swap to it, offline likes synced and still signed in;
  - two devices (one takes over, the other finishes its song and steps aside);
  - no IndexedDB (iOS private mode): "no downloads yet", a clean pause, no exceptions.
- **On a phone:** see the airplane-mode checklist in `docs/MOBILE_LAUNCH_READINESS.md`.


---

## Walk-throughs

### Server or internet lost mid-session

```
1. Streaming track 1 (not downloaded); the server stops answering
2. API errors / the WebSocket closing trigger an immediate /api/health probe
3. 2 failed probes -> connectionMode 'degraded' (or 'offline') -> audioState.offlineMode = true
4. PlaybackContext.enterLocalMode(): keep track 1 playing, build a local queue from the downloads
5. The notice shows "Offline · playing your downloads (N)"
6. Track 1's buffer runs dry -> 1.5 s stall -> skip to the next download
7. Auto-advance / next / previous / seek run locally
```

### Server returns

```
1. Health probes succeed twice -> offlineMode = false -> api.reportServerRecovered()
2. WebSocket reconnects immediately; offline likes are synced (api.syncOfflineWrites)
3. First playback_state on the new socket -> handBack()
   - nobody else playing: claim (if needed) + play {track_id} + seek {position} -> no reload, no jump
   - another device playing: finish this song, then follow the server as a remote
4. The notice disappears; streaming resumes for the following tracks
```

### Reload while offline

```
1. The service worker serves the cached app shell (precached from asset-manifest.json)
2. Health probes fail -> offlineMode -> local session starts paused on a download
3. The catalog shows the downloads; pressing play starts them
```

## What stays online-only

The DJ, voice search, Radio Mode talk breaks, shoutouts, song generation, device management and profile changes need the server. Offline search is plain text matching on the downloads' titles, styles, tags and lyrics; the semantic search lives on the server. After a server restart the queue and station are not restored (see `docs/KNOWN_ISSUES.md`).

## Forcing offline for a test

The most realistic test is to stop the (mock) backend: the app should switch to the downloads in ~2-4 s. Chrome DevTools → Network → "Offline" also works.

The design notes from before the September 2026 rebuild (routing on `navigator.onLine`, a fixed 2 GB cache, the raw online/offline handlers) were removed on 1 Oct 2026; they are in git history.

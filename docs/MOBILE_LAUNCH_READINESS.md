# Mobile launch readiness (iOS + Android), 28 Sep 2026

This document covers:
- a code audit by a separate agent (static reading, no device);
- the fixes made the same day;
- what is still open, and how to test on a real iPhone.

Rules and architecture notes are in CLAUDE.md ("iOS Safari Compatibility", section 4, "Client Error Reporting & Logs").

## Status at a glance

| Area | Before | Now |
|---|---|---|
| **iPhone streaming** | The engine required `MediaSource` (iPhones don't have it), so every non-downloaded song failed on iPhone. | Capability check (`lib/mediaSupport.js`). iPhone plays the server's MP3 stream (`/api/stream/{id}`) as a normal progressive `<audio>`, and offline downloads are saved as MP3. |
| **"Tap and nothing happens"** (iOS allows each audio element to start only from a tap) | Audio started after a network round trip, so iOS blocked it. The next tap on Play then *paused*. The DJ voice was silently dropped. | Every audio element is unlocked on the first real tap, and the current song starts inside that tap. A "Tap to start audio" button appears if the browser still blocks playback, and Play never toggles to pause while blocked. Blocked DJ lines wait for the next tap. |
| **Opening the app steals playback** | Opening PLAiR on the phone took over from the laptop even before any tap, so the laptop went quiet and the phone stayed silent. | Takes over only after the first real tap (within 2 min of opening). |
| **Lock screen / calls / headphones** | Play and Pause both toggled; after a call the UI still said "Playing". | Separate play/pause/seek handlers, lock-screen position bar, artwork URL. Outside pauses update the UI and the server. iOS "playback" audio session is set where supported (Safari 17+). |
| **WebSocket after the phone sleeps** | No heartbeat, so a dead ("zombie") socket could swallow taps for minutes. | Ping/pong every 20 s and on resume; a silent socket is replaced within about 5 s. Reconnects use jittered backoff. Stale queued commands expire after 8 s. |
| **Login token in nginx access log** | The JWT was in the WebSocket URL; the audit counted about 13,900 logged lines. | The token goes in the WebSocket handshake (subprotocol), not the URL. Old servers still work through a fallback. **The owner still needs to** turn off `/ws` access logging and rotate the JWT secret (see nginx below). |
| **Logging** | Browser console only; `radio.log` rotated at 1 MB × 5, so errors disappeared within hours. | Phones report errors plus the last 30 log lines to `/api/client-log` → `data/logs/client.jsonl` (redacted, rate-limited). `radio.log` is 20 MB × 10, categories switchable in `.env`, and WebSocket connects/disconnects are logged. |
| **Crash after a deploy** | A phone left open across a deploy got a black screen when opening a lazy-loaded modal. | Reloads once on a stale chunk; a "Reload PLAiR" screen for any other crash. |
| **Motion pop-up on first tap** | iOS asked for "Motion & Orientation" when you pressed Play. | Only asked from Settings → **Tilt Effects**. |
| **Server audio streaming** | gzip was applied to audio range responses (wasted CPU; gzip on a 206 range response can break Safari). The range parser mishandled "last N bytes" requests and never sent 416. | gzip skips `/api/stream`, `/api/artwork`, beds and stings. Correct suffix ranges and 416 responses. |
| **Install / PWA** | The maskable icon was identical to the regular one; the apple-touch-icon was the wrong size; the manifest screenshot sizes were wrong (so browsers ignored them); no manifest `id`. | Padded maskable icons (192/512), a 180 px apple-touch-icon, `apple-mobile-web-app-capable`, manifest `id` and correct screenshot entry. The refreshed artwork is in `brand/2026-refresh/`. |
| Smaller items | | iPhones start one visual-quality tier lower (they don't report memory); the mic retries without a stale saved device; health checks slow to once a minute in the background; the modal blur cache is capped. |

## Still open
1. **DJ voice on iPhone (needs a device test).** The DJ stream is WebM/Opus via MediaSource / ManagedMediaSource.
   - If an iPhone can't play it, the listener now sees "The DJ's voice can't play in this browser yet" instead of silence.
   - The real fix, if needed, is a second output from the server's live encoder: fragmented MP4/AAC for clients that ask for it. That's a medium-to-large job, so do it only after the device test.
2. **Screen-locked playback on iPhone.** It's set up correctly (audio session "playback", media session), but only a real device can confirm that music continues through a lock and a track change.
   - If it doesn't, the fallback is an iOS-only mode that skips Web Audio (hard cuts instead of crossfades).
3. **Crossfades on iPhone.** They need two audio elements playing at once. This works in WebKit today, but confirm it on a device.
4. **nginx (owner, admin shell).** In the `plair.live` HTTPS server block of `C:\nginx\conf\nginx.conf`, change the `/ws` location and add gzip, then run `nginx -s reload` from an admin shell:
   ```nginx
   gzip on;
   gzip_min_length 1024;
   gzip_types application/javascript text/css application/json application/manifest+json image/svg+xml;

   location /ws {
       proxy_pass http://plair_backend;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_read_timeout 75s;
       access_log off;
   }
   ```
   - gzip cuts the first load from about 1.7 MB to about 0.5 MB of JavaScript.
   - The old access log still contains tokens. Rotating `JWT_SECRET_KEY` (already on the owner's to-do list) invalidates them. The old log can then be archived or deleted by the owner.
5. **Geolocation prompt** (audit P1-9).
   - Signed-in users are asked for precise location on load with no explanation, and reverse geocoding runs in the browser with a public LocationIQ key.
   - Suggested: ask after an explicit "local news & weather?" opt-in, use low accuracy, and geocode on the server (`area_geocode` already exists).
   - Not changed yet: it's a product decision because it touches City Pulse.
6. **After launch (P2):**
   - an install guide for iOS "Add to Home Screen" (home-screen apps keep storage; Safari tabs lose it after 7 days of no use);
   - `user-scalable=no` (accessibility);
   - an "update ready" prompt for the service worker;
   - three.js tree-shaking in `offlineVideoRenderer.js` (it imports `* as THREE`);
   - iOS mic uploads are named `.webm` but contain MP4 (the server's ffmpeg detects the real format, but verify).

## How this was tested (no iPhone available)
The test harness ran headless Chrome (`--disable-gpu --mute-audio`) against a mock backend. **68/68 checks** passed:
- 51 offline-mode checks (see OFFLINE_MODE.md);
- 17 mobile checks:
  - **iPhone-like browser** (MediaSource removed, WebM reported unplayable, strict autoplay): one tap starts playback, MP3 progressive streaming, crossfade to the next song with no extra tap, offline pill.
  - **Strict autoplay with the server already "playing":** the "Tap to start audio" prompt appears, one tap starts audio (no toggle to pause), and the event is reported to the server log.
  - **Claim-on-open:** a second device doesn't take over until tapped.
  - **WebSocket:** handshake login (no token in the URL); a frozen socket is replaced within seconds while music keeps playing; legacy fallback with an old server.
  - **Error reporting:** uncaught errors reach `/api/client-log` with the token redacted.
  - **Outside pause:** shows as paused, and the server is told.

Headless Chrome is not iOS. It proves the logic, not WebKit's exact rules.

## Testing on a real iPhone without owning one
The code must be deployed first (PLAiR Start), because the phone tests the live site.
- **Cheapest: borrow any iPhone for 15 minutes** and run the checklist below plus the airplane-mode checklist at the end of this document.
- **Cloud real-device services** (BrowserStack Live, LambdaTest, Sauce Labs) rent real iPhones in a browser tab. All have free trials with a limited number of minutes; check their current plans.
  - You create the account and sign in yourself.
  - Claude can then drive the remote iPhone through Claude in Chrome / computer use and read Safari's console.
  - It can't hear the audio, so audio checks rely on the UI, the console and `data/logs/client.jsonl`.
- A Mac with Xcode's iOS Simulator also works (free), but needs a Mac.

**Device checklist (15 minutes):**
1. **Capability check.** In the remote Safari console run:
   - `!!window.MediaSource`
   - `window.ManagedMediaSource?.isTypeSupported('audio/webm; codecs="opus"')`
   - `new Audio().canPlayType('audio/webm; codecs="opus"')`
2. **First play.** Count the taps needed to start music (expect 1). Let a song crossfade into the next one, then ask the DJ something.
3. **Interruptions.** Lock the screen through a track change, flip the silent switch, take a call, then use the lock-screen Play button.
4. **Zombie socket.** Unlock after 5 minutes and press Next (it should respond at once).
5. **Claim-on-open.** With the laptop playing, open the phone app: the laptop keeps playing until you tap the phone.
6. **Error reports.** Check `data/logs/client.jsonl` for anything reported from the phone.

**Airplane-mode checklist (offline mode on a phone):**

Before you start: open plair.live once while online, play 3-4 songs to the end (or like a few while signed in and wait a minute) so there are downloads. Then reload once so the new service worker is active.
1. Play a song, then switch on **airplane mode**. Within about 5 s you should see **"Offline · playing your downloads (N)"** at the top. The song should keep playing with no gap.
2. Let it reach the end, or press **next**. The next song should come from your downloads. **Previous**, **pause/play** and **seek** should all work.
3. Open the catalog. It should list only your downloads, with no "Failed to load tracks" message.
4. Try voice search, talking to the DJ and shoutouts. Each should show a short friendly "needs a connection" message, not an error.
5. Like a song while offline.
6. **Close the app completely and reopen it while still in airplane mode.** It should open (not a browser error page), show the notice, and play a download when you press play. If you were signed in, you should still be signed in.
7. Switch airplane mode **off** while a song plays. Within about 10 s the notice should go away and the **same song should carry on without a jump or restart**. Then check that the like from step 5 shows on another device or after a reload.
8. Two devices: play on the laptop, go offline on the phone and play there, then reconnect. One device keeps playing. The other finishes its song and then shows "Playing on another device".
9. Server down (the owner can stop the backend window instead of using airplane mode): same as steps 1-3 and 7, but the phone still has internet.
10. iPhone only: repeat steps 1-2 in a private tab. It should say "Offline · no downloads yet" and pause cleanly when the buffer runs out.

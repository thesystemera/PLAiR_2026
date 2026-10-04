# Settings

Every option in the Settings panel belongs to a **device kind**, not to the account and not to a device ID.

## Why device kind

Device IDs live in local storage and change whenever a browser's site data is cleared, a home-screen app is installed next to the browser, or a new browser is used (4 Oct 2026: 538 device IDs for one account, 498 unused for 30+ days). Settings keyed by device ID would be lost and would pile up. A device kind (`windows-pc`, `android-phone`, `iphone`, `ipad`, `mac`, `linux-pc`, `chromebook`, `android-tablet`) is the same every time on the same kind of device, so a setting turned off on the Windows PC stays off there and stays on on the phone, through log out/in and cleared site data. Each account has at most a handful of kinds; nothing to prune.

## One registry

`client/src/lib/settingsSchema.json` lists every setting: its name and default (its type is the default's type), plus the allowed `options` for choices. The client and the server both read this file; nothing else defines a default, a type or a storage key.

| Setting | Default |
|---|---|
| `ttsMuted`, `notificationsMuted` (Sounds) | off |
| `audioQuality` | auto |
| `fpsEnabled` | off |
| `videoClipsEnabled` | off |
| `visualQuality` (High / Medium / Low / Auto; only Auto adapts, `QualityContext`) | high |
| `litArtwork` (3D Lit Artwork) | on |
| `dataSaverMode` | off |
| `costTickerEnabled` | off |
| `autoClaimOnOpen` | on |
| `backgroundDownloads` | on |
| `radioInput` (voice / text) | voice |

## Where values live

- **On the device:** one local storage entry (`plair_settings`), read at startup so settings show instantly. Guests only have this copy.
- **On the server:** `users.device_settings` = `{kind: {setting: value}}`.
- **The kind** is worked out by the client (`deviceKind()` in `lib/session.js`, touch-aware so iPadOS is not taken for a Mac) and sent as `X-Device-Kind` on every API request and on the WebSocket.

## Flow

1. Startup: `settingsState` (UIState) loads the local copy.
2. Sign-in: `GET /api/settings` returns this kind's saved values; they win. If the kind has none yet, the local copy is uploaded.
3. A change: `publishSettings()` updates the state, saves the local copy and sends the change (`PUT /api/settings`, debounced; retried after a failed save). The server broadcasts it to the account's other open devices, which apply it only if they are the same kind.
4. Log out resets nothing.

## Server-side readers

The server reads settings only through `services/device_settings_service.py`:
- DJ voice mute (`ttsMuted`) for the session is the setting of the device kind that is playing (Radio Mode talk breaks and the voice queue).
- The video clips route reads `videoClipsEnabled` for the requesting kind.
- Stream bitrate: the client sends the bitrate it wants; `auto` is 256k for accounts, 192k for guests.

## Not settings

Station and Radio Mode preferences, likes and bans, location and profile are about the listener and stay on the account. Microphone, speaker and the learned quality tier are about the hardware and stay in local storage only.

## Device rows

`user_devices` (the device picker) keeps one row per device ID; rows unused for `DEVICE_ROW_RETENTION_DAYS` (60) are pruned at startup.

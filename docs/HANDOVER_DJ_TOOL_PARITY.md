# Handover: DJ tools should match what the app can do

Written 30 Sep 2026. Status: built and tested on a private backend (30 Sep, late). See "What was built".

## The goal

Anything a listener can do by tapping in the app, they should be able to ask the DJs to do by voice or text.
Today the two have drifted: the front end has grown features that the DJs' tools never got. The job is to go
through the code base in full, list every front-end capability, find its DJ-tool equivalent (or the lack of one),
and close the gaps.

The example that started this (30 Sep): the app's search box has two modes (typed keyword search with
`title: ...` style prefixes, and the AI search on Enter). The DJs' `search_and_play` only had the single-field
mode with the AI part switched off. The fix was not a new search: it was wiring the tool to the function the
front end already calls, and adding one variable (`within`: catalog / favourites / super_likes).

## What was built (30 Sep)

Owner's decisions: no settings power tool (audio quality, sound modes and visual settings stay in the app), no
deleting posts by voice, no song generation or conversation clearing by voice.

Fixed:
- `rate_track` goes through `preferences_service` (the app's route), so the rating reaches every open device.
- `play_playlist` refuses favorites / discovery for guests, as `/api/queue/seed` does.
- `seed_radio` takes `target` (current / previous / next).
- `play_shoutouts` uses the Shoutouts panel's AI search when the hosts give it a topic.

Added:
- `playback_control`: `restart`, `seek` (`position_s`), `remove` (an upcoming track by `title`, else the next one).
- `rate_track` target `shoutout` (+ optional `shoutout_id`): like, superstar, clear or ban another listener's post.
- `pulse_search` `mine` (the listener's own shoutouts, replies and reviews) and `about_track` (reviews of one
  track); `pulse_detail` on a shoutout lists its replies (`more_replies`).
- `move_playback`: move the music to another online device by name; no `device` lists the online devices.
- `radio_settings`: read or patch Radio Mode, its break types, stings, reviews and music source. Signed-in listeners
  go through `preferences_service.apply_radio_settings` (now also what `PUT /api/radio-mode` calls); guests get a
  `radio_mode_updated` patch that the client merges and stores (`PreferencesContext`).

Tested live on port 8011 (test account and a guest): restart, seek, remove next, like a track, radio settings
change and read, device list, move to a device that isn't online, reviews of the current track, own posts, seed
from the previous track, play shoutouts then like the one that aired, guest refusals. Not tested: a real move
between two devices, and the guest Radio Mode patch in a real browser.

## Rules for closing a gap

1. **Look first.** For every gap, find how the front end does it (component -> `client/src/lib/api.js` method ->
   router -> service). The DJ tool calls that same service. Never write a second implementation.
2. **Extend, don't add.** Prefer one more variable on an existing tool over a new tool. A new tool only when
   nothing existing fits. All tools live in one table, `server/services_radio/dj_tools.py` `TOOL_REGISTRY`
   (name, cost, requires, summary, description, parameters); the Producer's catalog and the DJ's declarations
   are built from it.
3. **Same permissions as the app.** If the route needs a signed-in user, so does the tool
   (`authorize_tool_call`). Guests get a plain refusal the hosts pass on in their own words.
4. **The listener's own things only.** Deleting or editing applies to the listener's own content, exactly as
   the route enforces it.
5. **No keyword gates.** The DJs decide when to call a tool from what the listener said; limits are plain
   (signed in, per-turn caps), never word lists.
6. **Report honestly.** Every tool result says what actually happened so the hosts can't claim something that
   didn't.
7. **Test on a private backend** (`CLAUDE.md`: port 8011, embeddings copy, `tests/dj_pulse_test.py`), read the
   turn in `data/logs/dj_turns.jsonl`, and don't start the production window yourself.

## Audit (30 Sep, from the code)

Sources read: every method in `client/src/lib/api.js`, the WebSocket commands in `server/routers/ws.py`, every
route in `server/routers/*.py`, `TOOL_REGISTRY`, `authorize_tool_call` and the `execute_*` methods.
DJ tools today (19): `pulse_search`, `pulse_detail`, `listener_context`, `city_trends`, `search_and_play`,
`playback_control`, `seed_radio`, `play_playlist`, `rate_track`, `get_news`, `get_weather`, `get_events`,
`find_places`, `get_artist_biography`, `explain_lyrics`, `play_shoutouts`, `save_shoutout`,
`save_shoutout_reply`, `save_review`.

### Existing tools that don't match the app (fix first)

| Tool | The app | The tool | Fix |
|---|---|---|---|
| `rate_track` | `preferences_service.set_track_preference` / `remove_track_preference`, with `broadcast_preference_change` | its own copy of the database write in `execute_track_preference`; no preference broadcast to the listener's devices | call the preferences service (rule 1) |
| `play_playlist` | `/api/queue/seed` refuses `favorites` / `discovery` for guests | no check; the tool reports "ok" for a guest (what the queue then holds is not checked yet) | same refusal in `authorize_tool_call` (rule 3) |
| `seed_radio` | `api.seedRadio(category, trackId)`: a station from any track (Seed Radio modal) | current track only | add `target` (current / previous / next), as `rate_track` has |
| `play_shoutouts` | Shoutouts panel: AI search on Enter (`use_ai_analysis`) | `community_on_air.pick` always passes `use_ai_analysis=False` | pass the AI weighting when there is a query, as `pulse_search` does for tracks |

### Missing, with the service the tool would call

| Area | What the app does | Route -> service | Smallest extension |
|---|---|---|---|
| Transport | Seek, restart the track | WS `seek` -> `playback_service.seek` | `playback_control` actions `restart`, `seek` + `position_s` |
| Queue | Remove one track | `DELETE /api/queue/remove/{id}` -> `playback_service.remove_from_queue` | `playback_control` action `remove` + `target` |
| Shoutouts | Like, super-like, ban, clear | `/api/shoutouts/{id}/preference` -> `preferences_service.set_shoutout_preference` (signed in, not your own post) | `rate_track` gains a shoutout target (default: the one that just aired, `community_engagement.last_aired`) |
| Shoutouts | Delete my own shoutout, reply or review | `DELETE /api/user_content/shoutouts/{id}` -> `user_content_service.delete_shoutout` (own only) | new tool `delete_my_post` (owner decision on confirming) |
| Shoutouts | List my posts; a track's reviews; a shoutout's replies | `/api/user/community` -> `items_by_user`; `/api/tracks/{id}/reviews`; `.../replies` | `pulse_search` `within: mine` for community/review kinds; full lists via `pulse_detail` |
| Devices | List online devices, move playback | `/api/devices`, `/api/devices/activate` -> `playback_service.transfer_playback` (target must be online) | new tool `move_playback` (device by name); rename/remove stay in the app |
| Radio Mode | On/off, each segment type, reviews, stings, feature interval, music source | `PUT /api/radio-mode` -> `preferences_service.set_radio_settings` + `radio_mode_service.set_user_prefs` (guests: WS `radio_mode_prefs`) | new tool `radio_settings` (one patch of `RadioPrefs` fields) |
| Sound | DJ voice / pings muted, audio quality | `PUT /api/user/profile` (`tts_muted`, `notifications_muted`), `PUT /api/auth/audio-quality`; both stored server-side | fold into the settings tool (owner decision) |
| Generation | New song from a request, more like a track, cancel, list jobs | `/api/generate` -> `suno_generation_queue_service.start_generation_job` with `rate_limit_service.reserve_generations` (signed in) | new tool `generate_song` (owner decision on limits) |
| Profile | Clear DJ conversation, reset persona | `/api/manage_user_data` -> `user_profile_service.delete_conversations` / `reset_persona` | owner decision |
| Stats | One track's analytics, my usage | `/api/analytics/track/{id}`, `/api/usage/me` | track analytics into `pulse_detail` for a track; usage stays in the app |

### Already matching

Track search (both modes, `within`), play / pause / next / previous, add to queue, all 10 seed modes and all 5
playlists (the tool's enums are built from the same tables), track like / super-like / ban / clear, record or type
a shoutout, reply or review (same editor verdict and pipeline).

### App-only (no tool planned)

Username, location and timezone, profile picture, uploads, track edits, artwork and artist profiles, device rename
and remove, offline downloads and cache, billing, share video, visual settings (FPS counter, video clips, visual
quality), login, admin usage, lyric timing generation.

### Corrections to the first pass

- The app has no queue reorder or clear: only remove (`Queue.jsx` -> `removeFromQueue`). Nothing to match there.
- Sound mode and audio quality are stored on the server (`users` row), not only per device, so a tool can change
  them through the existing services. Not checked yet: how an open client learns of the change (the profile
  route sends no WebSocket event, unlike `radio_mode_updated`).

## How to run the audit

1. **Front end, exhaustively.** Every method in `client/src/lib/api.js`, every `playback_command` sent in
   `PlaybackContext.jsx`, every WebSocket message type sent by the client, and every setting in UIState
   (`settingsState`, `interfaceState`) that the listener can change. One row each: what it does, the route,
   the service method.
2. **Server, exhaustively.** Every route in `server/routers/*.py` with its permission dependency.
3. **DJ side.** `TOOL_REGISTRY`, the `execute_*` methods in `dj_command_executor.py`, and `authorize_tool_call`.
4. **Match them.** For each front-end row: equivalent tool, partial, missing, or deliberately app-only (say why).
5. **Decide with the owner** which missing rows to build, in what order. Shoutout like / ban / delete is the
   first one named.
6. **Build each as the smallest extension** (rule 2), with a live test turn.

## Where things are

- DJ tools and limits: `server/services_radio/dj_tools.py`
- Actions behind the tools: `server/services_radio/dj_command_executor.py`
- One DJ turn: `conversation_service._process_tool_turn` -> `dj_prompt_service.gpt_dj_interactive_tools`
  -> `ai_service.run_gemini_tool_turn`
- Front-end API surface: `client/src/lib/api.js`
- Shoutouts, replies, reviews: `CLAUDE.md` section 17
- Track search and scope: `services/catalog_vector_search_service.py`, `services/listener_filters.py`

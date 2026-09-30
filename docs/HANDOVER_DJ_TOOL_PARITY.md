# Handover: DJ tools should match what the app can do

Written 30 Sep 2026. Status: audit not started; the table below is a first pass from a quick inventory.

## The goal

Anything a listener can do by tapping in the app, they should be able to ask the DJs to do by voice or text.
Today the two have drifted: the front end has grown features that the DJs' tools never got. The job is to go
through the code base in full, list every front-end capability, find its DJ-tool equivalent (or the lack of one),
and close the gaps.

The example that started this (30 Sep): the app's search box has two modes (typed keyword search with
`title: ...` style prefixes, and the AI search on Enter). The DJs' `search_and_play` only had the single-field
mode with the AI part switched off. The fix was not a new search: it was wiring the tool to the function the
front end already calls, and adding one variable (`within`: catalog / favourites / super_likes).

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

## First-pass inventory

Front-end capabilities come from `client/src/lib/api.js` and the playback commands in `PlaybackContext.jsx`.
DJ tools today (19): `pulse_search`, `pulse_detail`, `listener_context`, `city_trends`, `search_and_play`,
`playback_control`, `seed_radio`, `play_playlist`, `rate_track`, `get_news`, `get_weather`, `get_events`,
`find_places`, `get_artist_biography`, `explain_lyrics`, `play_shoutouts`, `save_shoutout`,
`save_shoutout_reply`, `save_review`.

| Area | What the app can do | DJ tool today | Gap |
|---|---|---|---|
| Track search | Typed keyword search, AI search, by category | `search_and_play` (both modes, `within`) | Closed 30 Sep |
| Search own likes | Favourites / super-likes views | `within` on `search_and_play`, `pulse_search` | Closed 30 Sep |
| Transport | Play, pause, next, previous | `playback_control` | None |
| Transport | Seek within a track, restart the track | none | Missing |
| Queue | Add, play from queue | `search_and_play` | None |
| Queue | Remove an item, reorder, clear upcoming | none | Missing |
| Stations | Seed radio (10 modes), playlists (favourites, discovery, top hits) | `seed_radio`, `play_playlist` | Check every mode is reachable |
| Track ratings | Like, super-like, ban, remove rating | `rate_track` | None |
| Shoutouts | Play, search by meaning | `play_shoutouts` | Check search parity with the Shoutouts panel's AI search |
| Shoutouts | Record / type a shoutout, reply, review | `save_shoutout`, `save_shoutout_reply`, `save_review` | None |
| Shoutouts | Like, super-like, ban a shoutout | none | Missing (the owner's example) |
| Shoutouts | Delete my own shoutout, reply or review | none | Missing (the owner's example) |
| Shoutouts | List my own posts, read a track's reviews, read replies | `pulse_detail` (top reply), pulse `review` kind | Partial |
| Devices | List devices, move playback to another device, rename | none | Missing ("play this on my phone") |
| Radio Mode | Turn talk breaks on/off, choose features, music source (both / human / AI) | none | Missing |
| Sound | DJ voice / notification sound modes, audio quality | none | Missing (some are per-device client settings) |
| Generation | Generate a song, check or cancel a generation job | none | Missing |
| Uploads / artists | Upload music, edit track details, artist profiles | none | Probably stays in the app; decide |
| Profile | Username, location, reset persona, clear DJ conversation | none | Missing; decide which belong on air |
| Stats | Station stats, track analytics, my usage | `city_trends`, pulse `chart` kind | Partial |
| Offline | Download for offline, cache management | none | Client-only; out of scope |
| Billing | Checkout, billing portal | none | Out of scope (never by voice) |
| Sharing | Share video export | none | Client-only; out of scope |

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

## Open decisions for the owner

- **Destructive actions by voice** (delete my shoutout, clear my conversation): do it on request, or have the
  hosts confirm first?
- **Client-only settings** (sound mode, tilt effects, audio quality are stored per device in the browser): the
  DJs would need a message to the client to change them. Worth it, or app-only?
- **Music generation by voice:** it costs money per song; which limits apply?

## Where things are

- DJ tools and limits: `server/services_radio/dj_tools.py`
- Actions behind the tools: `server/services_radio/dj_command_executor.py`
- One DJ turn: `conversation_service._process_tool_turn` -> `dj_prompt_service.gpt_dj_interactive_tools`
  -> `ai_service.run_gemini_tool_turn`
- Front-end API surface: `client/src/lib/api.js`
- Shoutouts, replies, reviews: `CLAUDE.md` section 17
- Track search and scope: `services/catalog_vector_search_service.py`, `services/listener_filters.py`

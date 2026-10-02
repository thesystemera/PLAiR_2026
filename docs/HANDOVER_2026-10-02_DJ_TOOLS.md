# Handover 2026-10-02: DJ tools don't speak one language

The owner's view: the misses come from the tools, not the LLM. The tools don't describe what they really do, and
they use different words for the same thing. Goal for the next session: **one vocabulary across the DJ tool
schemas, the code that checks their arguments, the app and the database, and descriptions that match what the code
accepts.** Read `CLAUDE.md` section 5 first. Owner rules apply: report findings and the exact planned change, get a
yes before editing code; fix at the source, no band-aids, no rules piled onto prompts; no keyword gates; go easy on
Gemini (announce, a handful of calls).

## What broke (last turns, 2026-10-02 18:17-18:21 NZ, user 1 "thesystemera", voice, owner out of the house)

From `data/logs/radio.log`; the DJ's full replies are in the `conversations` table (timestamps are UTC, 05:17-05:21).

```
18:21:29 [LISTENER] thesystemera (user 1): said to the DJ: "What about something for my favourites? Like, uh, my super likes or like, what should I do like this or something?"
18:21:29 [TTS_QUEUE_MANAGER] ... impulse started | 5 lines (jess+leo), 13 segments | "Not their own tunes. Rude."
18:21:41 [COMMANDS] DJ tool turn: review step ([STUDIO] You signed off partial: do what you told the listener you'd do now, the)
18:21:41 [WARNING] DJ reply repeated its script after the notes (1238 chars) - kept out of the notes and history
18:21:42 ... chat reply started | 11 lines (jess+leo), 24 segments | "Huh, seems like the old super likes vault is a bit... quiet right now."
18:21:43 [WARNING] DJ reply repeated its script after the notes (343 chars) - kept out of the notes and history
18:21:45 [PLAYBACK] thesystemera (user 1): queued 3 track(s)
18:21:45 [PLAYBACK] thesystemera (user 1): picked 'Unfolding Stillness' by Brian Eno      <- already playing since 18:19:59
18:21:48 [COMMANDS] thesystemera (user 1): DJ turn | Plan: search_and_play [station notes: 2 track · fresh plan] | tools: search_and_play invalid ('query' is required); ({play}{description})"something calming" -> playing Unfolding Stillness by Brian Eno | 4 round(s) | said 205 words | [TASK] complete
```

The DJ's own notes for that turn: "The initial attempt to play from the listener's super likes failed because a query
was implicitly expected by the tool when none was provided." On air it said the super likes were "empty". User 1 has
**158 super likes, 167 likes, 16 bans** (`track_preferences`).

Earlier turns in the same session:

```
18:17:14 said: "Hey, um, can we make some fun and stealing lights on those grunts?"     (fast transcript, base.en)
         DJ got: "Hey, um, can we mix it up? I'm kind of feeling like snow scrunch."   (quality transcript, large-v3-turbo)
18:17:25 DJ turn | tools: ({play}{track}) -> playing Tendon Prayer by Iceage            (probably "some grunge")
18:18:55 said: "Yeah, I love this one again."
         DJ got: "Okay, I'm going to put something in space."
18:19:03 DJ turn | Plan: just talk | tools: ({save_shoutout}) -> done                    <- should have been a like; a junk public shoutout now exists
18:19:49 said: "...I'll still back in time for the long move... not out of volume."
         DJ got: "Sorry guys, let's go back home from the long walk, something a bit more, I don't know, not so intense, I'm not out of range."
18:19:59 DJ turn | tools: ({play}{track}) -> playing Unfolding Stillness by Brian Eno
```

## Findings (verified, no code changed)

1. **`search_and_play` declares one thing and the code demands another.** The schema's only required field is `mode`;
   `query` says "Leave out when you pass track_id". `normalize_tool_args` (`dj_tools.py` ~line 910) requires `query`
   unless `track_id` is given. So `search_and_play(within=super_likes, mode=play)`, a call that matches the declaration,
   is rejected. Checked all 23 tools with only their declared required args: this is the only real schema/code mismatch.
2. **No way to say "play my super likes".** `within` (catalog / favourites / super_likes) only narrows a search that
   needs words. `play_playlist` has `favorites` (likes + super likes on shuffle, says neither) and no super-likes
   option. The `WITHIN` description invites "something of their own" asks that no tool can serve without inventing a
   query.
3. **Same thing, different words** (what the owner means by "not speaking the same language"):
   - `favorites` (play_playlist, `PERSONAL_PLAYLISTS`, radio_mode) vs `favourites` (`within`, `listener_filters.SEARCH_SCOPES`).
   - Super-like is `superstar` (rate_track rating), `super_likes` (`within`, preferences cache), `SUPER_LIKE` (DB), "super-liked" (descriptions).
   - `vocal` (search/seed category = delivery) vs `vocals` (who sings filter, added today).
   - `mode` = play/queue in search_and_play but = the aspect to match in seed_radio.
   - Internal default category `description` leaks into the brace command (`{play}{description}`).
   Next session: inventory every enum and parameter name in `dj_tools.TOOL_REGISTRY`, the app routes (`routers/`),
   the client (`api.js`, Radio/Catalog UI) and the DB, and propose one name per concept.
4. **A tool error reads to the DJ like an empty result.** After `'query' is required` the hosts told the listener the
   super likes were empty. Check how invalid-argument results are worded back to the model (`FAILED_ACTION_NOTE`,
   `authorize_tool_call` / `normalize_tool_args` errors) so an error is clearly "your call was malformed, here is the
   right shape", not "nothing found".
5. **Speech recognition is poor outdoors.** Fast (base.en, logged) and quality (large-v3-turbo, sent to the DJ)
   transcripts disagree completely on every voice turn above. "I love this one again" became "put something in
   space" and was saved as a public shoutout; the editor AI (`community_judge`) kept it. Not investigated yet: the
   recordings, mic/Bluetooth path, VAD. Delete that shoutout (user 1, ~05:19 UTC) once the owner agrees.
6. **Impulses that don't fit, then apologies.** The impulse library is young; closest scripts were off ("Chilled, at
   this hour?", "Workout music, they said..." when nobody said it). 3 of 4 replies then spent lines on "my bad, I
   blurted". Owner accepted the impulse note design on 2026-10-02 ("good enough"); raise it, don't change it unasked.

## Done (2 Oct, evening, owner approved)

- Findings 1-4 fixed. One name per concept: `favorites`, `super_like`, `clear`, seed `category`; `description` no
  longer offered or logged. `search_and_play` / `find_tracks` take `within` favorites / super_likes with no query.
  Malformed calls return `invalid_call` + `accepts`. The smart search skips the playing track.
- Owner's addition: favorites and super likes are weighted by the listener's own listens and skips
  (`services/listener_plays.py`, also the favorites playlist), and the DJ sees them (`yours` on candidates,
  `listener_context` most_loved / most_played). On user 1 the heavily skipped super likes drop to ~0.3 weight, the
  most-listened reach ~15.
- Test: `tests/dj_find_test.py 2 1025bb91d723 6` (the failing turn, signed in as user 1, playback faked): 2 of 2
  played a super like in 2 rounds.
- Still open: findings 5 (speech recognition, junk shoutout) and 6 (impulses).

## State of the repo

- Everything from this session is committed and pushed (last: `a41674a` vocals field). PLAiR restarted 17:10 NZ,
  smoke test 10/10.
- Untracked `*.wav` files and `upscale_ab/` in the repo root belong to other sessions; leave them.
- New today: `derived_tags.vocals` on every loaded track (`services/catalog_vocals.py`). ~2,000 not-yet-migrated
  tracks in `D:\catalog\metadata` lack it; after the upscale migration run
  `E:/AI_RADIO/.venv/Scripts/python.exe server/utils/backfill_vocals.py --apply` (~14 DeepSeek calls).

# Listener timeline: what aired for this listener

Written 30 Sep 2026. Status: design for the owner to react to. Nothing is built.

## The problem

The hosts can only point at three tracks (`current`, `previous`, `next`). Everything else a listener has heard is
out of reach: "like that shoutout" when three played, "the song before the news", "what was that review",
"say that headline again". On 30 Sep a first fix put the last five aired posts into every DJ prompt; the owner
rejected it (prompt weight for something rare) and it was reverted. Today a shoutout like or reply with no id
lands on the most recent post that played, which is a guess.

## The idea

One timeline per listener of everything that reached their ears, and one read tool the hosts call only when the
listener refers back to something. Nothing goes in the prompt.

## What already exists (read from the code)

| What | Where | Stored | Guests | Notes |
|---|---|---|---|---|
| Tracks played, skipped, completed | `play_events` (`PlaybackState._log_play_start` / `_log_play_end` -> `analytics_service.log_play_event`) | database | yes, by `session_id` | start time, duration, completion, skip reason |
| Shoutouts, replies, reviews played in the DJ mix | the same `play_events` table, with the post id in `track_id` (`community_engagement.record_on_air_play`) | database | yes | deduped 6 h per listener and post |
| The same posts, last 30 min | `community_engagement._aired` | memory | yes | what `save_shoutout_reply` and `rate_track` fall back on |
| Tracks around the current one | `PlaybackState.history` (50) | memory | yes | behind `current / previous / next` |
| Segments the hosts aired (news, weather, gigs, places, bio, lyrics, shoutouts) | `conversations.bot_response` with `message_type` | database | no | full script, signed-in only |
| What the hosts said between tracks | `content_bank._airings` | memory | yes | switched off (`DJ_AIRED_MEMORY_ENABLED=false`) |
| News stories already aired | `news_aired` | database | yes | per story |
| Pulse items already offered | `Pulse._offered` | memory | yes | per item |

So the two things the shoutout problem needs, tracks and listener posts, are already written to one table for
users and guests. The timeline is mostly a way to read what is there.

One catch: `analytics_service` buffers events and writes them in batches of 50, so the last few minutes are
often not in the database yet. The read has to include the buffer.

## Design

**One read path:** `services/listener_timeline.py`, `timeline(user_id, session_id, kinds=None, since=None,
limit=10) -> [Entry]`. It reads `play_events` for this listener (plus the unwritten buffer), newest first, and
labels each row from the catalog or the community store.

**Entry:** `at` (time), `kind` (`track` / `shoutout` / `reply` / `review` / later `segment`), `id`, `label`
("'Exit Ramp Liturgy' by Modest Mouse", "shoutout from Briony (Auckland): 'Hey John, how's it...'"), and for
tracks `outcome` (played / skipped / still on).

**One tool:** `what_aired` (read, cost memory), parameters `kinds`, `minutes` (how far back), `how_many`.
The hosts call it when the listener points at something that already played. The result carries ids.

**Action tools take an id from it.** `rate_track`, `save_shoutout_reply`, `save_review`, `seed_radio`,
`explain_lyrics` and `pulse_search about_track` accept a timeline id next to `current / previous / next`, which
stay as shortcuts for the common case. With no id and more than one candidate, a tool says so instead of
guessing.

**No new table in phase 1.** `play_events` is the store. No new writes for tracks or posts.

## Phases

1. **Timeline read + `what_aired`, tracks and listener posts.** Closes the shoutout hole and gives "the song
   before that one", "the one I skipped", "that track from twenty minutes ago". Includes the buffer read.
   Removes the guess from `rate_track` and `save_shoutout_reply`.
2. **Segments.** Record each aired segment (kind, one-line label, the ids it used) as a timeline entry for
   guests and users. This needs a small new write, since `conversations` only covers signed-in listeners and
   holds a full script, not a label. Gives "what was that gig", "say that headline again".
3. **Fold the other ledgers in, one at a time:** the 30-minute aired list, the hosts' recent-airings memory,
   and possibly the news and pulse "already aired" ledgers read from the same place.
4. **Optional: a "recently heard" view in the app** on the same read.

## Decisions for the owner

1. **Segments (phase 2): a `kind` column on `play_events`, or a separate small table?** `play_events` feeds top
   hits and charts, so segment rows there would have to be filtered out of every analytics query. I'd use a
   separate table for segments and leave `play_events` alone.
2. **How far back can the hosts look?** I'd default to the last 2 hours and allow up to 24.
3. **Post plays are deduped for 6 hours**, so a shoutout heard twice shows once, at its first play. Fine for
   "which one did they mean"; say if the timeline should show every play.
4. **Phase 1 only for now, or 1 and 2 together?** Phase 1 is small and fixes the actual bug.

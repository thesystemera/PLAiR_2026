# Listener timeline: what aired for this listener

Written 30 Sep 2026. Status: phases 1 and 2 built and tested on a private backend (1 Oct). Phase 4, the view in the app, built 1 Oct (see "Phase 4 as built"). Phase 3 is open.

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

## Phase 1 as built (1 Oct)

- `server/services/listener_timeline.py`: `timeline(user_id, session_id, kinds, minutes, limit)` reads
  `play_events` for the listener (by `user_id`, guests by `session_id`) plus the unwritten
  `analytics_service.event_buffer`, pairs each track's start with its end (`played through` / `skipped` /
  `on air now`), and labels tracks from the catalog and posts from the community store.
- Tool `what_aired` (read, memory): `kinds` (track / shoutout / reply / review), `minutes`
  (`DJ_TIMELINE_DEFAULT_MINUTES` 120, up to `DJ_TIMELINE_MAX_MINUTES` 1440), `how_many` (10, up to
  `DJ_TIMELINE_MAX_ENTRIES` 25). Not a core tool: the Producer plans it, or the hosts request it.
- `rate_track`, `seed_radio`, `save_review` and `explain_lyrics` take `track_id` (any catalog track id, resolved in
  `_resolve_track_id`); `rate_track` `shoutout_id` and `save_shoutout_reply` `parent_id` take a post id.
  `pulse_search about_track` still takes only current / previous / next.
- No more guessing: with no id, a shoutout like or reply acts only when a single post played in the last 30
  minutes; otherwise the tool tells the hosts to call `what_aired` or ask.
- Tested live (test account and a guest): like a named shoutout after several played, like an earlier track by
  id, "what have I been listening to", "what was that first song". Not tested: a reply to an earlier shoutout by
  id, and reviews in the timeline.
- Seen in testing: for "two songs ago" the hosts picked the track one back and said its title on air. The
  timeline was right; the counting is the model's.

## Phase 2 as built (1 Oct)

- New table `aired_talk` (`database/models.AiredTalk`: user_id, session_id, kind, label, text, seconds,
  aired_at), kept `DJ_TIMELINE_KEEP_DAYS` (2: the hosts can look back 24 hours at most; raise it when the
  search by meaning is built). Clearing the conversation in the app also deletes the listener's rows. `play_events` is untouched, so charts and top hits see nothing new.
- Everything the hosts voice is written there as plain text with speaker names, at the one place a voice stream
  finishes airing (`TTSQueueManager._generate_tts_stream` -> `listener_timeline.record_talk`): produced segments
  (news, weather, events, places, biography, lyrics, shoutouts), chat replies and between-track talk. Radio Mode
  talk breaks are rendered ahead, so they are recorded when they go on air (`radio_mode_service`,
  `talk_break_start`). Cancelled streams, fillers and stings are not recorded.
- Timeline kinds `segment` (produced segments and talk breaks) and `talk` (chat replies, between-track lines).
  `what_aired` lists segments by default and talk only when asked for; an entry shows how it opened, and
  `what_aired(id=...)` returns everything that was said (`talk_detail`, the listener's own entries only).
- Time is always a window, chosen by the hosts from the listener's words (`listener_timeline.window`):
  `around_minutes_ago` for one rough moment (the studio looks either side: half the distance, at least 10 and at
  most 60 minutes, so 20 -> 30 to 10 minutes ago) and returns the entries closest to that moment;
  `from_minutes_ago` / `to_minutes_ago` for a stretch (default the last 120 minutes, at most 24 hours). A track
  counts if it was on air at any point in the window, not only if it started in it. The result says what stretch
  was looked at (`looked_at`) and how many entries were left out (`not_shown`).
- Size of a result: a list is capped at `how_many` entries (10, at most 25), each with only its first 160
  characters (7 entries measured about 2,200 characters); one full entry is capped at 3,000 characters. Entries
  carry `min_ago` and, when the listener's timezone is known, the local clock time (`at`).
- Tested live (1 Oct): "about 45 minutes ago" -> around 45 (68 to 22 min ago), "between an hour and an hour and a
  half ago" -> 90 to 60, "the last ten minutes" -> last 10. Not covered: asking by clock time ("around nine
  o'clock"); the hosts would have to work out the minutes themselves.
- Tested live as a guest: a news bulletin and a weather forecast aired and were recorded; "run me through
  everything I've heard" listed them with the chat replies; the full text of a segment reads back; another
  listener can't read it. For questions about the last few minutes the hosts answered from the conversation
  already in their context, without the tool. Not tested: Radio Mode talk breaks in the timeline, and the hosts
  calling `what_aired(id=...)` themselves in a turn.
- This table is the base for phase 3's "what were you talking about five hours ago": a semantic source over
  `aired_talk` (the LifeSpan conversation-vector pattern) would let the hosts search it by meaning.

## Phase 4 as built (1 Oct): the timeline in the app

- A Timeline toggle (clock icon) in the Radio panel header, next to the message filters. On: the filters dim and
  the conversation is replaced by `ListenerTimeline.jsx` (the conversation stays mounted underneath, so nothing
  streaming is lost); tapping a filter goes back to the conversation.
- It lists everything that aired for this listener in the last 24 hours (the most the timeline allows), newest
  first: songs with played through / skipped / on air now, shoutouts, replies, reviews, segments and talk breaks,
  chat replies and between-track talk. Talk and segment rows open to the full text that was said
  (`GET /api/timeline/entry?id=aired:N`, the listener's own rows only; the id is a query parameter because the
  request guard refuses `:` in paths).
- `GET /api/timeline?hours=&limit=` is the same `listener_timeline.timeline()` read the hosts' `what_aired` uses,
  so what the listener sees is what the hosts can point at. It refreshes every 30 s, on a track change and when a
  DJ stream ends; offline it says it needs a connection.
- Checked against the real database: entries, the full-text read, and another listener or a guest getting 404.
  Not yet seen in a browser.

## Phases

1. **Timeline read + `what_aired`, tracks and listener posts.** Closes the shoutout hole and gives "the song
   before that one", "the one I skipped", "that track from twenty minutes ago". Includes the buffer read.
   Removes the guess from `rate_track` and `save_shoutout_reply`.
2. **Segments.** Record each aired segment (kind, one-line label, the ids it used) as a timeline entry for
   guests and users. This needs a small new write, since `conversations` only covers signed-in listeners and
   holds a full script, not a label. Gives "what was that gig", "say that headline again".
3. **Search what the hosts said by meaning** (owner keen, 1 Oct): a semantic source over `aired_talk`, so
   "what were you on about this morning" finds it without paging through the list. Then fold the other ledgers
   in, one at a time: the 30-minute aired list, the hosts' recent-airings memory (`content_bank._airings`), and
   possibly the news and pulse "already aired" ledgers read from the same place.
4. **Optional: a "recently heard" view in the app** on the same read.

## Decisions for the owner

1. **Segments (phase 2): a `kind` column on `play_events`, or a separate small table?** `play_events` feeds top
   hits and charts, so segment rows there would have to be filtered out of every analytics query. I'd use a
   separate table for segments and leave `play_events` alone.
2. **How far back can the hosts look?** I'd default to the last 2 hours and allow up to 24.
3. **Post plays are deduped for 6 hours**, so a shoutout heard twice shows once, at its first play. Fine for
   "which one did they mean"; say if the timeline should show every play.
4. **Phase 1 only for now, or 1 and 2 together?** Phase 1 is small and fixes the actual bug.

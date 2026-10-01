# Open work and known issues

The one list of what is still open. Consolidated on 1 Oct 2026 from the September handovers, audits and to-do
docs (now deleted; they are in git history before this commit). Add new items here instead of writing a new
handover. Reference docs that carry their own detail are linked from each item.

Grouped by effort, easiest first. "Checked" means it was confirmed against the code or the machine on 1 Oct;
anything else is carried over as written.

## 1. Quick wins

- **Press PLAiR Start, then run the smoke test**, so the 30 Sep / 1 Oct work is live. The studio-message wording
  fix (section 2) has only been measured by replay.
- **`PROJECT_OVERVIEW.md`** had its engine, encoder and DJ facts corrected on 1 Oct, but its statistics and
  feature lists were not re-counted.
- **Install screenshots** need recapturing after UI changes (they have no WebGL background); optional iOS splash
  images were never made. Masters and the rebuild script are in `brand/2026-refresh/`.

## 2. DJ turn: bugs and things to watch

- **Fixed 1 Oct, not yet live: the DJ's fallback model failed every follow-up round.** gemini-3.5-flash-lite
  answers 400 INVALID_ARGUMENT to a thinking budget of 0 (even on a plain prompt) and accepts 1, which uses no
  thinking tokens; 2.5-flash and 3.5-flash accept 0. It had nothing to do with history or thought signatures.
  `llm_router.fit_thinking` now raises the budget to the model's floor (`GEMINI_THINKING_BUDGET_FLOOR`). Checked
  through the real router: tool round then follow-up on the fallback model, both fine. Watch the next 429 in
  `radio.log`: the `dj: … failed (… 400 …)` line should not follow it.
- **Fixed 1 Oct, not yet live: second hand-off line after scheduling a segment.** Cause: the segment result said
  "your line was the hand-off, go to your notes" and the studio message right after it said "reply on air now".
  After a line plus a scheduled segment the studio message is now `ai_service.HANDED_OFF_NOTE`. Replays of the
  four recorded turns (`tests/dj_followup_replay.py`): second line in 11 of 12 before, 0 of 12 after, no stray
  text. Not changed: when the hosts call the segment tool without a line, the hand-off they then speak can run
  long (33 to 79 words in three recorded turns).
- **Repeated DJ script after `[TASK]`.** Gemini sometimes writes the whole script twice in one reply (4 of 45
  turns, about +45% cost on that round). It no longer reaches the notes or history, but it is still generated.
  Cause unproven. Lead: history rows show content after `[TASK]` (the `[STUDIO TOOLS]` log), which teaches the
  model to keep writing. Plan: replay the four turns (`data/logs/dj_turns.jsonl*`, `rounds[].text` with two
  `[BROADCAST]`), count repeats, move `[STUDIO TOOLS]` before the script in the history, replay again. The `parts`
  field in the trace shows whether the repeat arrives as its own part.
- **Stray text before the on-air reply after a tool call** (found 1 Oct, 2 of 62 live turns; cause found, wording
  fixed, watch for recurrence). With thinking off in the follow-up round, Gemini sometimes continues the studio's
  `[STUDIO]` message as if it were its own text or rewrites the STUDIO CLOCK block before `[BROADCAST]`. When the
  stray text contains `[INTERNAL DIALOGUE]`, the real reply lands in the notes and the fragment fails in the voice
  queue ("NO SPEAKER TAG FOUND"). Replays (`tests/dj_followup_replay.py`): a studio message ending on the open
  conditional gave stray text in about 40% of replies; the old wording 3 of 160; the same words with the sentence
  closed (now in `ai_service.run_gemini_tool_turn`) 0 of 142. A thinking budget in the follow-up round is not the
  answer: 256 or 1024 tokens made the hosts call the same tool again in 1-3 of 12 replies. Not done: a parser rule
  that keeps text outside any channel off air (owner's call; it would hide a recurrence, not prevent it).
- **Thinking is off after the first tool round.** Known risk: narrated tool calls ("I will call …") in multi-step
  turns. Setting to revert: `DJ_TOOL_FOLLOWUP_THINKING_BUDGET`.
- **The announcer's length.** One announcement ran 32 s in a ~9 s gap. The log line `announcement | window … |
  wrote N words` and the saved prompt in `dj_turns.jsonl` will show what happened; nothing has been read yet.
  Suspect: the time constraint sits mid-prompt, before the talking-points menu.
- **`search_and_play` plays the closest match straight away** when nothing matches, before the DJs can ask.
- **Not yet tried in the real app:** a real move between two devices, the guest Radio Mode change in a browser, a
  reply to an earlier shoutout by id, Radio Mode breaks and reviews in `what_aired`, asking by clock time, the
  exact-title queue change, the "tool call limit" refusal message, favourites search against a large like list,
  Radio Mode breaks and the announcer at the measured pace (2.0 words/s).
- **Older, not investigated:** replies that answer the conversation history instead of the question ("Still not
  Tom, mate." to a comedy question, 29 Sep, before the tools-only DJ); `Queen Street` without a city geocodes to
  the wrong one (harmless while prompts ask for full place names).

## 3. Requested on 30 Sep, not done

- **Offline unit tests for every DJ tool** (checked: only `tests/dj_tool_loop_test.py` exists). No backend, no
  LLM: `dj_tools.normalize_tool_args` for every tool, `authorize_tool_call` limits, `command_string`, the tool
  loop (empty replies and recovery, the review step, MAX_TOKENS, the last round, `_done_with`, parallel calls),
  `talk_clock` length maths and the pace meter, news identity (`news_links`, `news_store.title_key` / `item_key`,
  `news_reader.summarize`). Next step up: each `DJToolRuntime` handler against fake services, one test per row of
  `TOOL_REGISTRY`. Add the run to `scripts/check-quality.ps1`.
- **Audit of the 30 Sep changes** from the latest logs: leftovers (`NEWS_REPORT_DEPTHS` and `SEGMENT_DEPTHS` share
  labels; confirm they can't drift), prompt weight before and after (studio clock, pulse ids, new tool
  parameters), each new log line appears, docs match code.
- **Token cost pass.** A DJ call is ~7,900 input tokens, 87% cached, about $0.0012. Count rounds per turn over a
  day and find third rounds that added nothing (the `[TASK]` review step); the `(75 chars)` notes in the example
  dialogue are copied into replies (owner's call, they may teach overlap timing); guest padding ("Unknown
  Location", "None (Guest)"); City Pulse noise (weather and area items carry unrelated "linked" news, two takes of
  one song list as identical lines); whether the ~3 s filler before every reply is still wanted.

## 4. Owner decisions

Detail for each is in `docs/AUDIT_2026-09-30_DJ_VOICE.md` (sections 2, 3 and 5).

- **Streets on air.** The listener context lets the hosts name the listener's street. Privacy.
- **Segment scripts cached word for word** (bio and lyrics for 7 days; news 20 min; weather 1 h).
- **Clip-match thresholds:** TTS (0.975), paralanguage (0.85) and sound effects (0.75) were re-measured on mpnet on
  2026-10-01; impulse (0.75) and breaths (0.65) still date from flan-T5. Sound effects are cache-only, so a `%sfx%`
  with no match at 0.75 is dropped.
- **Sound-effect beds** were −80 dB (silent) in the old player and are audible now.
- **Proximity effect is about half as strong** as the old player (`&N&` lines go through the effects chain once,
  not twice).
- **Stings outside Radio Mode** replace some host links, and every link gets a talking-points menu.
- **Stutter** was toned down for Orpheus; Chatterbox may handle it.
- **Notes on every reply:** keep `[INTERNAL DIALOGUE]` and `[TASK]` on every reply or not.
- **The Producer sees only the latest sentence**, not the conversation ("yes do that" is routed blind).
- **Perth watermark is off** in Chatterbox; not confirmed by the owner.
- **Geolocation prompt** (checked: `useGeolocation.js` still asks for high accuracy and reverse-geocodes in the
  browser with a public LocationIQ key). Suggested: ask after a "local news & weather?" opt-in, low accuracy,
  geocode on the server (`area_geocode`).
- **Listener timeline and City Pulse** have their own decision lists: `docs/LISTENER_TIMELINE.md` ("Decisions for
  the owner"), `docs/CITY_PULSE.md` section 13.
- **"In the style of"** framing for artist trivia; a traffic segment for Radio Mode; non-English Google News
  editions; the numbers/dates override for cached-line similarity.

## 5. Owner to-dos (not for an agent)

- **Rotate the API keys and the JWT secret.** The whole `.env` was visible in a screenshot on 28 Sep, and old
  nginx access logs contain login tokens. Rotating the JWT secret logs everyone out once; the old log can then
  be deleted.
- **Test on a real iPhone:** device checklist and airplane-mode checklist in `docs/MOBILE_LAUNCH_READINESS.md`.
  Open there: DJ voice on iPhone, screen-locked playback, crossfades.
- **Stripe live mode** when ready (test mode today).
- **Listen and decide:** the music beds, the stings and the "Play Air" pronunciation, and whether to run the
  catalog re-master (`server/utils/reprocess_catalog_audio.py`, not yet run; A/B set first). "Park Bench
  Philosophy" (e86b6267…) needs a re-master from intermediates. FlashSR has no licence: keep Apollo.
- Done since they were listed (checked): nginx gzip and `/ws` without an access log are applied; DeepSeek is
  topped up (no errors on 30 Sep); everything is committed and pushed.

## 6. Medium jobs

- **Listener timeline roadmap** (`docs/LISTENER_TIMELINE.md`): search what the hosts said by meaning, then fold
  the other "already aired" lists in. The view in the app (Timeline toggle in the Radio header) was built 1 Oct and
  has not been seen in a browser yet.
- **After a server restart the queue and station are lost** (checked: the server keeps playback state in memory
  only, and the client does not send its station back). Offline mode covers the rest of the old "session resync"
  issue: the song and position are handed back, a server that is down shows the offline notice, and a reconnect
  follows the server's snapshot.
- **Responsiveness on track changes** (reported 28 Sep, no record of it being resolved). Profile before changing
  anything. Candidates: optimistic next/prev and the latest-wins transport in `PlaybackContext.jsx`, the hold
  logic in `audioEngine.js`, the document-level listener in `microMotion.js`, the queue refill before advancing
  in `playback_state.py`, artwork prefetch competing with audio fetches.
- **Generation progress messages** (`plans/unified_ws_protocol.md`, not started): seven message types and dead
  `*_stage` fields to fold into one `task_progress`.
- **UIState still holds some business logic** (`docs/UISTATE_CLEANUP_TODO.md`). The September cleanup was about
  re-renders (slice selectors, `useUIState` removed, one notice store) and did not move these out; checked 1 Oct
  in `UIStateContext.jsx`: the tilt sensor and its animation loop, music ducking, settings and seed-mode writes to
  storage, the download-state updater, video-clip loading. It all works; this is tidiness, low priority.
- **Talk-break progress bar: built 1 Oct, not yet seen in a browser.** A break only goes on air once it has fully
  rendered, so its exact length is already known (`stagedDuration`); no estimate is needed. While a break is on
  air the player's waveform becomes an ON AIR bar (title, elapsed / total). The playing device reads the DJ
  stream's own clock (`DJStreamPlayer.getProgress`, `TalkBreakController.progress`); other devices work it out
  from `talk_break.on_air_at_ms` and `server_time_ms` (they don't freeze while the break is paused). Check it on
  the next break after PLAiR Start.
- **LifeSpan ideas not ported** (audit section 3): text normalisation before TTS (units, URLs, "feat."), filler
  lines shown in chat and told to the model, richer clip metadata, a stateful limiter and reverb (A/B first),
  prelude and interlude fillers, per-round timing and cost in the turn log, history hygiene.
- **City Pulse:** the places city sweep was never seen running after deploy (`regional_knowledge_refresher`);
  `local_nuggets` view went missing once on a running backend (30 Sep, cause not found); RNZ blocks page reads
  (no summaries); watch `[WEB] … rate limited` for Google link lookups.
- **Music location:** an artist hometown or scene `where` on catalog tracks, and the uploader's location on
  human uploads.
- **Catalog list re-renders** (low): `Catalog.jsx` `renderTrack` changes with `currentTrackId` and
  `queuedTrackSet`, and `VirtualScroller` compares `renderItem` by reference.
- **Small caches never added** (low): device lookups and conversation history hit the database each time.
- **After launch** (`docs/MOBILE_LAUNCH_READINESS.md`): iOS install guide, `user-scalable=no`, an "update ready"
  prompt, three.js tree-shaking in `offlineVideoRenderer.js`, iOS mic uploads named `.webm`.
- **Data Saver** should prefer downloaded tracks in queue fill (a TODO from the old playback doc; not checked).

## 7. Large jobs

- **Depth-map upscaling** (owner, 1 Oct). No written plan yet.
- **Chatterbox headroom.** S3Gen re-reads each voice's 10 s reference on every call. Options: a CUDA graph for
  the flow estimator (~28%), overlapping the vocoder with token generation, a shorter reference (changes the
  voice; owner's call).
- **DJ voice on iPhone**, if the device test fails: a second output from the live encoder (fragmented MP4/AAC).
- **Streaming mic input** (LifeSpan: VAD and a live draft transcript while the listener speaks).
- **Voiced speech at higher quality:** cached host lines are MP3 and the live stream is fixed at 128 kbps Opus;
  lossless storage and a bitrate that follows the listener's music quality were proposed.
- **Other engines if the RTX 6000 frees up:** `docs/TTS_ENGINE_RESEARCH.md` (VoxCPM2, Step-Audio-EditX).

## Done on 1 Oct 2026 (cleanup)

- The demo pop-up text (`client/src/content/DEMO_MODE_INFO.md`) was brought up to date.
- The four old flan-T5 index files and the 20 old unslugged `*_embeddings` tables (each had an `_mpnet_` twin) were
  removed; the TTS tables were not touched. Two unused screenshots were removed.
- `App.jsx` no longer tries to add generated tracks to the queue itself: the server already adds them
  (`suno_generation_queue_service` calls `playback_service.add_to_queue` before it sends
  `generation_batch_completed`), and the client call never ran because it read `.id` from plain id strings.
- Docs corrected against the code: `ARCHITECTURE_SSOT.md`, `NODE_SYSTEM.md`, `VECTOR_DB_ARCHITECTURE_TTS_PATTERN.md`,
  `NGINX_HTTPS_SETUP.md`, `OFFLINE_MODE.md` (old design notes cut). `PLAYBACK_ARCHITECTURE.md` was deleted: it
  taught removed events and a forbidden pattern, and `CLAUDE.md` sections 2, 3, 6 and 16 cover the subject.
- Decided: the community editor's feedback wording stays as it is (written for the DJs, shown as is in the app).

## Where things are kept

- Old Orpheus voice caches: `D:\tts_candidates\orpheus_clip_backup`. Engine candidates, bake-off WAVs and purged
  conversations: `D:\tts_candidates`.
- WSL crash dumps moved off C: on 30 Sep (another project filled the disk): `D:\wsl-crash-dumps`.
- Private test backend: port 8011 with a copy of `data/embeddings` and `TTS_SERVER_EXTERNAL=true` (CLAUDE.md).

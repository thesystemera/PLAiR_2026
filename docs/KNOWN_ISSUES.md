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

- **Fixed 2 Oct: DJ tools didn't speak one language.** "Play my super likes" now works
  (`search_and_play(within=super_likes)` with no query, 2 of 2 test turns as user 1); one name per concept
  (`favorites`, `super_like`, `clear`, seed `category`); malformed calls return `invalid_call` with the accepted
  arguments; the smart search skips the track already playing; favorites and super likes are weighted by the
  listener's own listens and skips (`services/listener_plays.py`), and the DJ sees those counts. Still open from the
  same handover: speech recognition outdoors, the junk shoutout from 05:19 UTC (delete once the owner agrees), and
  ill-fitting impulses. Details: `docs/HANDOVER_2026-10-02_DJ_TOOLS.md`.

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
- **Picking a track from clues: built 1 Oct, watch it live.** `find_tracks` lets the hosts look before they play. End-to-end runs (`tests/dj_find_test.py`): the Sonic Youth request 2 of 2, Pixies 2 of 2, Aphex Twin 2 of 2, a plain vibe 2 of 2; the Stereolab one ('French woman over Moog synths ... play them') 0 of 2: the hosts read 'play them' as a vibe and searched blind.
- **Every AI track now has an inspired-by artist (1 Oct).** 218 tracks had none: 113 had an artist name but no `derived_tags.inspired_artist` (which is what the search's artist category reads for AI tracks, so a name search couldn't see them) and 105 had no artist at all ('Afterimage' showed as 'by -'). The enrichment prompt only filled it when the style literally said 'in the style of X'. `server/utils/backfill_inspired_artist.py` filled it from each track's own data: the artist name (113), the artist in the original request (88), the first similar artist (16), one by hand (Coolio). Originals backed up in `D:\catalog\metadata_backup_2026-10-01_inspired_artist`. The enrichment prompt now always names one artist.
- **Catalog consistency audit (1 Oct, `server/utils/catalog_audit.py`, read-only).** Clean: titles, durations, styles, genres, created dates, upload credits, and every file the Asset Doctor checks. Fixed: an AI track's artist is now compulsory (`services/catalog_credit.py`: the enrichment output requires it and falls back to the track's own data; the Asset Doctor flags and repairs a missing one from the same data, the AI only when there is nothing to go on), and the search's artist category reads the credit as well as the inspired-by artist (106 tracks such as 'Ammonite', credited to Pixies but inspired by Steve Albini). Video search terms filled 1 Oct for all 1309 tracks that had none (`server/utils/backfill_video_search_terms.py`, DeepSeek only, 15 tracks per call, 88 calls; 13 tracks that kept them in generation_params moved to derived_tags). Who sings is now `derived_tags.vocals` on every track (2 Oct, `server/utils/backfill_vocals.py`: 1221 from their settings, the 140 without vocal_gender by DeepSeek in 10 calls -> 96 duet, 31 unknown, 11 male, 2 female). Still open, owner's call (each needs an AI call per track): 112 without vocal_style_keywords, 4 without a lyrical_interpretation.
- **The catalog's vocal category now says who sings** (male / female / duet / instrumental from `derived_tags.vocals` plus the style's sentences about the vocals; it used to be delivery keywords only). Measured: 'instrumental, no vocals' 0 -> 6 instrumentals in the top 10, the French-accented singer #2 -> #1. The Annoy index (seed radio) keeps the old vocal vectors until its next rebuild.
- **Not yet tried in the real app:** a real move between two devices, the guest Radio Mode change in a browser, a
  reply to an earlier shoutout by id, Radio Mode breaks and reviews in `what_aired`, asking by clock time, the
  exact-title queue change, the "tool call limit" refusal message, favourites search against a large like list,
  Radio Mode breaks and the announcer at the measured pace (2.0 words/s).
- **Older, not investigated:** replies that answer the conversation history instead of the question ("Still not
  Tom, mate." to a comedy question, 29 Sep, before the tools-only DJ); `Queen Street` without a city geocodes to
  the wrong one (harmless while prompts ask for full place names).

- **Fixed 1 Oct evening, watch it live: paralanguage read aloud as words.** The DJs sometimes write an object noise as paralanguage (`~taps desk~`, about 3% of tags); the paralanguage writer then bracketed or imitated it (`[taps desk]`, `*click*`) and Chatterbox read it aloud. The writer's engine-tag paragraph now has it voice the host only (15 actions x 2 runs on DeepSeek: clean), stray brackets are snapped to the closest engine tag or dropped, and every contaminated take was purged.
- **Fixed 1 Oct evening: 'mate' on almost every reply.** A 'cheers, mate' example added to the channel rules that evening raised it from 0.4 to 1.5 per reply; the example is gone and the 108 cached lines and 2 filler scripts that said 'mate' were purged.

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
  one song list as identical lines).

## 4. Owner decisions

Detail for each is in `docs/AUDIT_2026-09-30_DJ_VOICE.md` (sections 2, 3 and 5).

- **Streets on air.** The listener context lets the hosts name the listener's street. Privacy.
- **Segment scripts cached word for word** (bio and lyrics for 7 days; news 20 min; weather 1 h).
- **Clip-match thresholds:** TTS (0.975), paralanguage (0.85) and sound effects (0.75) were re-measured on mpnet on
  2026-10-01; breaths (0.65) still date from flan-T5. Impulse and interlude scripts always play the closest script and
  write a better one under `FILLER_LEARN_BELOW` (0.6, LifeSpan's value, not yet measured on mpnet). Sound effects are cache-only, so a `%sfx%`
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
- **Listen and decide:** the music beds, the stings and the "Play Air" pronunciation.
- **Music chain signed off by ear (owner, 2 Oct):** Apollo -> RoFormer vocal split -> ClearVoice
  MossFormer2_SR_48K alone on the vocal, level-matched (the speech denoiser and envelope warp dulled singing) ->
  SonicMaster with the owner's original prompt (no template), 20 steps, 50/50, blend compensation on (above 2 kHz its output
  has ~0 coherence with the input, so a plain 50/50 lost 3 dB) -> master EQ at 100%. RoFormer is ~3.5x slower
  than Demucs (0.45 vs 0.13 s per audio second). Still to do: the catalog re-master as a background job that
  renders only with no listeners online and free VRAM (the live station uses 18-21 GB of the P6000; a render
  next to it starved Whisper on 2 Oct), and the master's notch finder, which always cuts the 4 biggest
  spectral peaks even when they are the song's own notes. "Park Bench Philosophy" (e86b6267…) needs a
  re-master from intermediates. FlashSR has no licence: keep Apollo.
- Done since they were listed (checked): nginx gzip and `/ws` without an access log are applied; DeepSeek is
  topped up (no errors on 30 Sep); everything is committed and pushed.

## 6. Medium jobs

- **Resemble Enhance on music vocal stems (owner, 3 Oct, parked):** it gave the DJ voices only a small lift
  over CVSR, too slow to run live, but it may help vocals in the music remaster chain (offline). Runner and setup:
  `scripts/voice_restoration/`, findings in `docs/TTS_ENGINE_RESEARCH.md`.
- **Between-track talk and stings (owner, 1 Oct):** the station clock doesn't come in as often as it could,
  some transitions have nothing between the tracks, and the cool-offs between announcer, stings and Radio Mode
  breaks are tangled. Look at it as one schedule. The announcer's window still assumes the next song starts when
  the current one ends (`_analyze_transition` offsets the intro by the full duration and deducts a share of the
  crossfade); it should use the crossfade plan's real start (`crossfade_plan.plan`, `optimal_start_ms`), which
  it now gets from the same analysis.
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
- **LifeSpan ideas not ported** (audit section 3): text normalisation before TTS (units, URLs, "feat."), richer
  clip metadata, a stateful limiter and reverb (A/B first), prelude, interject and interrupt fillers, per-round
  timing and cost in the turn log, history hygiene. Impulse and interlude scripts were ported on 2026-10-01.
- **Fillers next (owner's ideas):** impulses as a "fly on the wall" view of the studio; a vector conversation store
  so impulse and interlude keys match by meaning across sessions; interludes aired mid-turn are not yet told to
  the DJ model between tool rounds (only the impulse is, via `[ON AIR JUST NOW]`).
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

- **Music catalog re-render (running since 3 Oct, 22:31).** `server/utils/process_backlog.py --rerender` on the
  RTX 6000: ~870 Suno songs that never had a master, then ~1,350 masters below chain version 3; ~2.5 min per song
  (GPU-bound), a few days in all. Progress in `data/logs/backlog.log`; restart the same command if it stops
  (finished songs are skipped). While a song is re-rendered (~3 min) its old Opus/WebM are gone, so playback may
  skip it. `utils/relevel_catalog.py` is moving the remaining -14 LUFS masters to -16 by gain (log
  `data/logs/relevel_run.out`). Chain details: CLAUDE.md section 18.
- **Music: open ideas, not started.** SonicMaster fp16 on the RTX (`SONIC_MASTER_PRECISION=auto`, 1.7x faster, a
  slightly different take; the owner kept fp32). Suno's lossless WAV via sunoapi.org `/wav/generate` (~0.4 credits;
  needs the generation taskId, which the catalog doesn't store). A learned artifact-mask model in the style of
  Intrect's ArtifactNet (patent-pending), or reviving `D:\Projects_parked\SUNO_UPSCALE`; both need a clean
  real-music dataset.
- **E: is full** (about 9 GB free of 954 GB). `E:\deepPBR.io` is 859 GB and grows with every reconstruction job
  (`storageolatileeconstruct_*`); the deepPBR sessions were asked what is safe to prune. PLAiR's test renders
  live on D: (`D:\_audio_quality_scratch`).

- **DJ autonomy: the station knows what you're up to** (owner, 2 Oct; `docs/DJ_AUTONOMY.md`). A DJ mode where
  the hosts change the music themselves from the listener's activity (what they say, time and place, movement,
  later heart rate from a watch), and requests that keep steering the station instead of fading back after a few
  tracks. Owner's 2010 thesis; needs the native app for movement and wearables.
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

## Done on 1-2 Oct 2026 (crossfades)

- Crossfades are planned per pair of songs from both songs' loudness and lyric timing (`crossfade_plan.py`):
  1.5-5 s overlap from the outro fade and intro build, never over full-level vocals, equal-power fades with
  their own timing, and the ON AIR edge glow in the incoming song's colour while it runs. The plan used to reach
  the client only with the next state change, so most fades had been the generic 3 s one; it is now sent when it
  is made. Owner listened on air 2 Oct: works well. Details: `CLAUDE.md` section 6.

## Where things are kept

- Old Orpheus voice caches: `D:\tts_candidates\orpheus_clip_backup`. Engine candidates, bake-off WAVs and purged
  conversations: `D:\tts_candidates`.
- WSL crash dumps moved off C: on 30 Sep (another project filled the disk): `D:\wsl-crash-dumps`.
- Private test backend: port 8011 with a copy of `data/embeddings` and `TTS_SERVER_EXTERNAL=true` (CLAUDE.md).

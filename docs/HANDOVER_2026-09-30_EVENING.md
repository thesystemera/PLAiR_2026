# Handover: 30 Sep 2026, evening session

For the next session. Covers what changed today (commits `36e4a95` to `27c0391`, all on `main`, all pushed), what
was and wasn't proven, and the three things the owner wants next: an audit of today's changes, unit tests that
prove every DJ command works, and a pass for efficiency (token cost) and stability.

Read `CLAUDE.md` first. Related: `docs/HANDOVER_DJ_TOOL_PARITY.md` (front end vs DJ tools),
`docs/TALK_PACE_PROPOSAL.md` (now built), `docs/CITY_PULSE.md` (news, gathering, length).

## State when this was written

- The production backend is running code up to `1207d50`. **Not live yet:** `6a12037` and `ec51cae` (the DJs'
  search changes). The owner starts the backend with the desktop "PLAiR Start" shortcut
  (`external_components/plair_start.bat`); ask them to, or launch that shortcut through `explorer.exe` when they
  ask you to. Never start the backend window any other way: windows started from a session are invisible on
  their desktop.
- `data/logs` was emptied at 21:38 on the owner's request. Only look at logs from the latest run.
- The test account (`tests/.dj_test_account.json`, git-ignored) has 10 liked tracks for favourites tests.

## What changed today

| Area | What | Key files |
|---|---|---|
| News identity | A story is its publisher address. Feeds save at once (keyed by Google link, matched by remembered link or headline); a background worker decodes Google links and merges duplicates. Was storing ~20% of stories twice. | `news_store.py`, `news_links.py`, `external_news_service.py` |
| News summaries | Each linked story is read once: first 5 sentences, or the publisher's description. No LLM. | `news_reader.py` |
| News cards | One batched DeepSeek call per 12 stories: tags, people, place, category, tone, worth. Replaced the separate headline-only placing call. | `news_analysis.py` |
| Web gathering | One polite fetch layer: per-site pacing, back-off, rest on 429, robots.txt, daily caps, a consistent rotating browser visitor per site. MusicBrainz uses it too. | `services/web_fetch.py` |
| DeepSeek tools | For You runs its tool loop on DeepSeek (Gemini loop as fallback). | `ai_service.run_tool_turn`, `pulse_agent.py` |
| Length | No per-prompt length numbers. Studio clock in every DJ turn; one depth scale (brief / standard / detailed) on every segment tool; song intro time returned by play tools; hidden "at most 90 words" DeepSeek cap removed. | `talk_clock.py`, `context_nodes.py`, `dj_tools.py` |
| Pace | One speaking pace for the station, measured from each finished voice stream (chat, announcer, segment), saved in `talk_pace`. | `talk_clock.PaceMeter`, `tts_queue_manager.py` |
| Tool loop | Gemini re-emitted its earlier line and tool call after a result (a song restarted four times). Cause: thinking mode in the rounds after a tool result. Now thinking is for the first round only, plus a `[STUDIO]` marker after each result; a logged safety net drops identical repeat calls. | `ai_service.run_gemini_tool_turn`, `DJ_TOOL_FOLLOWUP_THINKING_BUDGET` |
| Search | `search_and_play` has the app's two modes (AI search by default, or one category) and `within` (catalog / favourites / super_likes); `pulse_search` takes `within` and `how_many`. | `dj_tools.py`, `dj_command_executor.py`, `listener_filters.py`, `catalog_vector_search_service.py` |
| Weather | `get_weather` takes several periods in one call. | `dj_tools.py`, `context_service.py` |
| Logging | Every script (announcer, segment, talk break) is written to `dj_turns.jsonl` with its prompt, response and word count; one log line each with target and words written; DJ turn line ends with "said N words". | `dj_prompt_service.py`, `conversation_service.py` |
| Start / stop | One start script; it stops an older backend by command line (window included) and refuses to start a second. `restart_all.bat` deleted. | `external_components/plair_start.bat`, `plair_stop.ps1` |

## What was proven, and what wasn't

Proven on a private backend or by replay:
- News: 88 feed items -> 78 stories, second fetch needs 0 lookups, merge keeps pulls and the aired ledger.
- Segment depths on air: detailed 87 s, brief 18 s, standard weather 36 s.
- Echo fix: 0 repeats in 12 replays (7 of 10 before); replay test kept as `tests/dj_tool_loop_test.py`.
- Favourites scope and AI search, signed in as the test account.
- `plair_stop.ps1` against a live backend.

Not proven:
- **The announcer's length.** One announcement ran 32 s in a ~9 s gap. In isolation DeepSeek obeys the window.
  The new log line (`announcement | window ... | wrote N words`) and the saved prompt in `dj_turns.jsonl` will
  show what happened; nothing has been read yet. Suspect: the time constraint sits mid-prompt, before the
  talking-points menu and context.
- Radio Mode talk breaks and the announcer at the new pace (2.0 words/s instead of 3.0): not run on air.
- The exact-title queue change and the "tool call limit" refusal message: not exercised.
- Favourites search against the owner's 324 likes (only the 10-like test account was used).
- The pace meter inside a long-running backend (it loaded saved values at boot; no save line seen yet).

## Audit of today's changes (requested)

Go through each row of the table above and check it against the running system, from the latest logs only:

1. **Dead code and leftovers.** Run `scripts/check-quality.ps1`. Look for settings that no longer have a reader
   (`NEWS_REPORT_DEPTHS` and `SEGMENT_DEPTHS` share labels: confirm they can't drift), and for anything today's
   changes replaced but left behind.
2. **Prompt weight.** Today added text to prompts: the studio clock block with its length rules, ids on City
   Pulse lines, `within` / `depth` / `how_many` parameters on tools, a longer `search_and_play` description.
   Measure the DJ system prompt and a typical user message before and after (token counts from
   `ai_usage_events`), and trim what doesn't earn its place.
3. **Behaviour that changed for everyone.** Thinking is now off after the first tool round: watch for narrated
   tool calls ("I will call ...") in multi-step turns. Radio Mode and the announcer ask for about a third fewer
   words.
4. **Each new log line** actually appears and is correct.
5. **Docs match code**: `CLAUDE.md` section 5, `docs/CITY_PULSE.md` section 9 and "Gathering from the web".

## Unit tests for every DJ command (requested)

Today nearly all testing was live turns against a private backend: slow (a restart is ~3 minutes) and
non-deterministic. The aim is a fast offline suite that proves each command, with live turns kept for a final
check.

What can be tested with no backend and no LLM:

- **Argument handling**: `dj_tools.normalize_tool_args` for every tool (defaults, enums, bad input, the
  injected `depth`, `within`, `how_many` caps, weather period lists).
- **Limits**: `dj_tools.authorize_tool_call` (guests, call cap, one save per turn, one segment per turn,
  favourites need a signed-in listener).
- **Display**: `dj_tools.command_string` for every tool.
- **The tool loop**: `tests/dj_tool_loop_test.py` stubs `llm_router.gemini_generate_chain` and replays model
  output through `run_gemini_tool_turn`. Extend it: empty replies and recovery, the review step, MAX_TOKENS,
  the last round, `_done_with` release, parallel calls.
- **Length maths**: `talk_clock.length_line`, `words_for`, `spoken_words`, the pace meter (samples, bounds,
  minimum samples, save and load).
- **News identity**: `news_links.clean` / `identity` / `google_id`, `news_store.title_key` / `item_key`,
  `news_reader.summarize` (caption lines, short bodies, description fallback).

What needs a stubbed service layer (the next step up): each `DJToolRuntime` handler run against fake
`playback_service`, `vector_search_service`, `pulse` and `news_service` objects, asserting the executor was
called with the right arguments and the result has the documented shape. One test per row of `TOOL_REGISTRY`,
so a tool can't be added without a test.

Suggested shape: plain scripts under `tests/` like the existing ones, or pytest if the owner agrees to add it
(`server/requirements-dev.txt`); add the run to `scripts/check-quality.ps1` so it gates every batch of edits.

Live checks stay for what only a real model shows: `tests/dj_pulse_test.py` (guest and `--signed-in`),
`tests/for_you_probe.py`, and a segment of each depth.

## Efficiency and token cost (requested)

Measured today: a DJ call is ~7,900 input tokens, 87% cached, about $0.0012; background and announcer calls are
100% DeepSeek with no errors in the last day. Things worth doing:

- **Rounds per turn.** Most tool turns take 2 model rounds; the review step adds a third when the hosts sign
  off "partial". Count rounds per turn over a day and see which third rounds added nothing.
- **The `(75 chars)` annotations** in the example dialogue are copied into replies (wasted output tokens). They
  may teach overlap timing; decide with the owner before touching them.
- **Guest padding** ("Unknown Location", "None (Guest)") in the user message.
- **City Pulse noise**: weather and area items carry unrelated "linked" news; two takes of one song list as
  identical lines.
- **Fillers**: every turn plays a ~3 s filler before the reply; check it is still wanted now that replies are
  shorter.
- **News**: links and summaries cost no LLM; the card call is ~1 cent per few hundred stories. Watch
  `[WEB] ... rate limited` lines for Google.

## Stability

- **Google link lookups** got the IP rate-limited once (110 lookups in under a minute). The worker is now
  paced and rests on 429; keep an eye on it.
- **RNZ** blocks page reads (403): its stories have no summary.
- **`local_nuggets` view** went missing on a running backend between 14:02 and the restart; cause not found,
  gone after restart. If it recurs, find what dropped it.
- **Two backends** must never share the Annoy files: private tests use a copy of `data/embeddings` and
  `TTS_SERVER_EXTERNAL=true` on port 8011.
- **Thinking off after a tool result** is the one change with a known risk (narrated calls); it has a setting
  (`DJ_TOOL_FOLLOWUP_THINKING_BUDGET`) if it needs reverting.

## How the owner wants this done

Short answers. Check what the system already does before proposing anything; extend it with one variable rather
than adding machinery. Find causes from logs and raw model output, not guards. Only read the latest run's logs.
One thing at a time. The owner starts the backend.

# Audit: DJ voice, prompts and UI (2026-09-30)

Read-only audit, no code changed. Three comparisons:

- **Old player**: commit `33637ce`, the last commit before the "PLAiR revival" `6c3f657` (2026-09-28). This is the February–April code the owner remembers.
- **Current PLAiR** (`HEAD`).
- **LifeSpan** (`E:\LifeSpan`), the owner's newer project built on the same ideas.

Nine agent reports went into this. Items already fixed today are not repeated: the phonetic paralanguage prompt, the filler caps, breaths, the tildes, the paralanguage examples, and the Chatterbox switch. Paths are `server/services_radio/` unless given in full.

**Fixed on 2026-09-30:** B1–B10 and O1 (see the commit after this report). The rest is open.

## 1. Clear bugs (fix)

| # | Bug | Where | Effect on air |
|---|---|---|---|
| B1 | `keep_valid_tags` gained `flags=re.DOTALL` (6cd3170, no reason given). | `dj_prompt_helper_service.py:268` | Any stray bracket (`[pause]`, `[Jess]`, `[LEO, laughing]`) deletes everything up to the next `[`, across lines. **Spoken words vanish silently.** The old regex stopped at the line end. |
| B2 | Escaped-quote cleanup uses `r"\+(['\"])"` (matches a literal `+`) instead of `r"\\+(['\"])"`. My bug from today, verified. | `dj_prompt_helper_service.py:276` | `\"Karma Police\"` still reaches the voice with its backslashes. |
| B3 | Unreadable cached clip: only `OSError` is caught; a decode error drops the segment, keeps the DB row and doesn't fall back to generating. | `tts_generation_service.py` `load_clip` | A corrupt clip keeps winning and its line is lost every time. LifeSpan deletes the row and retries the next match (`orchestrator_service.py:247-311`). |
| B4 | The shotgun cooldown marks `results[0]` inside the query, even if it's below the threshold, its file is missing, or a different clip ends up playing. | `tts_vector_db_service.py:469-470` | Clips go on cooldown unheard (so more re-renders), while played clips can escape the cooldown. LifeSpan marks only the clip actually played. |
| B5 | "Random among equally good takes" uses a tolerance of `1e-10`. | `tts_vector_db_service.py:457-467` | Near-identical titles (differences around 1e-7) never share turns, so the same take wins until its cooldown. LifeSpan uses 0.01. |
| B6 | Punctuation is stripped by `encode('ascii','ignore')` before being converted. | `dj_prompt_helper_service.py:188` | "that’s" → "thats", "Hey—what" → "Heywhat", "Beyoncé" → "Beyonc"; °, €, £ vanish. LifeSpan converts first (`text_utils.py:21-24,119-123`). |
| B7 | A failed DJ turn (exception, safety block) is silent. | `ai_service.py:447-449`, `dj_prompt_service.py:108-121`, `conversation_service.py:626-628` | The listener asks and hears nothing. LifeSpan speaks an in-character line chosen by the reason (`chat_service.py:927-943`). |
| B8 | The client strips markup with `/~.*?~\|%.*?%\|@.*?@\|&.*?&/`. | `client/src/components/Conversation.jsx:48,340,729` | "R&B and Drum & Bass" displays as "R Bass"; "50% … 20%" loses the text between. The server already has the exact token pattern (`MARKUP_TOKEN_PATTERN`). |
| B9 | The persona counter resets even when the update failed. | `persona_service.py:186-193` | A failed persona update waits 5 more engagements instead of retrying. |
| B10 | The circuit breaker counts 400/422 request errors, and `LLM_DJ` has no timeout of its own, so it uses the 45 s background one. | `llm_router.py:53-54,244-246` | One bad request can take a healthy model offline for 5 min; a hung DJ round waits 45 s. |

## 2. Lost or changed since the old player (owner's call)

| # | Old player | Now | On air | Origin |
|---|---|---|---|---|
| O1 | **Only `[BROADCAST]` was spoken**; `[TXT]` stayed chat text (old `conversation_service.py:358-383`). | Everything is spoken (`_speak_dj_text`, `:471-489`). | The personal text-back channel is gone: private replies air, and the `format_channels` rules no longer match the code. | 6c3f657, no reason given |
| O2 | **`&N&` lines went through the effects chain twice** (once on load, again in the queue manager when mix > 0). | Once. | At `&0.3&` a line was ~10 dB below a close line; now ~4 dB. **Proximity/3D is about half as strong.** | 6c3f657, silent |
| O3 | **`%sfx%` beds at −80 dB**, effectively silent (old `tts_broadcast_service.py:38,103`). | Levelled to −30 dBFS, audible. | Studio sound beds that never played before now play under the hosts. | 6c3f657, silent |
| O4 | Announcer and segments on Gemini 2.5-flash-lite at 0.9, no length cap. | DeepSeek first (`LLM_ANNOUNCE`/`LLM_INTERPRET`), with **"at most 90 spoken words"** added whenever DeepSeek writes (`dj_prompt_service.py:30-36`). | A different writer for between-track talk and every segment. Bio, lyrics and news are cut to ~30 s, which conflicts with the announcer's own seconds×3 word target. | 6c3f657, for cost; the cap is undocumented |
| O5 | Every segment written fresh. | Finished scripts cached: bio/lyrics 7 days (persisted), news 20 min, weather 1 h, for non-personalised contexts. | Guests hear the same script word for word. This goes against "keep randomness alive". | 6c3f657 |
| O6 | The interactive DJ was one call to 2.5-flash-lite, max 1000 tokens. | 2.5-flash with thinking, 8192 tokens, tool loop, `[TASK]` review, a large tool-rules block in the system prompt. | Longer, more factual replies; possibly less banter. Worth an A/B listen. | Deliberate (measured) |
| O7 | "Acknowledge briefly… END DIALOGUE IMMEDIATELY" and the produced segment then aired. | Hosts answer facts inline, and this node is present on every turn. | Fewer produced segments, more fact talk in replies. | 3cddb7d |
| O8 | "Add natural stuttering… (li-like thiss)". | "The odd stutter… keep it rare". | Cleaner, less raw hosts. The toning down was done for Orpheus; Chatterbox may handle stutters better. | ae9f076 |
| O9 | Chat bubbles of a reply appeared one by one with random 0.2–0.8 s gaps and a sound each; messages entered with a random "scatter pop" (scale, x, y, rotation, spring). | All at once with one sound; one fixed slide animation. | Less alive. The randomness was dropped with the tool timeline and the motion system. | 102b4b7, 6c3f657 |
| O10 | Announcer always the hosts. | Stings, station IDs and time checks replace some breaks **outside Radio Mode too** (`STINGS_OUTSIDE_RADIO_MODE=true`); every break also gets a TALKING POINTS menu (city/stats). | Links drift from the music toward city info. | 6c3f657 / df9dacc |
| O11 | The hosts' own history was plain context. | It's wrapped as `<<UNTRUSTED_DATA>>`. | May weaken callbacks and running jokes. Unmeasured. | cff5424 / security |
| O12 | Empty segment data: the LLM improvised. | Two fixed canned scripts. | The same "Pirate radio, baby" lines repeat. | 6c3f657 |
| O13 | `user_basic` gave the city. | Adds a street-level description, "fine to mention the street casually". | Hosts may name a listener's street. | 6c3f657 |
| O14 | Music bus master gain 1.122. | 1.0. | Everything about 1 dB quieter; no reason given. | 6c3f657 |

Deliberate improvements confirmed as fine: the planner, blend maths and effects chain are otherwise identical; host overlap rules are unchanged; ducking holds between streams; there's a fade on cancel; per-listener cooldown; track progress is fixed; guests can use segments.

## 3. Better in LifeSpan (worth porting)

| # | What | LifeSpan | Why | Effort |
|---|---|---|---|---|
| L1 | **Paralanguage emojis**: each clip stores an emoji (DB column + file tag). `render_paralanguage_emojis` swaps `~tag~` for the emoji of the nearest clip in displayed text. | `vector_service.py:17,110-147,313-331`, `tts_service.py:303-336`, `scripts/seed_paralanguage_emojis.py` | Reactions become visible in chat, activity cards and pop-ups. See the design notes below. | M |
| L2 | **Text normalisation before TTS**: URLs, °C, units, currency, %, abbreviations ("feat.", "St.", "Dr"), `&` → "and". Applied to the voice text only; the cache title stays raw. | `tts_normalizer.py:5-123` | A radio DJ reads news, weather and events full of these. PLAiR has nothing, and `$` and `%` are also markup delimiters. | M |
| L3 | **Every audible line has text**: fillers, impulses and preludes appear in the chat. | `orchestrator_service.py:674-688`, `useChat.js:646-679` | Transparency. PLAiR's fillers play without text. | S |
| L4 | **The model is told which filler lines already aired** this turn. | `chat_service.py:361-364` | Stops the DJ repeating or contradicting the "Oh nice!" that just played. | S–M |
| L5 | **The router sees recent turns**, not one sentence; its cache key includes context plus a lexical guard; near-misses are logged. | `chat_history.py:5-17`, `brain_context.py:267-289` | "Yes do that", "what about tomorrow?" are routed blind today. | M |
| L6 | **Richer clip metadata**: description (phonetic prompt/line), emoji, domain, sequence; a full rebuild from file tags. | `vector_service.py:127-147`, `tools/tts_cache_management.py` | Enables L1 and better logging. PLAiR never reads TIT3 back, and re-indexing rejects titles with `...`. | S–M |
| L7 | **Stateful limiter and reverb**: one Pedalboard per stream and a stateful peak limiter. | `spatial_audio_service.py:133-173` | PLAiR builds a new reverb every 200 ms slice and renormalises per slice (pumping). `limit_peaks` compares RMS, so it rarely fires, and overlaps can clip. A/B before changing (it's audible). | M |
| L8 | **Anti-repeat hint** (most common cached outputs sent to the generator) and caching of "SILENCE" declines. | `vector_service.py:333-360`, `brain_service.py:226-235` | Fillers stay varied instead of converging. | S |
| L9 | **Prelude breath** at mic release, and **interlude** fillers after 1 s of silence while the LLM or tools work. | `orchestrator_service.py:332-369,736-738` | Fills dead air: the gap before the first impulse, and long thinking rounds. | S / M |
| L10 | **Per-round timing, tokens and thought summaries** in the turn log; the matched clip title in cache hit lines. | `llm_service.py:532`, `chat_tool_loop.py:562,586`, `vector_service.py:283-304` | Root-causing (the owner's rule). PLAiR collects `round_usage` but never writes it. | S |
| L11 | **History hygiene**: the spoken and display copies are stored separately; tool results replay as labelled summaries. | `chat_service.py:386-410` | PLAiR replays `[TASK]` and sometimes a duplicated script (2 of 33 turns). | M |
| L12 | Mic audio streamed while speaking (VAD, live draft transcript). | `useVoiceStream.js:376-396`, `orchestrator_service.py:700-770` | Removes the upload plus transcribe wait. Large job. | L |
| L13 | Fallback model mid-turn rebuilds thought signatures. | `chat_tool_loop.py:226-227,440-464` | The DJ fallback may fail exactly when needed (unverified). | S |

**Not worth porting:**
- **Interject while the listener talks:** the DJ would bleed into the mic.
- **Streaming the reply sentence by sentence:** conflicts with the planner; already rejected.
- **LifeSpan's simpler markup:** PLAiR's two-host planner is richer.

**PLAiR is already better at:**
- The two-host planner and the deadline-ranked TTS scheduler.
- The batched engine.
- Exact-line refresh.
- Gemini cache renewal.
- Persistent cost tracking.
- Untrusted-data wrapping.
- Script validation.
- The player watchdogs.
- Speaker-coloured shader glow.

## 4. Paralanguage emojis: design notes

1. **Keep the owner's phonetic prompt word for word.** Get the emoji from a separate small call per new tag title (or a batch backfill like LifeSpan's seeding script), not by changing that prompt.
2. **Store the emoji with the clip.** Add an `emoji` column on `meta_embeddings` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`) and a `TXXX:EMOJI` ID3 frame, which re-indexing reads back.
3. **Look it up.** `paralanguage_emoji(tag)` returns the nearest paralanguage row (no threshold, voice or cooldown), cached per tag.
4. **Apply it only at display boundaries, never to TTS input or stored text:**
   - the `dj_activity` "say" events;
   - `conversation_update`;
   - the announcer broadcast;
   - `GET /api/conversation`.

   `Conversation.jsx` then shows `~laughs~` as 😂 inside the host's bubble, after the B8 fix.
5. **Optional: a timed pop-up.** Attach the cue to the chunk timeline in `LiveStreamEncoder`, so the emoji pops when the laugh actually plays (the same pattern as the speaker colour schedule).

## 5. Also worth deciding

- **Clip-match thresholds:** re-tune them for the mpnet encoder. Meta and impulse at 0.75, sound effects at 0.5 and breaths at 0.65 were set for flan-T5, which scored almost everything as similar. Sound effects are cache-only, so a miss is silent. Measure hit rates first.
- **The `(NN chars)` notes in the dialogue example:** the model copies them into its replies, and `remove_char_counts` then deletes every bracketed phrase, including real speech. This is the original design for counting @X@, so it's the owner's call. The option is to explain the counting separately and keep the example as pure output.
- **The Producer:** it only sees the current sentence (see L5).

# Adaptive talk pace

Status: built 30 Sep 2026 (`talk_clock.PaceMeter`, table `talk_pace`, sampled in `TTSQueueManager`). Two kinds are
measured, `announcer` and `segment` (chat replies carry no word targets, so they aren't tracked). Stings, shoutout
segments (they contain listener audio) and chat replies are skipped; a station-ID lead-in is subtracted. The rest
of this note is the original proposal.

## What exists today

One value says how fast the hosts talk: `TALK_WORDS_PER_SECOND` (2.0), read through `talk_clock.pace()` and
`talk_clock.words_for(seconds)`. Everything that gives the hosts a time to fill uses it:

| Where | What it sizes |
|---|---|
| DJ-triggered segments (news, weather, events, places, biography, lyrics, shoutouts) | word ceiling for brief / standard / detailed |
| Radio Mode talk breaks | word range for each segment's seconds |
| Between-track announcer | word target for the gap between two songs |

The value was measured by hand on eight aired segments: 1.5 to 2.7 words a second, about 2.0 on average. It is
low because reactions (`~laughs~`), studio sounds and overlaps take air time that the word count doesn't see, and
the new voice engine currently holds them long.

## The idea

Let the station measure its own pace. Every voice stream already ends with the two numbers needed:

- how many words the script had (`spoken_text(script)`, already used by Radio Mode);
- how many seconds went to air (`encoder.fed_seconds`, already logged as "12.5s audio" in `TTSQueueManager`).

So each finished stream gives one sample: `words / seconds`. No audio analysis, no extra model call, no timer: one
division at a point the code already reaches.

## How it would work

1. **Sample** at the end of a stream in `TTSQueueManager`, only when the stream completed (not cancelled) and the
   script had enough words to be meaningful (say 25+). Stings, the talking clock and cache-only clips are skipped.
2. **Smooth** with a running average (each new sample moves the value a few percent), kept per kind of talk:
   `chat`, `announcer`, `segment`. They pace differently: a chat reply is mostly reactions, a bulletin is mostly
   words. Until a kind has enough samples (say 10) it uses the setting.
3. **Bound** the result (for example 1.2 to 3.5 words a second) so one odd stream can't swing it.
4. **Keep** the three numbers in memory and write them to one small Postgres row every few minutes and at
   shutdown, so a restart starts from what was learned.
5. **Read** through the same `talk_clock.pace(kind)`. Callers don't change beyond naming their kind.

`TALK_WORDS_PER_SECOND` stays as the starting value and the fallback. `TALK_PACE_ADAPTIVE=false` turns the
learning off.

## Cost

One division and one multiply per finished stream; one tiny database write every few minutes. Nothing runs per
second, per chunk or per sentence.

## What it buys

- While the engine's paralanguage runs long, segment lengths stay honest without hand-tuning.
- When the engine is fixed, the number rises by itself over the next few dozen streams.
- A per-kind pace fixes the one mismatch seen in testing: chat replies and bulletins don't talk at the same speed.

## Things to decide

- **Per host?** Leo and Jess may pace differently, but scripts mix both, so per kind is the practical unit.
- **Cached clips.** A stream built mostly from cached clips still measures real air time, so it counts.
- **Visibility.** One line in the hourly usage log ("pace: chat 1.8, announcer 2.1, segment 2.0 words/s") so
  drift is visible without a dashboard.

## Size

About 60 lines: a small `PaceMeter` in `talk_clock.py`, one call in `TTSQueueManager`, one table row, two
settings.

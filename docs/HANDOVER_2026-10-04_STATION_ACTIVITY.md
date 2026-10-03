# Handover 2026-10-04: let the station use its spaces like a DJ

## The owner's picture (agreed 2026-10-04)

Every song has spaces the station already knows about: lyric-free quiet stretches inside the song (often around two
thirds in) and the crossfade gap into the next song, each with a length. A DJ works those spaces: the hosts take the
change between songs when they have something to say; the computer voice (time check, station ID, city line, or just
a sting) takes any space that fits, mid-song included, and picks what to say by how long the space is. The hosts and
the computer can go back to back; the owner likes that. The only sense of restraint is "has the station said anything
lately", so it doesn't clutter. No stacks of timers, probability rolls and special cases. The listener can opt out.

## Where it is today (the rule stack to replace)

Read `CLAUDE.md` sections 12 and 15. Code: `announcer_service.py` (`on_playback_state_update`,
`_analyze_transition`, `_get_quiet_segments`, `_lyric_gaps`, `_schedule_announcement_for_transition`,
`_schedule_sting_for_transition`, `_fill_now`, `_schedule_midtrack_sting`, `_schedule_review_sting`),
`sting_service.py` (`plan_between_tracks`, `plan_midtrack`, `_gate`, `play`, `build`), `sting_schedule.py`,
`sting_types.py` (each kind's `min_window_s`), `radio_mode_service.blocks_announcer`.

- Everything is decided the moment a song starts. The "who's busy" gate (`sting_service._gate`: hosts speaking,
  conversation in the last 30 s, Radio Mode break lined up or aired in the last 90 s) is read then, usually while the
  previous announcement or a talk break is still airing, and the verdict stands for the whole song.
- Mid-song computer drops: Radio Mode only, at most every 20 min (`STINGS_MIDTRACK_MIN_INTERVAL_S`), a 35 % roll
  (`STINGS_MIDTRACK_PROBABILITY`), none within 120 s of a sting, a quiet stretch of 4 s or more in 20-80 % of the
  song, and the review sting goes first.
- Between songs: a separate rule set (short gap or every 4th change, 10-15 min since the last sting, time checks every
  15-30 min), the hosts' turn otherwise, and a fill only when the hosts' line fails outright.
- Air-time drops log on the `announcer` category (off), so they vanish without a trace.

Evidence, owner listening 2026-10-03 23:24 to 00:37 (about 75 min): 7 hosts' lines between songs, 3 talk breaks,
1 computer event (a musical hit). Eight time checks were planned and none aired; 18 of 43 song-change plans were held
by "busy" conditions read at song start that were long gone when the change came.

## Shape of the rebuild (check with the owner before coding)

1. **One map per song.** When a song starts, list its spaces from what is already computed: the transition window
   (`_analyze_transition`) and the mid-song quiet stretches (`_get_quiet_segments` + `_lyric_gaps` over the whole
   song), each with start and length.
2. **Fill each space when it arrives**, a moment before it, from the live state, in one place. The hosts get first
   claim on the change between songs (and keep their own LLM lead time); otherwise the computer takes any space that
   fits, choosing what fits the length (each `sting_types` kind already declares its `min_window_s`; a time check if
   one is due, else an ID or city line, else a musical sting). Radio Mode breaks keep their slot. Nothing airs over the
   hosts or a talk break, and a manual skip cancels the song's remaining spaces.
3. **One knob for restraint:** time since the station last aired anything (hosts, computer, break), e.g.
   `STATION_QUIET_TARGET_S` around 180. A space is used when the station has been quiet long enough, and always at a
   natural song change. This replaces the min gaps, rotation, probability and midtrack interval settings; keep only
   the time-check spacing so the clock isn't read out every few minutes.
4. **Listeners outside Radio Mode** get the computer too (stings already do between songs); the stings preference
   stays the opt-out.
5. **One log line per space** on the playback category: `space at 2:14 (5.1 s) -> time check` or `-> nothing (why)`.
6. Test with a listening session of songs that end on their own (no skips): every change and a fair share of the
   quiet mid-song stretches should carry something, never on top of the hosts.

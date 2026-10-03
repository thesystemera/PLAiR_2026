# Handover 2026-10-04: songs pass with nothing from the station

Owner's ask: between any two songs (that end on their own) something should air: the hosts, a talk break, or at
least the computer voice (time check, ID, city line). Mid-song quiet stretches (the lyric-free part two-thirds in)
should also carry the odd computer drop. The listener can opt out; by default the station should feel alive. All the
systems are meant to work together: if the hosts don't take a gap, another one does.

Read `CLAUDE.md` sections 12 (stings, drops) and 15 (Radio Mode) first. Code: `announcer_service.py`
(`on_playback_state_update`, `_schedule_announcement_for_transition`, `_schedule_sting_for_transition`,
`_fill_now`, `_schedule_midtrack_sting`), `sting_service.py` (`plan_between_tracks`, `plan_midtrack`, `_gate`, `play`),
`sting_schedule.py` (`decide_between_tracks`, `decide_midtrack`), `radio_mode_service.blocks_announcer`.

## Evidence (owner listening, 2026-10-03 23:24 to 2026-10-04 00:37, about 75 minutes)

Aired: 7 hosts' lines between songs, 3 talk breaks, **1** computer/sting (a musical hit). Eight time checks were
planned; **none aired**. The 43 song-change plans (`plan for the next song change` lines, playback category):

| outcome | count |
|---|---|
| hosts' turn, held by min_gap (10-15 min since the last sting) | 8 |
| time check planned | 8 |
| hosts' turn (announcer_turn) | 6 |
| held: "a Radio Mode break holds it" | 8 |
| held: "the hosts are speaking" / "mid-conversation" | 10 |
| other (window too short, ID, musical) | 3 |

## Findings

1. **Decided at the wrong moment.** Every song change is planned the moment the song *starts*
   (`on_playback_state_update` -> pair changed). The gate (`sting_service._gate`: hosts speaking, conversation within
   30 s, Radio Mode break lined up or aired in the last 90 s) is read at that moment, when the previous song's
   announcement or a talk break is usually still airing. Minutes later, at the actual change, none of it is true, but
   the decision stands. 18 of 43 plans were held by conditions that only existed at song start. `plan_midtrack` has
   the same flaw, so mid-song drops are gated at song start too.
2. **Rate limits stack up.** Between songs: a sting only on a short gap (<6 s) or every 4th break, and never within
   10-15 min of the last one (`STINGS_MIN_GAP_S`/`MAX_GAP_S`). Time checks every 15-30 min. Mid-song drops: Radio
   Mode only, at most every 20 min (`STINGS_MIDTRACK_MIN_INTERVAL_S`), 35 % chance, need 4 s lyric-free quiet in
   20-80 % of the song, and none within 120 s of another sting. That ceiling is a few computer events an hour.
3. **"The hosts' turn" has no safety net.** The fill (`_fill_now`) runs only when the hosts' line fails (timeout,
   empty, `[N/A]`, error) or there is no quiet window. When the hosts are skipped for another reason (Radio Mode hold
   at the trigger, window already passed, the turn decided at song start) nothing airs.
4. **Air-time drops are invisible.** `sting_service.play` logs "dropped at air time (gate closed)" in the `announcer`
   category, which is off. The time check planned at 23:47 for the 23:51 change vanished without a trace.
5. Skips cancel the plan by design (a manual skip gets nothing; the front end has its record crackle). The owner
   skips a lot, which hides how rarely a natural change gets something. Judge only changes where a song ended on
   its own.

## Suggested shape (agree with the owner first)

- Plan at song start only *where* the gap is (the analysis is fine); **decide what airs just before the gap** with
  the live state, in one place: Radio Mode break if due > hosts' line if eligible and the gap fits > computer voice
  (time check if due, else ID or city line) > musical sting. A natural song change always gets one of them unless the
  listener opted out or something already aired across that change.
- One station-wide "last thing aired" clock instead of separate min gaps: if nothing aired for N minutes (owner's
  feel: about 3), the next lyric-free mid-song gap of 4 s or more gets a computer drop (time check or ID). Retire the
  35 % roll and the 20-minute floor, and include listeners outside Radio Mode, as stings already do between songs.
- Log every air-time outcome on the playback category: `song change -> <what aired>` or `-> nothing (<why>)`.
- Test with a listening session of natural song ends (no skips): every change should get something.

Settings involved: `STINGS_MIN_GAP_S`, `STINGS_MAX_GAP_S`, `STINGS_ROTATION_N`, `STINGS_SHORT_WINDOW_S`,
`STINGS_CONVERSATION_QUIET_S`, `RADIO_ANNOUNCER_QUIET_AFTER_S`, `STINGS_TIME_CHECK_MIN/MAX_INTERVAL_S`,
`STINGS_MIDTRACK_*`, `STINGS_FILL_GAPS`.

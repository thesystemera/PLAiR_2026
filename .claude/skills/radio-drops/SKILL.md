---
name: radio-drops
description: Make, pick and install PLAiR's radio drops (the short sounds as the hosts or the computer voice cut in and out of the talk). Use when the owner wants more or different drops, blips, stingers or in/out sounds, or wants to pick or install a batch.
---

The tool is `server/utils/generate_radio_drops.py`; its docstring has every option. A batch is a folder under
`BLIPS_DIR/candidates/<batch>` (`D:\catalog\blips\candidates`). Name batches by date (`2026-10-03`, add `b` for a
second batch that day).

1. **Make the batch** (one DeepSeek call, then about 27 s per take on the P6000):
   `E:/AI_RADIO/.venv/Scripts/python.exe server/utils/generate_radio_drops.py batch <batch> [count] [takes]`
   Default is 24 prompts x 1 take (about 11 minutes). Run it in the background and tell the owner the call count
   first (CLAUDE.md, "Go easy on paid LLM calls"). The prompt step reads the Keep/Skip verdicts of earlier batches
   (`picks.json`) and steers DeepSeek toward what was kept.
   Owner's lesson (3 Oct): words alone got single hits, then horror-movie noise, then music. Proper radio stings are
   sound design that's hard to prompt, so prefer **importing a real imaging library** when one is available:
   `generate_radio_drops.py import <batch> <folder> [hosts|computer]`, then `page <batch>`. Downloaded libraries live
   in `BLIPS_DIR/library/<source>` (Music Radio Creative's free pack is there: royalty free incl. radio imaging, no
   attribution; downloading needs the owner's OK).
2. **Publish the picker**: copy `<batch>/picker.html` and the `<batch>/sounds/` folder into the scratchpad and
   publish the page with the Artifact tool, `capabilities: {"db": {}}`, `root` = the scratchpad and `files` = every
   `sounds/<name>.mp3` (the viewer blocks audio embedded as data URLs; files published next to the page play), one
   artifact per batch. Picks save to its `picks` collection as the owner taps
   Keep/Skip and In/Out/Either. Check once with `ArtifactData` `list` on `picks` (empty is fine), then send the link.
3. **Install the keepers** when the owner says they're done: `ArtifactData` `list` the `picks` collection, write the
   documents' bodies as a JSON array to `<batch>/picks-export.json`, then run
   `generate_radio_drops.py install <batch> <batch>/picks-export.json`. It copies each kept drop into
   `BLIPS_DIR/hosts|station/in|out/` (Either goes to both) and saves `picks.json` in the batch for the next round.
   Installing is destructive levelling: each drop is written at `BLIPS_TARGET_LUFS` (-25, integrated) with a
   `BLIPS_PEAK_DBFS` (-12) ceiling; the station plays the files as they are. After changing either setting or
   dropping files in by hand, run `generate_radio_drops.py level` (re-levels every installed drop in place).
4. **Make it live**: restart PLAiR (PLAiR Start shortcut, CLAUDE.md) so `station_blips` reloads the library, then check
   the boot log line `Blips: station in N, ...` and one chat reply in `radio.log`.

How the drops air (`services_radio/station_blips.py`, `tts_live_stream.LiveStreamEncoder`): every hosts' stream except
impulse and interlude, and every computer-voice sting, opens with a random "in" drop and closes with an "out" drop. The
voice comes in where the in-drop has decayed `BLIPS_RELEASE_DB` below its peak, with the line's silent lead tucked under
the drop, and the out-drop's peak lands where the last word ends. Drops are peak-levelled to `BLIPS_PEAK_DBFS`. Remove a
drop by deleting its file from `BLIPS_DIR/<voice>/<edge>/` and restarting.

Rules: keep the model on the P6000 (never the RTX 6000). The candidates folder holds every batch with its prompts,
manifest and picks; never delete it, since earlier verdicts steer the next prompts.

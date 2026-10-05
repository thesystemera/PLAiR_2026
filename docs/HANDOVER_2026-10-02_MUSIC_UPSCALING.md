# Handover: music upscaling chain (2-3 Oct 2026)

## How to work with the owner on this

- **Proof before words:** run `scripts/music_lab/proof.py ORIGINAL t0 RESULT t0 dur outprefix LABEL` on every render (spectrogram, change map, loudness, band changes level-matched, % of 1/3-octave cells cut, HF flatness) and look at the images before sending.
- Short answers, results not plans, no running commentary. One task at a time.
- Send loudness-matched files with short names; FLAC (16-bit) so they fit the 30 MB phone limit. Give a raw Suno version next to every render.
- Comparisons the owner liked: **beep cycles** (sections switch every 4 s, low/mid/high beep marks A/B/C) and **difference files** (B minus A, boosted, gain in the filename).
- Render on the **P6000** (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES=0`; since 5 Oct the RTX 6000 is the owner's DeepPBR card), one model at a time, full songs. Profile a new model on 5 s first.
- Test environment, open problems and next leads: `docs/HANDOVER_2026-10-06_SUNO_ARTIFACTS.md`.

## Locked chain (master chain version 4, 5 Oct)

Suno MP3 -> decode -> **Apollo** on the decoded MP3 (what it was trained on), keeping Suno's own signal below its MP3 cutoff (`audio_headroom.keep_source_below_cutoff`: Apollo only fills above it; on the full mix it had pulled 16.5-17.5 kHz down 4-5 dB) -> RoFormer split of the restored mix -> Lew's vocal Apollo on the vocal (no crossover) -> remix -> notches (`correct_audio`: 25 Hz rumble cut, stationary resonances only, 20 kHz safety roll-off) -> SonicMaster (33% wet, 20 steps, fp32, prompt "give the mix more shine and sparkle, with depth and separation between left and right, and let the audio breathe more and improve the dynamics", blend compensation on) -> **reference EQ + -80 dBFS tape hiss + final leveler** (lane 6) at **-16 LUFS** (Apple Music standard; true-peak -1.5 dBTP, limiter up to 6 dB). `SONIC_MASTER_PRECISION=auto` (default since 5 Oct): fp16 on the RTX (1.7x faster, a slightly different take), fp32 on the P6000. SonicMaster parks on the CPU between runs and tiles its VAE, so a render no longer spills out of a shared card (CLAUDE.md section 18).

What changed from version 3 (5 Oct, measured on Digital Dollhouse, Faded Signal, Static & Silhouette, Carbon Copy):
- Apollo moved first. It is trained on 32-128 kbps MP3 decodes; in version 3 it got a remix with an already restored vocal. On the spectrum the two orders are nearly identical (difference 23 dB under the music), v4 keeps about 0.7 dB more at 18 kHz.
- Apollo treated everything from about 16 kHz up as missing and re-generated it lower: 16.5-17.5 kHz lost 4-5 dB of Suno's real signal. The crossover keeps the source below the detected cutoff (17.2-18.0 kHz on Suno MP3s). Result: 16-20 kHz is 2-2.5 dB above version 3, 17 kHz back at Suno's own level.
- SonicMaster at 100% wet adds 7-10 dB at 10-16 kHz (the sparkle prompt) and cuts above 17.5 kHz (-21 dB at 19 kHz); its wet and dry are aligned to 1 sample but phase-unrelated above 2 kHz (coherence 0.00), so the blend is the only place in the chain that can sound phasey. Blend compensation adds 2-2.5 dB at 8-16 kHz. Both left as they are; the reference EQ sets the final balance.
- Tone moved from before SonicMaster to the end (lane 6) and now targets the modern-master average within ±3 dB (CLAUDE.md section 18). Version 3 masters sat 4-6 dB over it at 8-12 kHz; version 4 sits at the band edge. Trials: v4/v5 (Elowsson target, ±2 dB, 75% pull) sounded muddy, which is why the target changed.

Station level -16 LUFS everywhere: masters, uploads, shoutouts; stings -18, station voice -20, beds -22; DJ hosts `DJ_VOICE_LEVEL_DB` -2 dB to keep the voice/music balance. `utils/relevel_catalog.py` moves older masters to -16 by gain only.

Lanes: 1 decode, 1b Apollo, 2 separation, 3 vocals + remix + notches, 4 SonicMaster (`SONIC_MASTER_ENABLED`), 5 Whisper (skipped when lyric timings exist), 6 reference EQ + leveler + transcode. Every master gets `master_chain_version` + `master_rendered_at` in its metadata.

Master chain versions: 1 = Nov 2025 (Apollo first, Demucs, ClearVoice SE/SR chain, SonicMaster 50%, master -14 with the notch bug); 2 = the earlier 3 Oct renders (-14, 25%); 3 = 3 Oct night (Apollo after the remix, Elowsson tone before SonicMaster); 4 = above. Bump `MASTER_CHAIN_VERSION` whenever the sound changes.

Backlog: `server/utils/process_backlog.py [--rerender]` runs on the P6000 (`--gpu 0`) in its own process (log `data/logs/backlog.log`): Suno tracks without a master first, then (with `--rerender`) masters below the current version; super-likes, likes, then the rest; ~2.5 min per song (GPU-bound). The live backend picks finished songs up within a minute (`CATALOG_WATCH_INTERVAL_S`), and "Recent" sorts by `catalog_added_at`.

## Measured facts (3 Oct)

- Master tone target: Elowsson & Friberg (AES 2017, 12,345 pop masters) quadratic LTAS fit. The old pink target was ~7 dB (presence) / ~15 dB (air) too bright, so every song hit the +4 dB cap. Now: tolerance ±4 / ±6 dB, half the excess corrected, max ±3 dB; most songs get no tone EQ.
- Notch bug fixed: a notch needs ≥ 6 dB prominence in ≥ 90% of loud frames; real notes sit at 25-67% and are left alone.
- Limiter allowance 3 -> 6 dB: at 3 dB peaky songs were held at -17 LUFS. Finals are as dynamic as Suno (peak-to-loudness 11.9-12.4 dB vs 10.8-12.5; loudness range equal or +0.5 LU).
- SonicMaster regenerates the highs (no phase relation above ~2 kHz), so any partial wet mix loses power (25% plain = -2 dB); blend compensation restores it. At 100% it smooths codec flange but adds its own artifacts. The stereo prompt adds ~3.8 dB of side energy at 33%; mono fold-down -1.2 dB.
- 20 vs 50 steps: bands within 0.1 dB; the difference is 37 dB below the music but audible as artifact detail. Owner chose 20 for speed.
- Suno MP3s are ~175-180 kbps VBR, 48 kHz. Apollo (trained on 32-128 kbps) changes little at that rate (21-26 dB below the music) and mostly fills 18-22 kHz. Lew's Universal Apollo measured the same.

## Rejected (measured)

3-4 Oct additions: De-limiter (Jeon 2023; Suno barely hits a limiter, peak-to-loudness 14.3 -> 14.4 dB), a 2-8 kHz expander on the music stem (5.7 -> 7.6 dB depth on the stem, only +0.7 dB after Apollo + SonicMaster; inaudible), a low-band transient lift (no measurable change). Measured "squash" signature of Suno: 2-8 kHz drops only ~5 dB between hits vs 7-8.5 dB in the neighbouring bands; bass crest ~6.7 dB. Our chain-3 masters score DR 10-11 vs Suno's 8.1.

ClearVoice SE (gates), ClearVoice SR/cvsr (cuts 8-16 kHz on singing), the February ClearVoice chain (same), Smule Renaissance, BigVGAN, baicai vocal Apollo, HRAudioWizard, DTT-BSR (gates gaps/harmonies), BABE-2 (dull, 10 min per 6 s), rvq-artifact-remover (notchy DSP), AudioDelossifier 192k/256k (61 dB below the music = nothing), our own resonance suppressor (two versions, artifacts on quiet tails), AudioSR (cutoff replacement like cvsr). Suno's own Remaster/Advanced Split: owner won't use them.

## Open ideas

- Suno lossless WAV via sunoapi.org `/wav/generate` (~0.4 credits; needs the generation taskId, which the catalog does not store; store it on new generations). Unproven whether it is a true lossless decode.
- Intrect-style artifact mask: train a small U-Net (0.5·sigmoid mask) on the 4-stem Demucs residual (mix minus stems) plus codec-degraded pairs; ~1-2 days on the P6000; method is patent-pending. Owner's parked CycleGAN project is in `D:\Projects_parked\SUNO_UPSCALE` (blocked on a clean real-music dataset).

## Not done

- Catalog re-render (~2,000 songs) to chain version 4: `server/utils/process_backlog.py --rerender` (owner decides when and on which GPU).

## Files

- Older A/B renders and experiment scripts were cleaned up on 5 Oct; the current test environment is listed in `docs/HANDOVER_2026-10-06_SUNO_ARTIFACTS.md` section 6.

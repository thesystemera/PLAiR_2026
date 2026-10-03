# Handover: music upscaling chain (2-3 Oct 2026)

## How to work with the owner on this

- **Proof before words:** run `data/upscale_test_2026-10-02/scripts/proof.py ORIGINAL t0 RESULT t0 dur outprefix LABEL` on every render (spectrogram, change map, loudness, band changes level-matched, % of 1/3-octave cells cut, HF flatness) and look at the images before sending.
- Short answers, results not plans, no running commentary. One task at a time.
- Send loudness-matched files with short names; FLAC (16-bit) so they fit the 30 MB phone limit. Give a raw Suno version next to every render.
- Comparisons the owner liked: **beep cycles** (sections switch every 4 s, low/mid/high beep marks A/B/C) and **difference files** (B minus A, boosted, gain in the filename).
- Render on the **RTX 6000** (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES=1`), one model at a time, full songs. Profile a new model on 5 s first.
- E: is nearly full: keep renders on D: (`D:\_audio_quality_scratch\`; `data/upscale_test_2026-10-03` is a junction to it).

## Locked chain (live since 3 Oct, owner's sign-off)

Suno MP3 -> decode (44.1 kHz float) -> RoFormer vocal/music split -> **Lew's vocal Apollo** on the vocal (`AudioVocalEnhanceService`, weights `server/Apollo/vocal/apollo_vocal_lew.bin`, from the HF space patriotyk/Apollo `apollo_vocal2.bin`; v1 or v2 unknown) -> remix -> **Apollo** (full-mix top fill) -> **corrective EQ** (`AudioMasterService.correct_audio`: 25 Hz rumble cut, notches only for stationary resonances, tone vs the commercial-master curve) -> **SonicMaster** 25% wet, 20 steps, fp32, prompt "give the mix more shine and sparkle, with depth and separation between left and right", old Feb chunking (no align/conditioning, per-chunk RMS match), blend compensation on -> **final leveler** (`master_audio(..., correct=False)`: -14 LUFS, true-peak limiter, up to 6 dB).

Orchestrator lanes: 1 decode, 2 separation, 3 vocals + Apollo + corrective EQ, 4 SonicMaster (`SONIC_MASTER_ENABLED`), 5 Whisper, 6 leveler/transcode. `reprocess_catalog_audio.py` follows the same order (`--from source`). Uploads keep their own order; their vocal enhancer is also Lew's Apollo.

## Measured facts (3 Oct)

- Master tone target: Elowsson & Friberg (AES 2017, 12,345 pop masters) quadratic LTAS fit. The old pink target was ~7 dB (presence) / ~15 dB (air) too bright, so every song hit the +4 dB cap. Now: tolerance ±4 / ±6 dB, half the excess corrected, max ±3 dB; most songs get no tone EQ.
- Notch bug fixed: a notch needs ≥ 6 dB prominence in ≥ 90% of loud frames; real notes sit at 25-67% and are left alone.
- Limiter allowance 3 -> 6 dB: at 3 dB peaky songs were held at -17 LUFS. Finals are as dynamic as Suno (peak-to-loudness 11.9-12.4 dB vs 10.8-12.5; loudness range equal or +0.5 LU).
- SonicMaster regenerates the highs (no phase relation above ~2 kHz), so any partial wet mix loses power (25% plain = -2 dB); blend compensation restores it. At 100% it smooths codec flange but adds its own artifacts. The stereo prompt adds ~3.8 dB of side energy at 33%; mono fold-down -1.2 dB.
- 20 vs 50 steps: bands within 0.1 dB; the difference is 37 dB below the music but audible as artifact detail. Owner chose 20 for speed.
- Suno MP3s are ~175-180 kbps VBR, 48 kHz. Apollo (trained on 32-128 kbps) changes little at that rate (21-26 dB below the music) and mostly fills 18-22 kHz. Lew's Universal Apollo measured the same.

## Rejected (measured)

ClearVoice SE (gates), ClearVoice SR/cvsr (cuts 8-16 kHz on singing), the February ClearVoice chain (same), Smule Renaissance, BigVGAN, baicai vocal Apollo, HRAudioWizard, DTT-BSR (gates gaps/harmonies), BABE-2 (dull, 10 min per 6 s), rvq-artifact-remover (notchy DSP), AudioDelossifier 192k/256k (61 dB below the music = nothing), our own resonance suppressor (two versions, artifacts on quiet tails), AudioSR (cutoff replacement like cvsr). Suno's own Remaster/Advanced Split: owner won't use them.

## Open ideas

- Suno lossless WAV via sunoapi.org `/wav/generate` (~0.4 credits; needs the generation taskId, which the catalog does not store; store it on new generations). Unproven whether it is a true lossless decode.
- Intrect-style artifact mask: train a small U-Net (0.5·sigmoid mask) on the 4-stem Demucs residual (mix minus stems) plus codec-degraded pairs; ~1-2 days on the P6000; method is patent-pending. Owner's parked CycleGAN project is in `D:\Projects_parked\SUNO_UPSCALE` (blocked on a clean real-music dataset).

## Not done

- Catalog re-render (~2,100 songs) with the locked chain: `server/utils/reprocess_catalog_audio.py --gpu --from source` (refuses non-P6000 GPUs).

## Files

- A/B sent to the owner: `upscale_ab/` (chain_v8, blend_25_vs_33, stereo_prompts, prompt_test*, hole_remix, ...).
- Scripts: `data/upscale_test_2026-10-02/scripts/` (`render_final.py` = the locked chain for any track ids, `proof.py`, cycle/prompt/blend tests).

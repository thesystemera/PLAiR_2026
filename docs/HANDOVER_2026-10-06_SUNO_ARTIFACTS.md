# Handover: Suno codec artifacts, where we stand (6 Oct 2026)

The music chain (master chain version 4, CLAUDE.md section 18) fixes Suno's tone and its missing top end, but the owner still hears Suno's artifacts. This document is the brief for the next round: what the problem is, what is measured, what was tried, what has not been tried, and how to test. History of the chain itself: `docs/HANDOVER_2026-10-02_MUSIC_UPSCALING.md`.

## 1. The problem, as the owner hears it

- **Digital noise / grain:** a fizzy, granular texture in the highs and on sustained sounds, like low-bitrate streaming.
- **Flanging / phasiness:** a swirling, comb-like movement on cymbals, pads and the whole mix, which changes over time.
- **Harsh, flangy vocals:** metallic, phasey vocals with brittle sibilance; the worst offender.
- **Ringing notes / standing frequencies:** some notes build up and ring (see section 4, "notch hypothesis").
- **Dull top / brick wall:** fixed (section 2).

Goal: remove or mask these without removing music (the owner rejects anything that gates, thins or cuts instrumentation), at catalog scale (~2,000 songs, minutes per song on one GPU).

## 2. What chain version 4 does about each

| Artifact | Chain v4 | Status |
|---|---|---|
| Too bright at 8-12 kHz, dull at 16 kHz | Reference EQ to the modern-master average (Pestana 2013, No.1 singles 2000-2010), only what falls outside ±3 dB, up to 10 dB | Fixed: every tested song inside or within 0.3 dB of the band |
| Nothing above the 18 kHz MP3 wall | Apollo on the decoded MP3, keeping Suno's signal below its cutoff and filling above | Fixed (16-20 kHz +2-2.5 dB vs v3) |
| Codec holes in the highs | Steady tape hiss at -70 dBFS before the limiter | Masked: deepest holes halved; owner likes it |
| Flange / swirl | SonicMaster 33% wet (regenerates the highs; at 100% it smooths codec flange but adds its own artifacts) | Partly: still audible |
| Harsh flangy vocals | RoFormer split -> Lew's vocal Apollo -> remix | Partly: still audible |
| Digital noise / grain | Nothing targets it directly (Apollo changes little at Suno's bitrate) | Open |
| Ringing notes | Notch stage exists but never fires (90% presence rule) | Open, see section 4 |

SonicMaster's blend is the one stage that can itself sound phasey: its wet output is aligned to the dry to 1 sample but has no phase relation above 2 kHz (coherence 0.00).

## 3. Measured facts

**Suno fingerprint**, 2,242 raw Suno MP3s (`upscale_ab/suno_profile/`, `profiles_v2.jsonl` + `suno_profile.png`):
- Tone vs modern masters (median): +2.7 dB at 8 kHz, +3.2 at 10 kHz, +2.7 at 12.5 kHz, -2.5 at 16 kHz; the brightest 10% are +6 to +7 dB. About half the songs are outside ±3 dB at 8-16 kHz.
- MP3 cutoff: median 18.0 kHz (10-90%: 16.6-18.1 kHz). Files are ~175-180 kbps VBR, 48 kHz.
- Steady narrow peaks: ~5 per song; 95% are the song's own notes (Suno is tuned to A440 within ~1 cent; a few songs ~35 cents sharp). No frequency recurs across songs as a fixed artifact (the most common in 0.4% of songs).
- Static comb (fixed-delay flange): strong in only 1% of songs. Suno's flange is time-varying, not a fixed comb. (Detector validated: a synthetic 1 ms comb reads 19x, an untouched song ~5x; "strong" is 8x.)
- Holes (6-16 kHz cells 20 dB under their frame's median): ~1% per band in the mids, ~3% at 250-1k and 12-16k.
- Envelope range per band (95th-10th percentile): 17 dB at 250-1k up to 30 dB at 8-16k.

**Chain facts:**
- Apollo (JusperLee, trained on 32-128 kbps MP3s) changes Suno's music only 21-26 dB under the signal: Suno's bitrate is above what it learned, and Suno's artifacts are not MP3 artifacts. Lew's Universal Apollo measured the same.
- Suno's artifacts most likely come from its own generative audio decoder (a neural codec), with the MP3 on top. Anything trained only on MP3 damage will miss most of them.
- Notch stage: needs >= 6 dB above neighbours in >= 90% of the loud frames; Suno's ringing peaks are 8-13 dB on average but present 36-71% of the time, so it never fires (0 notches on 7 songs).
- GPU memory: the P6000 is shared with the live station (~13.6 GB). On Windows a full card spills silently to system RAM and runs a step 10-25x slower. Peaks: Apollo +8 GB per 20 s chunk, SonicMaster 3.9 GB model + ~3.8 GB (tiled VAE), Lew +1.8, RoFormer +0.8. A song takes ~5 min on the P6000, ~4 on the RTX 6000.

## 4. Tried and rejected (all measured, most heard by the owner)

The 2-4 Oct list is in `docs/HANDOVER_2026-10-02_MUSIC_UPSCALING.md` ("Rejected"): ClearVoice SE/SR and the February ClearVoice chain, Smule Renaissance, BigVGAN, baicai vocal Apollo, HRAudioWizard, DTT-BSR, BABE-2, rvq-artifact-remover, AudioDelossifier, AudioSR, A2SB, our own resonance suppressor (twice), De-limiter, a 2-8 kHz expander, a transient lift, and Suno's own Remaster / Advanced Split (owner won't use them). Common failure: they gate gaps and harmonies, cut 8-16 kHz on singing, only fill the top, or change nothing at Suno's bitrate.

Added 5-6 Oct:
- **Elowsson 2017 tone target:** 6-7 dB too dark for modern masters at 8-12 kHz (folk-heavy CD-era corpus); matching it made masters muddy. Replaced by Pestana 2013.
- **The owner's own mixes as a tone reference:** rejected by the owner.
- **Level-following noise for masking:** replaced by steady tape hiss (owner: "we are trying to create tape hiss").
- **Dynamic resonance tamer (notch hypothesis):** 3 s windows, 2.7 Hz bins, cut <= 8 dB while a peak rings > 6 dB above its neighbours. It found ringing peaks in every song, but they sit on the key notes. The owner: "it does make things sound clearer but possibly removing too much of actual instrumentation". Shelved. Retry only with a real-music control set (rings beyond normal records?) and a decay test (a standing wave keeps ringing after the note changes; a note stops). Excerpts: `upscale_ab/notch_test/`.
- **Running Apollo after the remix (v3):** replaced by Apollo first (v4); spectrally nearly identical, but Apollo now gets the input it was trained on.

## 5. Leads not yet tried

Ordered by how much each could change, and whether it fits the owner's rules (learned restoration over hand-written DSP, no content removal, the owner OKs downloads). Check licence, size and speed before any download, and profile on 5 s first.

1. **Better source: Suno's lossless WAV.** sunoapi.org `/wav/generate` (~0.4 credits per song) needs the generation taskId, which the catalog doesn't store yet (store it on new generations). It would remove the MP3 layer only; the decoder's own artifacts stay. Unproven whether it is a true lossless decode: test on 2-3 new generations against their MP3s.
2. **A restoration model trained on the right damage.** Suno's damage is mostly neural-codec damage, so train (or fine-tune Apollo / a Mel-Band RoFormer) on pairs of clean real music vs the same music pushed through neural codecs (DAC, EnCodec, SNAC at low bitrates) and then MP3 at ~180 kbps. Clean data: MUSDB18-HQ (150 songs, ~22.7 GB, zenodo record 3338373), MoisesDB, FMA full, MTG-Jamendo. Intrect's ArtifactNet (patent-pending) uses a learned mask on a Demucs residual. The owner's parked CycleGAN project is in `D:\Projects_parked\SUNO_UPSCALE`.
3. **Per-stem treatment.** Split 4 stems (vocals, drums, bass, other) and restore each with what suits it; the vocal is the worst, and a mix-level model can't treat it differently.
4. **Vocal re-synthesis.** Re-render the separated vocal through a singing-voice vocoder or singing voice conversion to itself (e.g. NSF-HiFiGAN as used in DiffSinger, Seed-VC singing), which rebuilds the waveform and drops the codec texture. Risk: timbre and expression change; measure against the stem.
5. **Community separation and restoration models.** The MVSEP / UVR community trains Mel-Band and BS-RoFormer models for de-noise, de-reverb and "fullness" instrumentals, some on AI-music material. Survey with an agent; the owner said: read what each model does from its docs, don't blindly test what's on disk.
6. **Music Source Restoration** (MSR, 2025): a research task and challenge on restoring degraded stems. Check for released models.
7. **Measure Suno's decoder fingerprint directly.** AI-music detection work (Afchar et al., Deezer, 2024: "Detecting music deepfakes is easy but actually hard") reports that autoencoder decoders leave small, regular spectral artifacts from their upsampling layers. Our profile only counted peaks >= 6 dB; average the noise-normalised spectrum of all 2,242 songs at high resolution to find small fixed peaks (1-3 dB) that no single song shows. If they exist, they are a precise target (and something we can check every render against).
8. **A time-resolved flange measure.** Inter-channel phase coherence and comb movement over time per band, so the flange can be scored per song, before and after each stage.
9. **A "Suno-ness" score as the test metric.** Train a small Suno-vs-real classifier (SONICS dataset, Rahman et al., ICLR 2025, has Suno/Udio songs) and use its confidence before and after each stage, next to Audiobox Aesthetics (`services/audio_quality_score_service.py`). This gives an objective target for any restoration idea.

## 6. Test environment

All scripts are in `scripts/music_lab/` (in git). Outputs go to `upscale_ab/` (repo root, git-ignored).

- **Render a song with the production chain:** `cd server; ../.venv/Scripts/python.exe utils/process_backlog.py --track <id> [--track <id> ...] --keep-intermediates --gpu 0|1 --in-flight 1` (0 = P6000, 1 = RTX 6000 when the owner isn't using it). This replaces the song's live master. Intermediates stay in `D:\catalog\{decoded_wav, apollo_wav, demucs_stems/<id>/roformer, premaster_wav, sonic_wav}`.
- **A/B/C for the owner:** `export_three.py <id> ...` writes `upscale_ab/v4_three_way/<Title>_{1_Suno, 2_v4_no_hiss, 3_v4_tape_hiss}.flac` (all -16 LUFS; 2 and 3 from the same SonicMaster file) and prints each against the industry band. 9 songs are there now.
- **Suno vs v3 vs v4 table + plot:** `measure_chain.py <Name> <id> <outdir>` (expects `<outdir>/<Name>_v3_live.wav`).
- **Proof images for any render:** `proof.py ORIGINAL t0 RESULT t0 dur outprefix LABEL` (spectrogram, change map, band changes, % of cells cut, HF flatness).
- **Suno fingerprint:** `suno_profile.py [workers]` (1.6 s per song, ~10 min for the catalog on 10 CPU workers) then `suno_profile_report.py`.
- **Notch hypothesis:** `notch_hypothesis.py [seed]` (6 random songs: why the notch stage doesn't fire + a time-resolved scan) and `ringing_excerpts.py` (raw / tamed / removed-only excerpts).
- **GPU probes:** `stage_memory_probe.py`, `sonic_memory_probe.py`, `vae_tiling_probe.py` / `vae_tiling_verify.py`, `sonic_residency_probe.py`; `gpu_mem_watch.ps1` logs a render's dedicated and spilled GPU memory.
- **Catalog-wide re-render:** `process_backlog.py --rerender` (or `reprocess_catalog_audio.py` for A/B sets with backup/swap/restore; it refuses anything but the P6000).
- **Reference values in code:** `MODERN_MASTER_HZ/DB`, `REFERENCE_TOLERANCE_DB` (audio_master_service.py), `MASTER_TAPE_HISS_DBFS`, `SONIC_MASTER_*` (settings.py).

## 7. Working with the owner on this

- Proof before words: measure every render against the original (levels per band, gating check with per-100 ms levels and a spectrogram) before sending. The owner is not the test instrument.
- Send loudness-matched FLACs (under 30 MB for the phone, else 320k MP3) with short names, always with raw Suno alongside; for a change, also send "removed only" (boosted) so they can hear what was taken.
- Judge tone by the industry chart and numbers. Anything that removes instrumentation is rejected, however clean it sounds.
- Short answers, results not plans. Ask before downloads (name, source, size) and before long GPU jobs; profile 5 s first.
- GPUs: P6000 = PLAiR (shared with the live station); RTX 6000 = the owner's DeepPBR card, use only when they say so.

## 8. Open owner decisions

- When, and on which GPU, to re-render the catalog to chain v4 (~2,000 songs).
- Downloading a real-music control set (MUSDB18-HQ, 22.7 GB) for leads 2, 7, 9 and the notch retry.
- Deleting `D:\_audio_quality_scratch` (29 GB old scratch; the agent's safety check blocks it).

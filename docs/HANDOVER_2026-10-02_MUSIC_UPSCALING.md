# Handover: music upscaling chain (2 Oct 2026)

## How to work with the owner on this

- **Check every file yourself before sending it:** measure its loudness and its band levels (2-4, 4-8, 8-12, 12-16, 16-20 kHz) against the original, and say in one line what changed. If a version loses something (e.g. sibilance at 8-12 kHz), catch it before the owner has to hear it.
- Answer in short Q&A. No tables, no long explanations, one task at a time.
- When sending audio, send exactly what was asked, as separate files with short names (`ORIGINAL.wav`, `AI.wav`). No flip files unless asked.
- Render full songs, never slices: ClearVoice normalises over the whole file, so a slice comes out different.
- Render on the **RTX 6000** (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES=1`). The owner allowed this on 2 Oct, when it is idle. The live station uses 12-21 GB of the P6000; a render next to it starved Whisper and crawled.
- Load one model at a time and unload it after its stage.

## Owner's design (what they want)

Suno track -> separate into stems -> enhance the vocal stem with a **vocal-enhancing GAN** (Suno vocals have digital, "computer" artifacts) -> Apollo for missing high frequencies -> recombine at the same levels -> SonicMaster (owner's prompt, 50/50 blend with phase/power compensation, 30 s chunks with continuous half-chunk Hann crossfades) -> master EQ at 100%.

## Open task (where the owner stopped)

The owner wants to hear the **raw output of the two vocal-enhancing GANs on our computer** on a separated Suno vocal, as plain files, against the original vocal. Nothing else applied.

- The two GANs on disk are the ClearVoice models: **MossFormer2_SE_48K** (speech enhancement) and **MossFormer2_SR_48K** (speech super-resolution, "cvsr", the one that sounded night and day on the DJ TTS voices). The ClearVoice package also ships MossFormerGAN_SE_16K.
- Use "Concrete Echoes" (`0bf1fdcc55a92e042e57319136f208e9`), RoFormer vocal stem at `data/upscale_test_2026-10-02/ab/0bf1fdcc55a92e042e57319136f208e9/stems/vocals.wav`.
- Send 3 files of the same 60 s (70-130 s): `ORIGINAL.wav`, `SE.wav` (raw SE_48K output), `SR.wav` (raw SR_48K output). SR raw was sent once as `AI.wav`; SE raw alone was never sent.
- Measured so far: SR_48K keeps the vocal below the bandwidth it detects (`clearvoice/utils/bandwidth_sub.py` `detect_bandwidth`, 99% energy) and regenerates above it. On singing it detects ~9.75 kHz, so it replaces Suno's real 10-16 kHz with duller speech-trained content: -3 dB at 8-10 kHz, -7.5 dB at 10-12 kHz (lost sibilance). The DJ takes were 24 kHz files, empty above 12 kHz, which is why SR sounded so good there. Forcing the cutoff to 16 kHz keeps sibilance but then SR barely changes anything. The owner rejects dropping vocal enhancement.
- The old chain (SE_48K -> confidence blend -> SR_48K -> envelope warp) was heard as versions C/D and sounded dull. Raw SE alone on music vocals has not been heard.
- If neither works: Resemble Enhance is pip-installed but has no weights on disk (download needs the owner's OK).

## Committed state (main)

- `ed5d6f9`, `70341dc`, `a6b8cc9`, `f16dcc3`. In `server/config/settings.py`: `SEPARATION_MODEL=roformer` (vocal Mel-Band RoFormer, owner preferred it over Demucs by ear), `ROFORMER_NUM_OVERLAP=2` (2x faster, difference 37 dB below the vocal), `SONIC_MASTER_SUNO_PROMPT` = owner's original wording "give the mix more shine and sparkle, clean and dynamic with rich full harmonics", `SONIC_MASTER_TEMPLATE_PROMPTS=false`, `SONIC_MASTER_STEPS=20`, align and conditioning on, per-chunk RMS match off (the owner wanted only the wording reverted), `SONIC_MASTER_BLEND_COMPENSATION=true`, `SONIC_MASTER_CHUNK_OVERLAP_S=15` (half chunk = continuous 30 s crossfade, owner insists; the 5 s attempt was reverted).
- `AudioMasterService.master_wet_mix = 1.0`; reprocess `SUNO_MASTER_WET = 1.0`. Uploads keep their own blend.
- `server/services/audio_clearvoice_service.py` is now **SR_48K only**, level-matched (LUFS) to the input vocal, with batched 4 s windows (identical output, 127 dB null, ~40% faster). The SE model and envelope warp were removed. Revisit this once the owner picks a vocal enhancer.
- Backend not restarted since these changes; production still runs the old code until PLAiR Start is run.

## Measured facts

- SonicMaster output has ~0 coherence with its input above 2 kHz, so a plain 50/50 blend loses 3 dB there; blend compensation fixes it.
- Apollo only fills 18-22 kHz (Suno cuts at ~18 kHz, -90 dB -> ~-33 dB); below 18 kHz it changes almost nothing. Its model cannot batch.
- Master notch finder bug (not fixed): it always notches the 4 biggest long-term spectral peaks (prominence >= 1 dB) by up to -11.6 dB at Q 50. On "This Air Is Mine" and "Concrete Echoes" these were the song's own notes (C/G and C#/G# harmonics), not standing waves.
- Audiobox Aesthetics (Meta) scores at 16 kHz mono, so it cannot hear above 8 kHz or stereo. Before/after differences were within ±0.06; useless as a judge here.
- Speed on the RTX 6000, full song, one model at a time: about 1.1x real time (3.8 min for a 3.35 min song) with SonicMaster at 20 steps and 15 s crossfades.
- Running several full-chain renders in one process once ended in "CUDA illegal memory access"; one song per process was fine.

## Not done

- Catalog re-render (~2,100 songs; ~1,360 have old masters). Should run as a background job on the RTX 6000, one model at a time, full songs. `server/utils/reprocess_catalog_audio.py` exists but refuses non-P6000 GPUs and keeps all models loaded.
- Master notch bug fix.

## Files

- A/B audio sent to the owner: `upscale_ab/music/<song>/`.
- Work files: `data/upscale_test_2026-10-02/` (`ab/<id>` has full-song stages: apollo, stems, vocals_enhanced, vocal_mix, sonic, final).
- Render scripts (copied to `data/upscale_test_2026-10-02/scripts/`): `render_ab.py` (full song, per-stage unload, A/B pair), `vocal_variants.py`.

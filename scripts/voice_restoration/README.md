# Voice restoration (parked)

Tested on 2026-10-03 against the live upscaler (ClearVoice `MossFormer2_SR_48K`, "CVSR", see CLAUDE.md
section 12). The owner kept CVSR alone for live use. These two are parked: offline only, never loaded by the
backend. Findings are in `docs/TTS_ENGINE_RESEARCH.md` ("Restoration after the engine").

| Model | 3 s take on the P6000 | Verdict |
|---|---|---|
| CVSR (live) | 0.56 s | Kept: clear, sparkly, fast enough |
| Resemble Enhance, quality (midpoint, 64 steps) | 1.5 s | Small gain; after CVSR in a full mix it barely shows. Music vocal stems (13 Nov): gates soft singing, harmonies and choruses, rejected |
| Resemble Enhance, fast (euler, 8 steps, no denoiser) | 0.6 s | Same, slightly less |
| Sidon | 0.17-0.28 s | Drier, more studio-like, but artifacts; sounded worse |

## Setup

Found on 13 Nov: `resemble-enhance` 0.0.1 and `deepspeed` 0.12.6 are installed in the production venv, and the
runner uses that package when `--repo` is left out. Its weights are in `E:\scratch\plair\resemble_enhance`
(`hparams.yaml` already fixed for Windows). The runner skips the package's model summary (it needs a newer
`tabulate` than the venv has), takes `--chunk` (GPU memory grows about 3 GB per 10 s; 10 s next to the live
station) and joins chunks with a plain crossfade unless `--shift-joins` is given.

For a clean install elsewhere (own venv each, never the production venv):

Python 3.11, PyTorch 2.6 cu124 (Pascal needs a CUDA 12.x wheel), fp32. Always pin the P6000:
`CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=0`.

**Resemble Enhance** (MIT): clone https://github.com/resemble-ai/resemble-enhance and install only its light
dependencies (no deepspeed, gradio or the old torch pin). `resemble_enhance_dir.py` stubs deepspeed, which only
the training code imports. Weights: `ResembleAI/resemble-enhance` (`enhancer_stage2`, 0.7 GB). On Windows, edit
the weights' `hparams.yaml`: its three `!!python/object/apply:pathlib.PosixPath` values must become plain strings.

```bash
python scripts/voice_restoration/resemble_enhance_dir.py <folder> --repo <clone> --weights <weights> --setting quality
```

**Seed-VC** (GPL-3.0; called as a separate process, never copied into our code), for re-singing music vocals:
a clone of https://github.com/Plachtaa/seed-vc in `E:\scratch\plair\seedvc\seed-vc`, run with the production
venv's Python. The only package it lacks there, `munch`, sits in `E:\scratch\plair\seedvc\extra` with a stand-in
for `dac` (Descript's codec, imported but never built by the 44.1 kHz singing model); both go on `PYTHONPATH`.
Its models download on first use (`Plachta/Seed-VC` singing model, `openai/whisper-small`, RMVPE, CAM++; BigVGAN
44 kHz) with `HF_HOME=E:\scratch\plair\hf`. The runner forces fp32 (Pascal) and loads the models once per folder.

```bash
HF_HOME='E:\scratch\plair\hf' PYTHONPATH='E:\scratch\plair\seedvc\extra' python scripts/voice_restoration/seedvc_dir.py <folder> --repo E:/scratch/plair/seedvc/seed-vc --reference <voice.wav>
```

**Sidon** (MIT): only torch, torchaudio, transformers and huggingface_hub. Weights `sarulab-speech/sidon-v0.1`
(TorchScript, 1 GB) plus the `facebook/w2v-bert-2.0` feature-extractor config. It also loads in the production
venv's torch 2.5.1.

```bash
python scripts/voice_restoration/sidon_dir.py <folder>
```

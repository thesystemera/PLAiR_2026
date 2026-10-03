# Voice restoration (parked)

Tested on 2026-10-03 against the live upscaler (ClearVoice `MossFormer2_SR_48K`, "CVSR", see CLAUDE.md
section 12). The owner kept CVSR alone for live use. These two are parked: offline only, never loaded by the
backend. Findings are in `docs/TTS_ENGINE_RESEARCH.md` ("Restoration after the engine").

| Model | 3 s take on the P6000 | Verdict |
|---|---|---|
| CVSR (live) | 0.56 s | Kept: clear, sparkly, fast enough |
| Resemble Enhance, quality (midpoint, 64 steps) | 1.5 s | Small gain; after CVSR in a full mix it barely shows. Maybe for music vocal stems |
| Resemble Enhance, fast (euler, 8 steps, no denoiser) | 0.6 s | Same, slightly less |
| Sidon | 0.17-0.28 s | Drier, more studio-like, but artifacts; sounded worse |

## Setup (own venv each, never the production venv)

Python 3.11, PyTorch 2.6 cu124 (Pascal needs a CUDA 12.x wheel), fp32. Always pin the P6000:
`CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=0`.

**Resemble Enhance** (MIT): clone https://github.com/resemble-ai/resemble-enhance and install only its light
dependencies (no deepspeed, gradio or the old torch pin). `resemble_enhance_dir.py` stubs deepspeed, which only
the training code imports. Weights: `ResembleAI/resemble-enhance` (`enhancer_stage2`, 0.7 GB). On Windows, edit
the weights' `hparams.yaml`: its three `!!python/object/apply:pathlib.PosixPath` values must become plain strings.

```bash
python scripts/voice_restoration/resemble_enhance_dir.py <folder> --repo <clone> --weights <weights> --setting quality
```

**Sidon** (MIT): only torch, torchaudio, transformers and huggingface_hub. Weights `sarulab-speech/sidon-v0.1`
(TorchScript, 1 GB) plus the `facebook/w2v-bert-2.0` feature-extractor config. It also loads in the production
venv's torch 2.5.1.

```bash
python scripts/voice_restoration/sidon_dir.py <folder>
```

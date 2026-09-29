# TTS engine research (2026-09-29)

Web research only (three passes: full landscape, speed on Pascal, community reports on expressiveness/reliability). Nothing here was measured on our card yet. Follows `docs/HANDOVER_TTS_ENGINE_SEARCH.md`.

## What we need

- At least as fast as Orpheus on the Quadro P6000 (one stream RTF ~1.2; two streams ~1.0-1.15 s audio per second), streaming PCM.
- Better quality. Delivery must work with our own **spelled-out paralanguage** ("ha ha", "uh", "w-w-whoa"); engine tags are not needed (we don't use Orpheus tags).
- Commercial licence, local only.
- Voices: we are **not** keeping Leo/Jess. Out-of-the-box voices to audition, and cloning so we can pick any voice.
- The RTX 6000 may be free in a few weeks; until then P6000 only.

## Why the P6000 decides most of this

- **No usable fp16/bf16** on Pascal (fp16 at 1/64 rate, bf16 emulated). PyTorch silently runs fp32: Qwen3-TTS 1.7B in PyTorch on a Tesla P40 (same GP102 chip) measured **RTF 7.3**. Only GGML/llama.cpp (quantised, dp4a int8 kernels), ONNX, or small fp32 models are realistic.
- **Not available on sm_61:** CUDA 13 (dropped Pascal; stay on 12.x), PyTorch cu128+ wheels (use cu126), TensorRT 10 (SM 7.5+), vLLM / SGLang / vLLM-omni (CC 7.0+/7.5+), FlashAttention 2 (sm80+), `torch.compile` (Triton, CC 7.0+). Every vendor "sub-100 ms with vLLM" claim is irrelevant here.
- ggml disables CUDA graphs before Ampere, so each token pays per-kernel launch overhead. On this card speed tracks **layer-steps per second of audio** (layers × autoregressive steps), not parameter count. Orpheus/Maya1: 86 steps × 28 layers ≈ 2,400.
- llama.cpp merged Qwen3-TTS (`llama-tts`, talker + predictor + codec in ggml) on 2026-08-04 (PR #26254); server `/tts` endpoint still a draft (#26603). `audio.cpp` (0xShug0) is one ggml engine for 20+ TTS families with an OpenAI-style streaming server; Pascal build flag added (PR #682) but untested on Pascal.

## Recommendation (P6000, spelled-out paralanguage)

1. **Qwen3-TTS 1.7B** (Apache-2.0). Same llama.cpp stack as Orpheus; `qwentts.cpp` has a streaming OpenAI-style server and Windows CUDA build scripts (F32/Q8_0/Q4_K_M). 12.5 Hz × 16 codebooks: ~1,290 layer-steps/s, **est. RTF 0.4-0.7** here (RTX 5050 via llama.cpp measured 0.35, ~300 ms first audio). Voices: 9 presets (English: Ryan, Aiden, both male; the others are Chinese/Japanese/Korean speakers), **VoiceDesign** (describe a voice in words), Base cloning from 3 s. Natural-language delivery instructions work on presets/VoiceDesign, not on cloned voices. Risks: scores low in blind tests (Artificial Analysis 930), reports of wrong-timbre short lines (~15%, on a fine-tuned voice), clone mode can echo the reference tail, time strings misread in clone mode.
2. **Chatterbox-Turbo** (MIT). 350M GPT-2 T3 at 25 Hz × 24 layers ≈ 600 layer-steps/s; **est. RTF 0.2-0.4** with CUDA-graph capture of the T3 loop (A6000: 22 → 385 tok/s), stock HF `generate` may not reach real time. Official ONNX export (fp32/q8/q4). Voices: one default, cloning from 5-10 s. Arena Elo 1023 (Chatterbox family). Risks: **gibberish on very short lines** ("Hi!", "Yes") (issue #97, open), unstable past ~25-30 s, PerTh watermark in every output (removable, MIT code), no official streaming.

Voice sourcing for cloning: Qwen3 VoiceDesign output (Apache) makes clean reference clips for either engine; CC-BY corpora (VCTK, LibriTTS-R) also work. Avoid Expresso/EARS (CC-BY-NC).

## Other candidates

| Model | Licence | Control | Est. P6000 | Why not first |
|---|---|---|---|---|
| VoxCPM2 2B | Apache | Per-line style on a cloned voice, 48 kHz | >1 (4090 RTF 0.30) | Too slow on Pascal; varies between takes. Strong pick on the RTX 6000. |
| Step-Audio-EditX 3B | Apache code, weights unclear | 14 emotions, ~30 styles, interjection sounds, cloning; AA Elo 1094 (best commercial) | >1, no streaming | Speed, 12 GB, Chinese-first. RTX 6000 candidate. |
| IndexTTS-2 / 2.5 | bilibili (free <100M MAU) | Emotion vectors, duration control, cloning | >1 (A100 fp32 0.84) | Speed, licence caps, no laughs |
| Maya1 3B | Apache card (likely Llama-3.2 terms too) | 20 tags, voice from description | 1.2 (same design as Orpheus) | No speed gain, no stable voice identity |
| Higgs Audio v2 5.8B | Community (<100k users, attribution) | Best community prosody | too big | Size, licence |
| Dia2 1B/2B | Apache | Whole two-speaker dialogues | PyTorch bf16 | Voice drifts per take; we render line by line |
| Hume TADA 1B | Llama 3.2 | No control; no hallucinations by design | ~0.3-0.6 unverified | No streaming runtime, no expression |
| Chatterbox-Nano 110M | MIT | Same as Turbo | ~0.1-0.2 | Quality unknown at 110M |
| MioTTS 0.6-1.2B | Apache (by size) | Cloning | ~0.15-0.3 | English quality unverified |
| Kokoro 82M / Supertonic / Pocket TTS | Apache / OpenRAIL-M / MIT | Presets, flat | very fast | No expressiveness |
| CosyVoice3 0.5B | Apache | Instruct, [laughter] | ~0.6-1.2 | Chinese-centric |
| NeuTTS Air, Kani-TTS-2 | Apache / LFM (<$10M) | Cloning, no emotion | ~0.5-1 | Limited expressiveness |

**Ruled out (licence):** Breeze TTS 2 (top open Elo 1207), Fish S2 Pro / OpenAudio S1, Voxtral TTS, Higgs Audio v3, OmniVoice, Spark, F5, Llasa, OuteTTS, XTTS-v2, Echo, MiraTTS, Kyutai's expressive voices (Expresso/EARS NC). **Too big:** MOSS-TTSD 8B, MisoTTS 8B, VibeVoice 7B, KugelAudio 7B. **Not open:** StepAudio 3, Qwen-Audio-3.0-TTS. **Other:** ZONOS2 is Linux-only; VibeVoice adds spontaneous music; Sesame CSM-1B slow/weak.

**Spelled-out laughter:** no commercially usable model is proven to turn "ha ha ha" into real laughter; Qwen3 reads it as words, Higgs turns sound words into foley. Our planner's spelled-out style has to be judged by ear in the bake-off.

## Leaderboard context

Artificial Analysis open-weights Elo (Sep 2026): Breeze 2 1207, Fish S2 Pro 1118, Step Audio EditX 1094, Voxtral 1080, Kokoro 1065, Magpie 357M 1063, Maya1 1046, OpenAudio S1 mini 1042, Higgs v3 1037, Chatterbox 1023, Zonos 1000, VibeVoice 955, Qwen3-TTS 930. Closed leaders: Eleven v4 1315, Cartesia Sonic 3.6 1279.

## Sources

- Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS, https://github.com/ServeurpersoCom/qwentts.cpp, https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF, https://github.com/ggml-org/llama.cpp/pull/26254, https://github.com/andimarafioti/faster-qwen3-tts, https://tinycomputers.io/posts/the-real-cost-of-running-qwen-tts-locally-three-machines-compared.html, https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base/discussions/2
- Chatterbox: https://huggingface.co/ResembleAI/chatterbox-turbo, https://huggingface.co/ResembleAI/chatterbox-turbo-ONNX, https://github.com/resemble-ai/chatterbox (issues #97, #543, #548), https://github.com/devnen/Chatterbox-TTS-Server/issues/175, https://github.com/jamiepine/voicebox/issues/1110
- audio.cpp: https://github.com/0xShug0/audio.cpp (PRs #393, #394, #682)
- Others: https://huggingface.co/openbmb/VoxCPM2, https://huggingface.co/stepfun-ai/Step-Audio-EditX, https://huggingface.co/IndexTeam/IndexTTS-2.5, https://huggingface.co/maya-research/maya1, https://github.com/nari-labs/dia2, https://github.com/HumeAI/tada, https://huggingface.co/bosonai/higgs-tts-2-3b-base, https://github.com/OpenMOSS/MOSS-TTS, https://neosophie.com/en/blog/20260317-tts
- Leaderboards: https://artificialanalysis.ai/text-to-speech/leaderboard/open-weights
- Platform: https://docs.nvidia.com/cuda/archive/13.0.1/cuda-toolkit-release-notes/index.html, https://github.com/pytorch/pytorch/issues/157517, https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html, https://github.com/vllm-project/vllm/issues/963

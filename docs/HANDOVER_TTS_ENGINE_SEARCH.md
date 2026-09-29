# Handover: find a better TTS engine for the PLAiR DJs (2026-09-29)

## The ask

The DJ voices need a better engine than what we run now. It must be **at least as fast** as the current engine on this machine, with **better audio quality**, and ideally more expressive control: a way to say *how* a line should be delivered (emotion, intonation, emphasis, pace), not just what to say.

Our own paralanguage system is good and stays: the planner writes stage directions and the hosts spell out a lot of reactions themselves ("ha ha ha"). An engine that handles those well, and adds tone/style control on top, is the goal. New voices are fine; we are not attached to the current ones.

Read `CLAUDE.md` section 12 (Local TTS Engine) before touching anything.

## Where we are

**Engine:** Orpheus-3B, a Llama-based model that writes SNAC audio tokens.
- Runs as GGUF Q4_K_M through llama.cpp CUDA, with a SNAC ONNX decoder.
- Its own process: `tts_server/` (Flask on 127.0.0.1:8090, own venv). The backend starts and stops it.
- Ported from the owner's LifeSpan project to replace ElevenLabs (retired for cost).

**Hosts:**
- `[LEO]` uses Orpheus `leo`.
- `[JESS]` uses Orpheus `jess`. She replaced `tara` today.
- The station computer uses `zac` with a radio treatment.

**Problems the owner hears:**
- **Jess sounds bad on paralanguage.** She struggles to make reactions and spelled-out sounds land. This is why we're here.
- **Tara, before today, was the wrong level and pace.** She was ~7 dB quieter and ~13% slower than Leo. We don't level individual clips; it wrecks the mix.
- **Lines sometimes repeat or cut off.** Orpheus occasionally loops, e.g. "Kicking it off with some seriously" said twice. A length cap cuts runaway takes, which can sound like an early cut.
- **General artifacts** and a "could be better" quality ceiling.
- **Emotion tags underused.** Orpheus only has 8 inline tags (`<laugh> <chuckle> <sigh> <gasp> <groan> <yawn> <cough> <sniffle>`) and no style or intonation control.

**Measured today:** all 8 Orpheus voices on the same 8 radio lines, one at a time on the P6000. Whisper large-v3-turbo checked the words. Script: `tests/tts_voice_bakeoff.py`.

| voice | words/s | LUFS | words heard | RTF (render time ÷ audio length) |
|---|---|---|---|---|
| leo | 3.34 | −19.7 | 96% | 1.20 |
| tara | 2.91 | −26.7 | 92% | 1.26 |
| jess | 3.21 | −20.9 | 92% | 1.25 |
| zoe | 2.93 | −19.6 | 96% | 1.24 |
| mia | 3.15 | −25.2 | 96% | 1.24 |
| leah | 3.05 | −22.8 | 96% | 2.34 |
| dan | 2.82 | −20.3 | 96% | 1.23 |
| zac | 2.83 | −20.5 | 100% | 1.24 |

**The speed bar to beat:**
- One stream runs at RTF ~1.2 (~74 tokens/s; real time needs ~82).
- Two concurrent streams give ~1.0–1.15 s of audio per second in total.
- Time to first audio is what the listener feels most.
- The clip cache, impulses and fillers hide the rest.

## Hard constraints

- **GPU:** Quadro P6000 only (Pascal, sm_61, 24 GB; the backend, Whisper, mpnet and the current TTS already use ~8 GB). Selected via `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=0`.
  - **Never use the Quadro RTX 6000 (index 1).** It belongs to the owner's other projects. That includes headless browsers.
  - Pascal has **no fast fp16/bf16**. PyTorch models silently run fp32 and are often far slower than their published numbers. One report measured Qwen3-TTS 1.7B in PyTorch at RTF 7.3 on a P40, which is the same chip. Favour GGML/llama.cpp, ONNX, int8, or small models.
- **Local only**, no paid cloud TTS.
- **Licence must allow commercial use.** PLAiR is going public.
- **Streaming output.** The rest of the pipeline expects raw PCM chunks as they are made (see the interface below).
- **Shared machine.** Other sites and GPU projects run here. Test on a private port, don't leave things running, and never run `external_components/restart_all.bat`.
- **Build gotchas** for the current engine are in `tts_server/README.md` (llama-cpp-python built last for archs `61;75`, cuDNN pinned). Keep the current engine working while evaluating.

## The interface a new engine must fit

The backend only talks to the engine over HTTP (`tts_server/README.md`, `tts_server/server.py`):

- `POST /tts` takes `{"text", "voice", "temperature?", "top_p?", "min_p?", "max_tokens?", "seed?"}`.
  - It streams **raw PCM s16le, 24 kHz, mono**.
  - The headers `X-Job-Id` and `X-Sample-Rate` come with the response.
- `POST /abort/<job_id>` and `POST /abort` cancel jobs when a listener interrupts.
- `GET /health` returns `{"status", "workers", "active_jobs", "queue_depth", "voices"}`.

A new engine can sit behind the same endpoints. Then the backend barely changes:
- `settings.VOICE_PREFERENCES` maps host → engine voice + temperature.
- `tts_generation_service.generate_local_tts` calls `/tts`.

Anything that takes style or emotion instructions would need a new optional field, e.g. `"style"` or `"instruct"`.

**Places that know about Orpheus specifically:**
- `dj_prompt_system_service.generate_meta_data_gpt_response` turns `*meta*` stage directions into Orpheus tags. It would map to the new engine's tags or style controls.
- The DJ prompt tag guide (`format_meta_tags_guide` / `format_meta_tag_examples` in `context_nodes.py`).
- The length cap (`TTS_DURATION_CAP_*`) and `<|end_of_speech|>` stop logic in `tts_server/server.py`.
- The talking clock (`talking_clock.py`) carries a workaround because Orpheus loops on bare numbers.
- Sample rate: 24 kHz is assumed in places (`TTS_SAMPLE_RATE`). A different native rate is fine if the engine resamples, or the setting changes.

**Must not change (the "sacred design"):** the performance planner, meaning:
- two hosts overlapping via `@N@`, `&N&` mixes;
- `*meta*`, `%sfx%` beds, breaths and impulses;
- the clip cache (near match plays now, exact line renders in the background).

Only the voice engine underneath changes. After a switch, purge the regenerable caches (`tts_audio`, `meta_audio`, `impulse_audio`, `breath_audio`, `station_audio` + their embedding rows) and let everything re-render. Never purge `audio_effect_audio`.

## Candidates

From a web search today (sources in the linked pages). **None has published Pascal numbers, so every one needs measuring here.**

| Model | Why it's interesting | Watch out |
|---|---|---|
| **Qwen3-TTS** 0.6B / 1.7B (Apache-2.0) | Runs as GGUF in upstream llama.cpp / `qwentts.cpp`, the same stack as Orpheus. Streams, ~12.5 audio frames/s (much less work per second of audio than Orpheus). Preset speakers, voice cloning and voice design; instruction-based emotion and style. | Rare runaway loops (~0.2%); no open inline laugh tags. https://github.com/QwenLM/Qwen3-TTS, https://github.com/ServeurpersoCom/qwentts.cpp |
| **Chatterbox-Turbo** 350M (MIT) | Small, fast, inline `[laugh] [chuckle] [sigh] [gasp] [cough] [groan]`; voice cloning from ~10 s; ONNX/C++ port. | Reported repeated endings and gibberish on very short lines; watermark. https://huggingface.co/ResembleAI/chatterbox-turbo |
| **Hume TADA** 1B / 3B (Llama 3.2 licence) | Aligns text and audio 1:1, so by design it doesn't skip or loop (our repeat/cut-off problem). Expressive. | Streaming not documented. https://github.com/HumeAI/tada |
| **Dia2** 1B / 2B (Apache) | Built for two-speaker dialogue (`[S1]/[S2]`), streams, non-verbals. | Voices drift between takes unless given an audio prefix; bf16-oriented. https://github.com/nari-labs/dia2 |
| Kyutai TTS 1.6B (CC-BY) | Streams text in, low latency. | Fixed voice set, no cloning. |
| Kokoro 82M (Apache) | Very fast and stable, even on CPU. | No emotion, no paralanguage: the safe fallback, not the goal. |
| Maya1 3B (Apache) | 20+ emotion tags, voice design. | Same Llama + SNAC design as Orpheus, so likely the same speed and looping. |
| Ruled out | Fish S2 (research-only licence, RTF 1.3 on a 3090), IndexTTS2 (licence needs written OK), Higgs Audio v2 (too heavy), CSM-1B (too slow), F5 / Spark (non-commercial), VibeVoice (research only). | |

Voice cloning (Qwen3, Chatterbox, TADA) also solves the level/pace mismatch: pick two reference voices that match each other.

## Suggested plan

1. **Short-list 2–3 engines** (likely Qwen3-TTS GGUF, Chatterbox-Turbo, TADA). Install each in its **own venv** under `tts_candidates/<name>/` (don't touch `tts_server/` or its venv), on `CUDA_VISIBLE_DEVICES=0`.
2. **Bake-off on this card** with the same lines for every engine. Extend `tests/tts_voice_bakeoff.py` (it measures words/s, LUFS, Whisper word recall, loops, cut-offs, RTF). Add:
   - time to first audio;
   - lines with our paralanguage: spelled-out laughs like "ha ha ha", tags, and "Mm-hmm."-type interjections;
   - very short lines ("Yeah!", "Oh.");
   - numbers and times ("half past nine", "forty-six minutes past");
   - names and places;
   - two voices, one male and one female.
   Also measure two concurrent streams, since that's how turns render.
3. **Render WAVs for the owner to listen to**, since the owner's ear is the final judge. Include matched lines from current Leo/Jess for comparison.
4. **Report back briefly:** a table plus the WAVs, and a recommendation.
5. **Only after the owner picks:**
   - wrap the winner behind the same `/tts` / `/abort` / `/health` API;
   - add a style field if it supports one;
   - update `VOICE_PREFERENCES`, the meta converter and the tag guide;
   - purge the regenerable caches;
   - redeploy with `external_components/plair_start.bat`;
   - run `tests/smoke_test.py`.

## Working with the owner

- Keep replies short (1–4 lines).
- Talk through the plan before big changes.
- Fix root causes, not band-aids.
- Don't level individual clips.
- Check `data/logs/radio.log` and `data/logs/tts_server.log` after every test.
- Commit on `main` and push when done.

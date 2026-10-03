"""Make short radio drops (the sounds as the hosts or the station's computer voice cut in and out of the talk).

A batch lives in BLIPS_DIR/candidates/<batch>/. `batch <batch> [count] [takes]` runs prompts, render and page in one
go (24 prompts x 1 take is about 11 minutes). The steps, in order:

  prompts <batch> [count]  DeepSeek writes <count> Stable Audio prompts to prompts.json (LLM_BACKGROUND, one call)
  render <batch> [takes]   Stable Audio Open renders every prompt in prompts.json, trims each take to the sound
                           (leading and trailing silence cut, short tail fade), peak-normalises it and writes FLAC plus
                           manifest.json (P6000, about 27 s per take)
  page <batch>             builds picker.html plus sounds/*.mp3: every drop alone and around a host or computer-voice
                           line, with Keep / Skip and In / Out / Either; publish the page as an artifact with the db
                           capability and the mp3s as its files (sounds/<name>), and the picks save to its `picks`
                           collection
  import <batch> <folder> [hosts|computer]
                           instead of prompts + render: takes a downloaded sound library (wav/mp3/flac/ogg/aiff, any
                           depth), trims leading and trailing silence, skips anything longer than 6 s and writes the
                           batch's FLACs and manifest.json; then run page as usual
  install <batch> <picks>  copies the kept drops (picks.json exported from that collection) into
                           BLIPS_DIR/<hosts|station>/<in|out>/; restart PLAiR to load them

Stable Audio Open 1.0: Stability AI Community License. It uses the deterministic EDM DPM-Solver with the model's own
noise schedule and noise-level mapping (the stock SDE scheduler needs torchsde), and renders a 6 s window instead of
the model's full 47 s.
"""
import asyncio
import json
import math
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

MODEL = "stabilityai/stable-audio-open-1.0"
LENGTH_S = 4.0
WINDOW_FRAMES = 128
STEPS = 100
TRIM_DB = 45.0
FADE_MS = 120
MAX_S = 4.2
PEAK_DBFS = -1.0
NEGATIVE = ("dark, ominous, menacing, horror, scary, industrial, machinery, drone, rumble, distorted, singing, speech, "
            "voice, words, long reverb, low quality, hiss")
VOICES = ("hosts", "computer")

BRIEF = """You write text prompts for Stable Audio Open, a sound-effect generator trained on Freesound clips and their
descriptions. It responds best to plain sound-design descriptions with concrete sources and adjectives, the way a
Freesound uploader would describe a clip (e.g. "short whoosh swoosh transition, airy, fast, mono"), not to stories.

The sounds are radio stings for PLAiR, an independent internet radio station: the produced sound-design imaging a
station plays the moment a presenter starts or stops talking. A radio sting is 2 to 4 seconds of many things happening
at once that morph into each other: radio static and tuning noise, electronic synth swells and sweeps, filtered noise
risers, glitches and stutters, whooshes, tape and vinyl movement, pitch bends, short echoes, all layered into one dense,
evolving texture with a clear shape. It is never a single hit, click, pop or blip, and never a plain "sound then
impact". Each prompt names three or four layers sounding together and how the whole thing morphs over its length
(swells, sweeps, tunes in, glitches, resolves). For coming in, a sting builds and resolves; for going out, it opens up
and dissolves away.

Two kinds of talker:
- "hosts": two human DJs, Leo and Jess. Warm, energetic, playful radio imaging: analog synth sweeps, AM static and dial
  tuning, tape stops and rewinds, vinyl, whooshes, filtered noise risers, stutter echoes.
- "computer": the station's computer voice that reads time checks and station IDs. Cooler digital broadcast imaging:
  data bursts, granular glitch textures, digital tuning sweeps, bitcrushed noise, modem-like chirps morphing with
  clean synth tones. Never cartoon sci-fi lasers.

Write {count} prompts: {hosts} for "hosts" and {computer} for "computer". Make them varied (different mixtures of
layers, textures and movements, and different wording and sentence shapes; don't end them all the same way) and each
concrete, under 35 words. Wide stereo is welcome. Avoid melodies, chord progressions, beats, words,
speech, long reverb tails and anything that sounds like a video game.

Reply with JSON only: {{"prompts": [{{"for": "hosts" or "computer", "prompt": "..."}}]}}"""

TASTE = """
The station owner has listened to earlier batches. Prompts whose sounds they kept:
{kept}
Prompts whose sounds they skipped:
{skipped}
Learn from this: write new prompts in the spirit of the kept ones, avoid what the skipped ones have in common, and
don't repeat any prompt above word for word."""


def past_verdicts(out: Path) -> str:
    kept, skipped = [], []
    for picks_path in sorted(out.parent.glob("*/picks.json")):
        if picks_path.parent == out:
            continue
        prompts = {entry["file"]: entry for entry in
                   json.loads((picks_path.parent / "manifest.json").read_text(encoding="utf-8"))}
        for pick in json.loads(picks_path.read_text(encoding="utf-8")):
            entry = prompts.get(pick.get("file"))
            if entry and pick.get("verdict") in ("keep", "skip"):
                (kept if pick["verdict"] == "keep" else skipped).append(f'- [{entry["for"]}] {entry["prompt"]}')
    if not kept and not skipped:
        return ""
    return TASTE.format(kept="\n".join(kept) or "- none yet", skipped="\n".join(skipped) or "- none yet")


async def write_prompts(out: Path, count: int):
    from services.llm_router import LLM_BACKGROUND, generate, parse_llm_json
    computer = max(1, count // 3)
    brief = BRIEF.format(count=count, hosts=count - computer, computer=computer) + past_verdicts(out)
    result = await generate(spec=LLM_BACKGROUND, prompt=brief, temperature=1.0, max_tokens=3000, json_mode=True,
                            task="radio drop prompts")
    prompts = [p for p in parse_llm_json(result["text"], {"prompts": []}).get("prompts", [])
               if p.get("for") in VOICES and str(p.get("prompt") or "").strip()]
    (out / "prompts.json").write_text(json.dumps(prompts, indent=1), encoding="utf-8")
    print(f"{len(prompts)} prompts from {result.get('provider')}:{result.get('model')} -> {out / 'prompts.json'}")
    for number, p in enumerate(prompts, 1):
        print(f"{number:2d} [{p['for']}] {p['prompt']}")


def trimmed(audio: np.ndarray, rate: int, max_s: float = MAX_S) -> np.ndarray:
    mono = np.abs(audio).mean(axis=1)
    hop = rate // 100
    frames = mono[:len(mono) // hop * hop].reshape(-1, hop).mean(axis=1)
    level = 20 * np.log10(frames + 1e-9)
    loud = np.nonzero(level >= level.max() - TRIM_DB)[0]
    start, end = (loud[0] * hop, (loud[-1] + 1) * hop) if loud.size else (0, len(audio))
    clip = audio[start:min(end, start + int(max_s * rate))].copy()
    fade = min(len(clip), int(FADE_MS * rate / 1000))
    if fade:
        clip[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]
    peak = np.abs(clip).max()
    return clip * (10 ** (PEAK_DBFS / 20) / peak) if peak > 0 else clip


def render(out: Path, takes: int):
    import soundfile as sf
    import torch
    from diffusers import (AutoencoderOobleck, EDMDPMSolverMultistepScheduler, StableAudioDiTModel,
                           StableAudioPipeline)
    from diffusers.pipelines.stable_audio import StableAudioProjectionModel
    from transformers import T5EncoderModel, T5TokenizerFast

    class CosineNoiseScheduler(EDMDPMSolverMultistepScheduler):
        def precondition_noise(self, sigma):
            if not isinstance(sigma, torch.Tensor):
                sigma = torch.tensor([sigma])
            return sigma.atan() / math.pi * 2

    prompts = json.loads((out / "prompts.json").read_text(encoding="utf-8"))
    scheduler = CosineNoiseScheduler(sigma_min=0.3, sigma_max=500, sigma_data=1.0, sigma_schedule="exponential",
                                     prediction_type="v_prediction", solver_order=2, solver_type="midpoint")
    pipe = StableAudioPipeline(
        vae=AutoencoderOobleck.from_pretrained(MODEL, subfolder="vae"),
        text_encoder=T5EncoderModel.from_pretrained(MODEL, subfolder="text_encoder"),
        projection_model=StableAudioProjectionModel.from_pretrained(MODEL, subfolder="projection_model"),
        tokenizer=T5TokenizerFast.from_pretrained(MODEL, subfolder="tokenizer"),
        transformer=StableAudioDiTModel.from_pretrained(MODEL, subfolder="transformer"),
        scheduler=scheduler,
    ).to("cuda")
    pipe.transformer.register_to_config(sample_size=WINDOW_FRAMES)
    rate = pipe.vae.sampling_rate
    manifest = []
    for number, entry in enumerate(prompts, 1):
        slug = re.sub(r"[^a-z0-9]+", "_", entry["prompt"].lower())[:36].strip("_")
        for take in range(1, takes + 1):
            generator = torch.Generator("cuda").manual_seed(number * 100 + take)
            started = time.perf_counter()
            audio = pipe(entry["prompt"], negative_prompt=NEGATIVE, num_inference_steps=STEPS,
                         audio_end_in_s=LENGTH_S, generator=generator).audios[0]
            clip = trimmed(audio.T.float().cpu().numpy(), rate)
            name = f"{number:02d}_{take}_{entry['for']}_{slug}.flac"
            sf.write(out / name, clip, rate)
            manifest.append({"code": f"{number}.{take}", "for": entry["for"], "prompt": entry["prompt"],
                             "file": name, "seconds": round(len(clip) / rate, 2)})
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            print(f"{name}  {len(clip) / rate:.2f}s  rendered in {time.perf_counter() - started:.1f}s", flush=True)


def _mp3(audio, out: Path, name: str) -> str:
    folder = out / "sounds"
    folder.mkdir(exist_ok=True)
    audio.set_channels(1).set_frame_rate(44100).export(folder / name, format="mp3", bitrate="128k")
    return f"sounds/{name}"


def _sample_line(pattern: str, shortest_ms: int, longest_ms: int):
    import glob
    import random
    from pydub import AudioSegment
    files = sorted(glob.glob(pattern))
    random.Random(7).shuffle(files)
    for path in files:
        audio = AudioSegment.from_file(path)
        if shortest_ms < len(audio) < longest_ms:
            return audio
    raise SystemExit(f"No sample line found in {pattern}")


def build_page(out: Path):
    import html
    from pydub import AudioSegment
    from config.settings import settings
    from services_radio import station_blips
    engine = settings.TTS_ENGINE_DATA_DIR
    lines = {"hosts": _sample_line(str(engine / "tts_audio" / "leo" / "*.flac"), 2500, 4200),
             "computer": _sample_line(str(engine / "station_audio" / "station" / "station_id_*.flac"), 1500, 4000)}
    labels = {"hosts": "Hosts", "computer": "Computer voice"}
    cards = []
    for entry in json.loads((out / "manifest.json").read_text(encoding="utf-8")):
        raw = AudioSegment.from_file(out / entry["file"]).set_sample_width(2)
        blip = station_blips.as_blip(raw)
        if blip is None:
            continue
        opened, _voice_at = station_blips.mix_in(blip, lines[entry["for"]])
        demo = station_blips.mix_out(opened, blip)
        code = html.escape(entry["code"])
        doc_id = "d" + code.replace(".", "-")
        sound_src = _mp3(blip.audio, out, f"{doc_id}-sound.mp3")
        line_src = _mp3(demo, out, f"{doc_id}-line.mp3")
        cards.append(
            f'<li class="drop" data-id="{doc_id}" data-code="{code}" data-for="{entry["for"]}" '
            f'data-file="{html.escape(entry["file"])}">'
            f'<div class="head"><span class="code">{code}</span><span class="tag">{labels[entry["for"]]}</span>'
            f'<span class="len">{entry["seconds"]:.2f} s</span></div>'
            f'<p class="prompt">{html.escape(entry["prompt"])}</p>'
            f'<div class="players"><label>Sound<audio controls preload="none" src="{sound_src}"></audio>'
            f'</label><label>Around a line<audio controls preload="none" src="{line_src}"></audio></label></div>'
            f'<div class="verdict" role="group" aria-label="Verdict for {code}">'
            f'<button type="button" data-verdict="keep">Keep</button>'
            f'<button type="button" data-verdict="skip">Skip</button><span class="sep"></span>'
            f'<button type="button" data-role="in">In</button><button type="button" data-role="out">Out</button>'
            f'<button type="button" data-role="either">Either</button></div></li>')
    template = (Path(__file__).with_name("radio_drops_picker.html")).read_text(encoding="utf-8")
    page = out / "picker.html"
    template = template.replace("<title>PLAiR Drop Picker</title>", f"<title>PLAiR Drops {out.name}</title>")
    page.write_text(template.replace("<!--DROPS-->", "\n".join(cards)), encoding="utf-8")
    print(f"{page} ({page.stat().st_size / 1e6:.1f} MB, {len(cards)} drops)")


def install(out: Path, picks_path: Path):
    import shutil
    from config.settings import settings
    folders = {"hosts": "hosts", "computer": "station"}
    edges = {"in": ("in",), "out": ("out",), "either": ("in", "out")}
    installed = 0
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    (out / "picks.json").write_text(json.dumps(picks, indent=1), encoding="utf-8")
    for pick in picks:
        if pick.get("verdict") != "keep" or pick.get("for") not in folders:
            continue
        for edge in edges.get(pick.get("role") or "either", ("in", "out")):
            target = settings.BLIPS_DIR / folders[pick["for"]] / edge
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out / pick["file"], target / f"{out.name}_{pick['file']}")
            installed += 1
    print(f"Installed {installed} drop file(s) into {settings.BLIPS_DIR}; restart PLAiR to load them")


IMPORT_MAX_S = 6.0
IMPORT_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}


def import_library(out: Path, source: Path, voice: str):
    import soundfile as sf
    from pydub import AudioSegment
    manifest = []
    files = sorted(p for p in source.rglob("*") if p.suffix.lower() in IMPORT_EXTENSIONS
                   and not p.name.startswith("._") and "__MACOSX" not in p.parts)
    for number, path in enumerate(files, 1):
        audio = AudioSegment.from_file(path).set_sample_width(2)
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32).reshape(-1, audio.channels) / 32768.0
        clip = trimmed(samples, audio.frame_rate, max_s=IMPORT_MAX_S + 1)
        seconds = len(clip) / audio.frame_rate
        if seconds > IMPORT_MAX_S:
            print(f"skipped {path.name} ({seconds:.1f}s, longer than {IMPORT_MAX_S:.0f}s)")
            continue
        name = f"{number:02d}_1_{voice}_{re.sub(r'[^a-z0-9]+', '_', path.stem.lower())[:36].strip('_')}.flac"
        sf.write(out / name, clip, audio.frame_rate)
        manifest.append({"code": f"{number}.1", "for": voice, "prompt": f"{path.stem} ({source.name})",
                         "file": name, "seconds": round(seconds, 2)})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"Imported {len(manifest)} sounds from {source} into {out}")


def batch_dir(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or len(path.parts) > 1:
        return path
    from config.settings import settings
    return settings.BLIPS_DIR / "candidates" / name


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    mode, out = sys.argv[1], batch_dir(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if mode == "prompts":
        asyncio.run(write_prompts(out, int(sys.argv[3]) if len(sys.argv) > 3 else 24))
    elif mode == "render":
        render(out, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif mode == "page":
        build_page(out)
    elif mode == "batch":
        asyncio.run(write_prompts(out, int(sys.argv[3]) if len(sys.argv) > 3 else 24))
        render(out, int(sys.argv[4]) if len(sys.argv) > 4 else 1)
        build_page(out)
    elif mode == "import" and len(sys.argv) > 3:
        import_library(out, Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "hosts")
    elif mode == "install" and len(sys.argv) > 3:
        install(out, Path(sys.argv[3]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()

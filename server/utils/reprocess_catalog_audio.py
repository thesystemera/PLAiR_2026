import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-process catalog audio through the headroom-safe chain. Dry-run by default: "
                    "nothing in the catalog changes unless --apply is given. Resumable via a state file."
    )
    parser.add_argument("track_ids", nargs="*", help="Track ids (same as --track-ids)")
    parser.add_argument("--track-ids", nargs="+", dest="track_ids_opt", default=[], help="Track ids to process")
    parser.add_argument("--ids-file", type=Path, help="Text file with one track id per line (first token is used)")
    parser.add_argument("--all", action="store_true", help="Process every catalog track that has metadata")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N pending tracks this run")
    parser.add_argument("--from", dest="from_stage", choices=["auto", "apollo", "sonic", "master"], default="auto",
                        help="apollo: full chain from the Suno MP3 (GPU); sonic: SonicMaster from the vocal mix (GPU); "
                             "master: re-master the existing SonicMaster WAV (CPU); auto: apollo with --gpu, else master")
    parser.add_argument("--gpu", action="store_true",
                        help="Allow GPU stages on the Quadro P6000 (CUDA_DEVICE_ORDER=PCI_BUS_ID, CUDA_VISIBLE_DEVICES=0)")
    parser.add_argument("--apply", action="store_true", help="Replace catalog files; originals are moved to the backup folder")
    parser.add_argument("--dry-run", action="store_true", help="Plan and estimate only (default unless --apply)")
    parser.add_argument("--preview", action="store_true",
                        help="Dry-run that also renders into the staging folder and reports before/after numbers")
    parser.add_argument("--state", type=Path, help="State file (default: data/reprocess_catalog_state.json)")
    parser.add_argument("--backup-dir", type=Path, help="Backup folder (default: <catalog>/_audio_backups/reprocess_<timestamp>)")
    parser.add_argument("--retry-failed", action="store_true", help="Retry tracks marked failed in the state file")
    parser.add_argument("--retry-skipped", action="store_true", help="Retry tracks marked skipped in the state file")
    parser.add_argument("--pause", type=float, default=5.0, help="Seconds to rest between tracks")
    parser.add_argument("--min-free-vram-gb", type=float, default=6.0, help="Wait until the P6000 has this much free VRAM")
    parser.add_argument("--min-free-disk-gb", type=float, default=100.0, help="Stop when the catalog drive has less free space")
    parser.add_argument("--cpu-threads", type=int, default=4, help="Torch/numpy CPU threads")
    parser.add_argument("--restore", type=Path, help="Restore every file listed in <backup-dir>/manifest.json and exit")
    parser.add_argument("--skip-apollo", action="store_true",
                        help="Same as BANDWIDTH_STAGE=off for this run: the Suno MP3 is decoded straight to float WAV")
    parser.add_argument("--ab-set", type=int, default=0,
                        help="Render N tracks in several chain variants to --ab-dir for listening (catalog untouched)")
    parser.add_argument("--ab-dir", type=Path, help="A/B output folder (default: data/reprocess_ab_<timestamp>)")
    parser.add_argument("--ab-variants", nargs="+",
                        help="Subset of A/B variants to render (default: all; GPU variants need --gpu)")
    parser.add_argument("--score", action="store_true",
                        help="Score rendered masters with Audiobox Aesthetics when that package is installed")
    args = parser.parse_args()
    args.track_ids = list(args.track_ids) + list(args.track_ids_opt)
    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")
    return args


ARGS = parse_args()
if ARGS.gpu:
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
else:
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[_var] = str(ARGS.cpu_threads)

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np  # noqa: E402
import pyloudnorm as pyln  # noqa: E402
import soundfile as sf  # noqa: E402

from config import settings  # noqa: E402
from services import track_asset_stages as stages  # noqa: E402
from services.audio_headroom import TRUE_PEAK_CEILING_DBTP, linear_to_db, mix_stems_to_file, true_peak  # noqa: E402
from services.audio_master_service import AudioMasterService, MASTER_TARGET_LUFS  # noqa: E402
from services.audio_transcoding_service import AudioTranscodingService  # noqa: E402

DURATION_TOLERANCE_S = 0.5
SUNO_MASTER_WET = 1.0
LOUD_SOURCE_LUFS = -12.0
LOUD_SOURCE_MASTERING_BLEND_MAX = 35
DEFAULT_STATE_PATH = settings.LOGS_DIR.parent / "reprocess_catalog_state.json"
LEGACY_SONIC = {"precision": "fp16", "steps": 50, "align": False, "conditioning": False, "rms_match": True,
                "templates": False, "prompt": "give the mix more shine and sparkle, clean and dynamic with rich full harmonics"}
AB_VARIANTS = {
    "current": {"description": "Current catalog master, unchanged", "copy_current": True},
    "fixed_master": {"description": "Existing SonicMaster WAV through the fixed mastering chain (CPU)", "from": "master"},
    "full_default": {"description": "Full chain with the new defaults (fp32, 20 steps, aligned + conditioned chunks, "
                                    "template prompt, compensated blend, true-peak master)", "from": "apollo"},
    "owner_prompt": {"description": "New defaults with the original free-text SonicMaster prompt (no template)",
                     "from": "apollo", "sonic": {"templates": False, "prompt": LEGACY_SONIC["prompt"]}},
    "steps_10": {"description": "New defaults with 10 SonicMaster steps", "from": "apollo", "sonic": {"steps": 10}},
    "steps_50": {"description": "New defaults with 50 SonicMaster steps", "from": "apollo", "sonic": {"steps": 50}},
    "legacy_sonic": {"description": "Old SonicMaster settings (fp16, 50 steps, old prompt, per-chunk RMS match) with the "
                                    "fixed float/blend/master chain", "from": "apollo", "sonic": LEGACY_SONIC},
    "bandwidth_off": {"description": "New defaults without a bandwidth-extension stage", "from": "apollo", "bandwidth": "off"},
    "bandwidth_flashsr": {"description": "New defaults with FlashSR bandwidth extension", "from": "apollo", "bandwidth": "flashsr"},
    "separation_roformer": {"description": "New defaults with RoFormer vocal separation", "from": "apollo",
                            "separation": "roformer"},
}
SECONDS_PER_AUDIO_SECOND = {"apollo": 0.35, "demucs": 0.15, "clearvoice": 0.4, "sonic": 0.9, "master": 0.06, "transcode": 0.05}
BACKUP_BYTES_PER_AUDIO_SECOND = {"outputs": 0.25e6, "apollo": 0.18e6, "stems": 1.06e6, "mix": 0.35e6, "sonic": 0.18e6}
GROWTH_BYTES_PER_AUDIO_SECOND = {"apollo": 0.18e6, "sonic": 0.18e6}


def score_audio(path: Path) -> Optional[Dict[str, float]]:
    try:
        from audiobox_aesthetics.infer import initialize_predictor
    except Exception:
        return None
    predictor = initialize_predictor()
    result = predictor.forward([{"path": str(path)}])
    return result[0] if result else None


def lower_priority():
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except Exception:
        pass


def wav_info(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        info = sf.info(str(path))
    except Exception:
        return None
    return {"rate": info.samplerate, "frames": info.frames, "duration": info.duration, "subtype": info.subtype}


def measure(path: Path) -> Dict[str, Any]:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    loudness = float(pyln.Meter(rate).integrated_loudness(data.astype(np.float64)))
    return {
        "duration": round(data.shape[0] / rate, 2),
        "sample_peak_db": round(linear_to_db(peak), 2),
        "true_peak_db": round(linear_to_db(true_peak(data.T)), 2),
        "lufs": round(loudness, 2) if np.isfinite(loudness) else None,
        "full_scale_samples": int(np.sum(np.abs(data) >= 32767 / 32768)),
    }


def load_metadata(track_id: str) -> Dict[str, Any]:
    try:
        return json.loads(stages.metadata_path(track_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def matches(info: Optional[Dict[str, Any]], reference: Optional[float]) -> bool:
    if info is None:
        return False
    if reference is None:
        return True
    return abs(info["duration"] - reference) <= DURATION_TOLERANCE_S


def plan_track(track_id: str, plan_stage_override: Optional[str] = None) -> Dict[str, Any]:
    metadata = load_metadata(track_id)
    is_upload = metadata.get("uploaded_by_user_id") is not None
    stems_dir = settings.DEMUCS_STEMS_DIR / track_id
    paths = {
        "source_mp3": stages.catalog_mp3_path(track_id),
        "apollo": settings.WAV_DIR / f"{track_id}.wav",
        "stems_dir": stems_dir,
        "vocal_stem": stages.demucs_vocal_stem(track_id),
        "no_vocals": stems_dir / "no_vocals.wav",
        "vocal_mix": settings.VOCAL_ENHANCED_WAV_DIR / f"{track_id}.wav",
        "sonic": settings.SONIC_WAV_DIR / f"{track_id}.wav",
        "master": stages.master_wav_path(track_id),
        "original": stages.uploaded_original_path(track_id, metadata) if is_upload else None,
    }
    infos = {key: wav_info(paths[key]) for key in ("apollo", "vocal_stem", "no_vocals", "vocal_mix", "sonic", "master")}
    reference = infos["apollo"]["duration"] if infos["apollo"] else None
    if reference is None and (metadata.get("track_info") or {}).get("duration"):
        reference = float(metadata["track_info"]["duration"]) / 1000.0

    plan: Dict[str, Any] = {
        "track_id": track_id,
        "title": (metadata.get("generation_params") or {}).get("title"),
        "is_upload": is_upload,
        "instrumental": stages.track_is_instrumental(metadata),
        "reference_duration": reference,
        "paths": paths,
        "infos": infos,
        "problems": [],
        "steps": [],
        "wet_mix": SUNO_MASTER_WET,
        "sonic_prompt": None,
        "sonic_wet": None,
    }
    if not metadata:
        plan["blocked"] = "metadata missing"
        return plan

    mix_ok = matches(infos["vocal_mix"], reference) and (infos["vocal_mix"] or {}).get("rate") == 44100
    sonic_ok = matches(infos["sonic"], reference)
    stems_ok = infos["vocal_stem"] is not None and infos["no_vocals"] is not None
    if infos["vocal_mix"] and not mix_ok:
        plan["problems"].append(
            f"vocal mix is {infos['vocal_mix']['duration']:.2f}s @ {infos['vocal_mix']['rate']}Hz (expected {reference}s @ 44100Hz)")
    if infos["sonic"] and not sonic_ok:
        plan["problems"].append(f"SonicMaster WAV is {infos['sonic']['duration']:.2f}s (expected {reference}s)")
    if infos["master"] and not matches(infos["master"], reference):
        plan["problems"].append(f"master WAV is {infos['master']['duration']:.2f}s (expected {reference}s)")

    if is_upload:
        plan["stage"] = "original"
        plan["wet_mix"] = float(metadata.get("mastering_blend_used", metadata.get("mastering_blend", 70))) / 100.0
        if paths["original"] is None:
            plan["blocked"] = "uploaded original file not found"
            return plan
        plan["steps"].append("decode")
        if ARGS.gpu and metadata.get("sonic_master_applied") and metadata.get("sonic_master_prompt"):
            plan["sonic_prompt"] = metadata["sonic_master_prompt"]
            plan["sonic_wet"] = float(metadata.get("sonic_master_blend_used", metadata.get("sonic_master_blend", 0))) / 100.0
            plan["steps"].append("sonic")
        plan["steps"] += ["master", "transcode"]
        return plan

    stage = ARGS.from_stage if plan_stage_override is None else plan_stage_override
    if stage == "auto":
        stage = "apollo" if ARGS.gpu else "master"
    if stage == "master" and not sonic_ok:
        stage = "sonic"
    plan["stage"] = stage

    if stage == "apollo":
        if not paths["source_mp3"].exists():
            plan["blocked"] = "Suno source MP3 missing"
            return plan
        plan["steps"].append("apollo")
        if not plan["instrumental"] and settings.ENABLE_VOCAL_ENHANCEMENT:
            plan["steps"] += ["demucs", "clearvoice", "mix"]
        plan["steps"] += ["sonic", "master", "transcode"]
    elif stage == "sonic":
        if mix_ok:
            plan["sonic_input"] = "vocal_mix"
        elif stems_ok and not plan["instrumental"]:
            plan["sonic_input"] = "rebuilt_mix"
            plan["steps"].append("mix")
        elif infos["apollo"] and matches(infos["apollo"], reference):
            plan["sonic_input"] = "apollo"
        else:
            plan["blocked"] = "no intact SonicMaster input (vocal mix, stems or Apollo WAV)"
            return plan
        plan["steps"] += ["sonic", "master", "transcode"]
    else:
        plan["steps"] += ["master", "transcode"]

    if any(step in ("apollo", "demucs", "clearvoice", "sonic") for step in plan["steps"]) and not ARGS.gpu:
        plan["blocked"] = f"stage '{stage}' needs the GPU: add --gpu"
    return plan


def estimate_seconds(plan: Dict[str, Any], learned: Optional[float]) -> float:
    audio_seconds = plan["reference_duration"] or 200.0
    if learned is not None:
        return learned * audio_seconds
    return sum(SECONDS_PER_AUDIO_SECOND.get(step, 0.0) for step in plan["steps"]) * audio_seconds + ARGS.pause


def estimate_disk_bytes(plan: Dict[str, Any]) -> float:
    audio_seconds = plan["reference_duration"] or 200.0
    total = BACKUP_BYTES_PER_AUDIO_SECOND["outputs"]
    for step, key in (("apollo", "apollo"), ("demucs", "stems"), ("mix", "mix"), ("sonic", "sonic")):
        if step in plan["steps"]:
            total += BACKUP_BYTES_PER_AUDIO_SECOND[key] + GROWTH_BYTES_PER_AUDIO_SECOND.get(key, 0.0)
    return total * audio_seconds


def live_outputs(plan: Dict[str, Any]) -> Dict[str, Path]:
    track_id = plan["track_id"]
    outputs = {"master": stages.master_wav_path(track_id)}
    for bitrate in stages.OPUS_BITRATES:
        outputs[f"opus_{bitrate}"] = stages.opus_path(track_id, bitrate)
        outputs[f"webm_{bitrate}"] = stages.webm_path(track_id, bitrate)
    if plan["is_upload"]:
        outputs["catalog_mp3"] = stages.catalog_mp3_path(track_id)
    else:
        outputs["apollo"] = plan["paths"]["apollo"]
        outputs["stems_dir"] = plan["paths"]["stems_dir"]
        outputs["vocal_mix"] = plan["paths"]["vocal_mix"]
        outputs["sonic"] = plan["paths"]["sonic"]
    return outputs


class Engines:

    def __init__(self):
        self.master = AudioMasterService()
        self.transcoding: Optional[AudioTranscodingService] = None
        self.stage_services: Dict[str, Any] = {}
        self.clearvoice = None
        self.sonic = None

    async def ensure_transcoding(self) -> AudioTranscodingService:
        if self.transcoding is None:
            self.transcoding = AudioTranscodingService()
            await self.transcoding.initialize()
            if not self.transcoding.ffmpeg_available:
                raise SystemExit("ffmpeg is not available")
        return self.transcoding

    def stage(self, kind: str, name: str):
        return self.stage_services.get(f"{kind}:{name}")

    async def ensure_gpu(self, steps: List[str], bandwidth: str, separation: str):
        import torch
        from models_global import gpu_lease
        from services.audio_stage_registry import create_stage_service
        if not torch.cuda.is_available():
            raise SystemExit("--gpu given but CUDA is not available")
        name = torch.cuda.get_device_name(0)
        if "P6000" not in name:
            raise SystemExit(f"Refusing to run on {name}; expected the Quadro P6000")
        torch.set_num_threads(ARGS.cpu_threads)
        wanted = []
        if "apollo" in steps and bandwidth != "off":
            wanted.append(("bandwidth", bandwidth))
        if "demucs" in steps:
            wanted.append(("separation", separation))
        async with gpu_lease("Reprocess load"):
            for kind, stage_name in wanted:
                key = f"{kind}:{stage_name}"
                if key not in self.stage_services:
                    service = create_stage_service(kind, stage_name)
                    if service is None:
                        raise RuntimeError(f"{kind} stage '{stage_name}' is not available yet")
                    await service.initialize()
                    self.stage_services[key] = service
            if "clearvoice" in steps and self.clearvoice is None:
                from services.audio_clearvoice_service import AudioClearVoiceService
                self.clearvoice = AudioClearVoiceService()
                await self.clearvoice.initialize()
        if "sonic" in steps and self.sonic is None:
            from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
            self.sonic = SonicMasterService()
            self.sonic.configure(**SUNO_SONIC_SETTINGS)
            await self.sonic.initialize()

    @staticmethod
    async def wait_for_vram():
        import torch
        while True:
            free, _total = torch.cuda.mem_get_info()
            if free / 1e9 >= ARGS.min_free_vram_gb:
                return
            print(f"  waiting for VRAM: {free / 1e9:.1f} GB free < {ARGS.min_free_vram_gb} GB")
            await asyncio.sleep(30)


async def render(plan: Dict[str, Any], staging: Path, engines: Engines,
                 variant: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    variant = variant or {}
    paths = plan["paths"]
    steps = plan["steps"]
    bandwidth = variant.get("bandwidth", "off" if ARGS.skip_apollo else settings.BANDWIDTH_STAGE)
    separation = variant.get("separation", settings.SEPARATION_MODEL)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    staged: Dict[str, Path] = {}
    transcoding = await engines.ensure_transcoding()
    gpu_steps = [s for s in steps if s in ("apollo", "demucs", "clearvoice", "sonic") and not (s == "apollo" and bandwidth == "off")]
    if gpu_steps:
        await engines.ensure_gpu(gpu_steps, bandwidth, separation)
        await engines.wait_for_vram()
    source: Optional[Path] = None

    if "decode" in steps:
        source = staging / "source.wav"
        probe = await transcoding.probe_media(paths["original"]) or {}
        rate = "48000" if (probe.get("sample_rate") or 44100) > 44100 else "44100"
        if not await transcoding.extract_audio_to_wav(paths["original"], source, sample_rate=rate, codec="pcm_f32le"):
            raise RuntimeError("could not decode the uploaded original")

    if "apollo" in steps:
        staged["apollo"] = staging / "apollo.wav"
        if bandwidth == "off":
            if not await transcoding.extract_audio_to_wav(paths["source_mp3"], staged["apollo"], sample_rate="44100",
                                                          codec="pcm_f32le"):
                raise RuntimeError("could not decode the Suno MP3")
        elif not await engines.stage("bandwidth", bandwidth).process_audio(paths["source_mp3"], staged["apollo"]):
            raise RuntimeError(f"bandwidth stage '{bandwidth}' failed")
        source = staged["apollo"]

    if "demucs" in steps:
        staged["stems_dir"] = staging / "stems"
        stems = await engines.stage("separation", separation).separate_stems(source, staged["stems_dir"])
        if not stems:
            raise RuntimeError("Demucs failed")
        enhanced = await engines.clearvoice.enhance_vocals(stems["vocals"], staged["stems_dir"] / "vocals_enhanced.wav")
        vocal_source = enhanced if enhanced else stems["vocals"]
        staged["vocal_mix"] = staging / "vocal_mix.wav"
        mix_stems_to_file(vocal_source, stems["no_vocals"], staged["vocal_mix"])
        source = staged["vocal_mix"]
    elif "mix" in steps:
        staged["vocal_mix"] = staging / "vocal_mix.wav"
        mix_stems_to_file(paths["vocal_stem"], paths["no_vocals"], staged["vocal_mix"])
        source = staged["vocal_mix"]

    if "sonic" in steps:
        if source is None:
            source = paths["vocal_mix"] if plan.get("sonic_input") == "vocal_mix" else paths["apollo"]
        staged["sonic"] = staging / "sonic.wav"
        restore_settings = apply_sonic_variant(engines, variant.get("sonic"))
        try:
            ok = await engines.sonic.enhance_audio(source, staged["sonic"], prompt=plan["sonic_prompt"],
                                                   wet_mix=plan["sonic_wet"])
        finally:
            restore_settings()
        if not ok:
            raise RuntimeError("SonicMaster failed")
        source = staged["sonic"]

    if source is None:
        source = paths["sonic"]

    if plan["is_upload"]:
        loudness = engines.master._analyze_loudness_sync(source)
        if np.isfinite(loudness) and loudness >= LOUD_SOURCE_LUFS:
            plan["wet_mix"] = min(plan["wet_mix"], LOUD_SOURCE_MASTERING_BLEND_MAX / 100.0)

    staged["master"] = staging / "master.wav"
    result, info = await asyncio.to_thread(
        engines.master._master_audio_sync, source, staged["master"], MASTER_TARGET_LUFS, plan["wet_mix"]
    )
    if not result:
        raise RuntimeError(f"mastering failed: {info.get('error')}")
    plan["master_info"] = {
        "output_lufs": round(info.get("output_loudness", float("nan")), 2),
        "output_true_peak_db": round(info.get("output_true_peak_db", 0.0), 2),
        "limiter_max_db": round((info.get("limiter") or {}).get("max_reduction_db", 0.0), 2),
        "loudness_trim_db": round(info.get("loudness_trim_db", 0.0), 2),
        "tonal_fixes": info.get("tonal_fixes"),
    }

    if "transcode" not in steps:
        return staged
    for bitrate in stages.OPUS_BITRATES:
        opus = staging / f"opus_{bitrate}.opus"
        webm = staging / f"webm_{bitrate}.webm"
        if not await transcoding.transcode_to_opus(staged["master"], opus, bitrate):
            raise RuntimeError(f"Opus {bitrate} encode failed")
        if not await transcoding.transcode_opus_to_webm(opus, webm):
            raise RuntimeError(f"WebM {bitrate} remux failed")
        staged[f"opus_{bitrate}"] = opus
        staged[f"webm_{bitrate}"] = webm
    if plan["is_upload"]:
        mp3 = staging / "catalog.mp3"
        if not await transcoding.convert_to_mp3(staged["master"], mp3, stages.CATALOG_MP3_BITRATE):
            raise RuntimeError("catalog MP3 encode failed")
        staged["catalog_mp3"] = mp3
    return staged


def apply_sonic_variant(engines: Engines, overrides: Optional[Dict[str, Any]]):
    if not overrides:
        return lambda: None
    from services.audio_sonic_master_service import SUNO_SONIC_SETTINGS
    flags = {
        "align": "SONIC_MASTER_ALIGN_CHUNKS",
        "conditioning": "SONIC_MASTER_CHUNK_CONDITIONING",
        "rms_match": "SONIC_MASTER_CHUNK_RMS_MATCH",
        "templates": "SONIC_MASTER_TEMPLATE_PROMPTS",
    }
    saved = {attr: getattr(settings, attr) for attr in flags.values()}
    saved_precision = engines.sonic.precision
    for key, attr in flags.items():
        if key in overrides:
            setattr(settings, attr, overrides[key])
    engines.sonic.configure(precision=overrides.get("precision", saved_precision),
                            num_inference_steps=overrides.get("steps", SUNO_SONIC_SETTINGS["num_inference_steps"]),
                            prompt=overrides.get("prompt", SUNO_SONIC_SETTINGS["prompt"]))

    def restore_settings():
        for attr, value in saved.items():
            setattr(settings, attr, value)
        engines.sonic.configure(precision=saved_precision, num_inference_steps=SUNO_SONIC_SETTINGS["num_inference_steps"],
                                prompt=SUNO_SONIC_SETTINGS["prompt"])

    return restore_settings


async def run_ab_set(ids: List[str], engines: Engines):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ab_dir = ARGS.ab_dir or (settings.LOGS_DIR.parent / f"reprocess_ab_{stamp}")
    names = ARGS.ab_variants or list(AB_VARIANTS)
    unknown = [n for n in names if n not in AB_VARIANTS]
    if unknown:
        raise SystemExit(f"Unknown A/B variants: {unknown}. Known: {list(AB_VARIANTS)}")
    candidates = [t for t in ids if not load_metadata(t).get("uploaded_by_user_id")]
    step = max(1, len(candidates) // max(ARGS.ab_set, 1))
    chosen = candidates[::step][:ARGS.ab_set] if not ARGS.track_ids else candidates[:ARGS.ab_set]
    summary = []
    for track_id in chosen:
        for name in names:
            variant = AB_VARIANTS[name]
            out_dir = ab_dir / track_id
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / f"{name}.wav"
            row: Dict[str, Any] = {"track_id": track_id, "variant": name, "description": variant["description"]}
            try:
                from services.audio_stage_registry import stage_available
                for kind in ("bandwidth", "separation"):
                    if variant.get(kind) and not stage_available(kind, variant[kind]):
                        raise RuntimeError(f"{kind} stage '{variant[kind]}' is not available yet")
                if variant.get("copy_current"):
                    shutil.copy2(stages.master_wav_path(track_id), target)
                else:
                    plan = plan_track(track_id, variant["from"])
                    plan["steps"] = [s for s in plan["steps"] if s != "transcode"]
                    if plan.get("blocked"):
                        raise RuntimeError(plan["blocked"])
                    staged = await render(plan, ab_dir / "_staging" / track_id / name, engines, variant)
                    shutil.move(str(staged["master"]), str(target))
                    shutil.rmtree(ab_dir / "_staging" / track_id / name, ignore_errors=True)
                row.update(measure(target))
                if ARGS.score:
                    row["score"] = score_audio(target)
                row["file"] = str(target)
                print(f"{track_id} {name}: {row}")
            except Exception as e:
                row["error"] = str(e)[:300]
                print(f"{track_id} {name}: SKIPPED - {e}")
            summary.append(row)
            (ab_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
            await asyncio.sleep(ARGS.pause)
    print(f"\nA/B set written to {ab_dir} (summary.json lists every file with loudness and peaks)")


def verify(plan: Dict[str, Any], staged: Dict[str, Path]) -> Dict[str, Any]:
    after = measure(staged["master"])
    reference = plan["reference_duration"]
    if reference is not None and abs(after["duration"] - reference) > DURATION_TOLERANCE_S:
        raise RuntimeError(f"new master is {after['duration']}s, expected {reference}s")
    if after["true_peak_db"] > TRUE_PEAK_CEILING_DBTP + 0.05:
        raise RuntimeError(f"new master true peak {after['true_peak_db']} dBTP exceeds the ceiling")
    return after


def swap_in(plan: Dict[str, Any], staged: Dict[str, Path], backup_root: Path, manifest: List[Dict[str, str]]):
    track_id = plan["track_id"]
    backup_dir = backup_root / track_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    for key, live in live_outputs(plan).items():
        new_file = staged.get(key)
        if new_file is None:
            continue
        entry = {"track_id": track_id, "key": key, "live": str(live), "backup": ""}
        if live.exists():
            backup = backup_dir / (key if live.is_dir() else f"{key}{live.suffix}")
            shutil.move(str(live), str(backup))
            entry["backup"] = str(backup)
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(new_file), str(live))
        manifest.append(entry)


def restore(backup_root: Path):
    entries = json.loads((backup_root / "manifest.json").read_text(encoding="utf-8"))
    quarantine = backup_root / "_replaced_by_restore"
    for entry in reversed(entries):
        live = Path(entry["live"])
        if live.exists():
            target = quarantine / entry["track_id"] / (entry["key"] if live.is_dir() else f"{entry['key']}{live.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(live), str(target))
        if entry["backup"] and Path(entry["backup"]).exists():
            shutil.move(entry["backup"], str(live))
        print(f"restored {entry['track_id']} {entry['key']}")


def read_ids() -> List[str]:
    ids = list(ARGS.track_ids)
    if ARGS.ids_file:
        for line in ARGS.ids_file.read_text(encoding="utf-8").splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if token and not token.startswith("#"):
                ids.append(token)
    if ARGS.all:
        ids += sorted(p.stem for p in settings.METADATA_DIR.glob("*.json") if stages.master_wav_path(p.stem).exists())
    seen = set()
    return [t for t in ids if not (t in seen or seen.add(t))]


def load_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(temp, path)


def is_pending(entry: Optional[Dict[str, Any]]) -> bool:
    if not entry:
        return True
    status = entry.get("status")
    if status == "failed":
        return ARGS.retry_failed
    if status == "skipped":
        return ARGS.retry_skipped
    return status != "done"


def describe(plan: Dict[str, Any]) -> str:
    title = plan.get("title") or ""
    head = f"{plan['track_id']}  {title}".rstrip()
    lines = [head, f"  steps: {' > '.join(plan['steps']) or '-'}  (master EQ wet {plan['wet_mix']:.2f})"]
    for problem in plan["problems"]:
        lines.append(f"  problem: {problem}")
    if plan.get("blocked"):
        lines.append(f"  SKIP: {plan['blocked']}")
    return "\n".join(lines)


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h{rest // 60:02d}m"


async def main():
    lower_priority()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if ARGS.restore:
        restore(ARGS.restore)
        return

    ids = read_ids()
    if not ids:
        print("No tracks selected: pass track ids, --ids-file or --all")
        return

    if ARGS.ab_set > 0:
        await run_ab_set(ids, Engines())
        return

    state_path = ARGS.state or DEFAULT_STATE_PATH
    state = load_state(state_path)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_root = ARGS.backup_dir or (settings.CATALOG_DIR / "_audio_backups" / f"reprocess_{stamp}")
    writing = ARGS.apply
    rendering = ARGS.apply or ARGS.preview

    pending = [t for t in ids if is_pending(state.get(t))]
    already = len(ids) - len(pending)
    if ARGS.limit > 0:
        pending = pending[:ARGS.limit]
    plans = [plan_track(t) for t in pending]
    runnable = [p for p in plans if not p.get("blocked")]
    skipped = [p for p in plans if p.get("blocked")]

    learned = state.get("_stats", {}).get("seconds_per_audio_second")
    total_seconds = sum(estimate_seconds(p, learned) for p in runnable)
    backup_bytes = sum(estimate_disk_bytes(p) for p in runnable)
    free_bytes = shutil.disk_usage(settings.CATALOG_DIR).free

    verbose = len(plans) <= 50 or not writing
    for plan in plans:
        if verbose or plan.get("blocked") or plan["problems"]:
            print(describe(plan))
    print(f"\nSelected {len(ids)} tracks: {already} already handled in {state_path.name}, {len(pending)} pending this run")
    print(f"Runnable {len(runnable)}, skipped {len(skipped)} (missing or damaged intermediates, or GPU needed)")
    print(f"Estimated time: {format_duration(total_seconds)} "
          f"({'learned rate' if learned else 'rough default rates'}, pause {ARGS.pause:.0f}s per track)")
    print(f"Estimated extra disk use (backups + larger float intermediates): {backup_bytes / 1e9:.1f} GB, "
          f"free on catalog drive: {free_bytes / 1e9:.1f} GB")
    if writing and backup_bytes > free_bytes - ARGS.min_free_disk_gb * 1e9:
        print("Not enough free space for this batch: use --limit to run in smaller batches and prune checked backups.")
        return

    if not rendering:
        print("\nDry run: nothing rendered or changed. Add --preview to render into the staging folder, --apply to replace files.")
        return

    engines = Engines()
    manifest_path = backup_root / "manifest.json"
    manifest: List[Dict[str, str]] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stats = state.setdefault("_stats", {"seconds_per_audio_second": None, "tracks_timed": 0})

    for plan in skipped:
        if writing:
            state[plan["track_id"]] = {"status": "skipped", "reason": plan["blocked"], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if writing:
        save_state(state_path, state)

    for index, plan in enumerate(runnable, start=1):
        track_id = plan["track_id"]
        if writing and shutil.disk_usage(settings.CATALOG_DIR).free / 1e9 < ARGS.min_free_disk_gb:
            print(f"Stopping: less than {ARGS.min_free_disk_gb} GB free on the catalog drive")
            break
        staging = backup_root / "_staging" / track_id
        started = time.monotonic()
        before = measure(plan["paths"]["master"]) if plan["infos"].get("master") else None
        try:
            staged = await render(plan, staging, engines)
            after = verify(plan, staged)
            elapsed = time.monotonic() - started
            record = {"status": "done" if writing else "previewed", "steps": plan["steps"], "seconds": round(elapsed, 1),
                      "before": before, "after": after, "master": plan["master_info"],
                      "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            if writing:
                swap_in(plan, staged, backup_root, manifest)
                manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
                shutil.rmtree(staging, ignore_errors=True)
                record["backup_dir"] = str(backup_root / track_id)
            audio_seconds = plan["reference_duration"] or after["duration"]
            rate = elapsed / max(audio_seconds, 1.0)
            count = stats.get("tracks_timed", 0)
            previous = stats.get("seconds_per_audio_second") or rate
            stats["seconds_per_audio_second"] = (previous * count + rate) / (count + 1)
            stats["tracks_timed"] = count + 1
            remaining = sum(estimate_seconds(p, stats["seconds_per_audio_second"]) for p in runnable[index:])
            print(f"[{index}/{len(runnable)}] {track_id} {plan.get('title') or ''}: {elapsed:.0f}s | "
                  f"before {before} | after {after} | ETA {format_duration(remaining)}")
        except Exception as e:
            record = {"status": "failed", "reason": str(e)[:300], "steps": plan["steps"], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            print(f"[{index}/{len(runnable)}] {track_id}: FAILED - {e}")
        if writing:
            state[track_id] = record
            save_state(state_path, state)
        if index < len(runnable):
            await asyncio.sleep(ARGS.pause)

    if writing:
        print(f"\nDone. State: {state_path}. Backups and manifest: {backup_root}. Undo with --restore \"{backup_root}\"")
        print("Tracks changed on disk; the catalog database does not need a reload (same paths).")
    else:
        print(f"\nPreview rendered under {backup_root / '_staging'} (catalog untouched, state not written)")


if __name__ == "__main__":
    asyncio.run(main())

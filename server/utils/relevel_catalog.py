import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("LOG_FILE_NAME", "relevel.log")

import numpy as np  # noqa: E402
import pyloudnorm as pyln  # noqa: E402
import soundfile as sf  # noqa: E402

from config import settings  # noqa: E402
from services import track_asset_stages as stages  # noqa: E402
from services.audio_headroom import write_pcm16_dithered  # noqa: E402
from services.audio_master_service import MASTER_TARGET_LUFS  # noqa: E402
from services.audio_transcoding_service import AudioTranscodingService  # noqa: E402

TOLERANCE_LU = 0.5


def parse_args():
    parser = argparse.ArgumentParser(description="Bring catalog masters that sit above the station level down to it "
                                                 "(gain only, nothing else changes) and re-encode their Opus/WebM")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def relevel_master(path: Path):
    data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    loudness = float(pyln.Meter(rate).integrated_loudness(data))
    if not np.isfinite(loudness) or loudness <= MASTER_TARGET_LUFS + TOLERANCE_LU:
        return None
    gain_db = MASTER_TARGET_LUFS - loudness
    tmp = path.with_suffix(".relevel.wav")
    write_pcm16_dithered(tmp, (data * 10 ** (gain_db / 20)).T, rate)
    os.replace(tmp, path)
    return gain_db


async def encode(transcoding: AudioTranscodingService, track_id: str):
    master = stages.master_wav_path(track_id)
    for bitrate in stages.OPUS_BITRATES:
        opus, webm = stages.opus_path(track_id, bitrate), stages.webm_path(track_id, bitrate)
        opus_tmp, webm_tmp = opus.with_name(opus.stem + ".relevel" + opus.suffix), webm.with_name(webm.stem + ".relevel" + webm.suffix)
        if not await transcoding.transcode_to_opus(master, opus_tmp, bitrate):
            raise RuntimeError(f"Opus {bitrate} encode failed")
        if not await transcoding.transcode_opus_to_webm(opus_tmp, webm_tmp):
            raise RuntimeError(f"WebM {bitrate} remux failed")
        os.replace(opus_tmp, opus)
        os.replace(webm_tmp, webm)


async def main():
    args = parse_args()
    ids = sorted(p.stem for p in settings.ENHANCED_WAV_DIR.glob("*.wav") if not p.stem.endswith(".relevel"))
    if args.limit:
        ids = ids[:args.limit]
    transcoding = AudioTranscodingService()
    await transcoding.initialize()
    semaphore = asyncio.Semaphore(args.workers)
    counts = {"relevelled": 0, "already": 0, "failed": 0}

    async def one(track_id: str):
        async with semaphore:
            try:
                if args.dry_run:
                    data, rate = sf.read(str(stages.master_wav_path(track_id)), dtype="float64", always_2d=True)
                    loud = pyln.Meter(rate).integrated_loudness(data)
                    counts["relevelled" if loud > MASTER_TARGET_LUFS + TOLERANCE_LU else "already"] += 1
                    return
                gain = await asyncio.to_thread(relevel_master, stages.master_wav_path(track_id))
                if gain is None:
                    counts["already"] += 1
                    return
                await encode(transcoding, track_id)
                counts["relevelled"] += 1
                done = counts["relevelled"] + counts["already"] + counts["failed"]
                if done % 25 == 0:
                    print(f"{done}/{len(ids)} {counts}", flush=True)
            except Exception as e:
                counts["failed"] += 1
                print(f"{track_id} failed: {e}", flush=True)

    await asyncio.gather(*(one(t) for t in ids))
    print("Finished", counts, flush=True)


if __name__ == "__main__":
    asyncio.run(main())

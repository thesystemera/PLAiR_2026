import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from config import settings  # noqa: E402
from services_radio import crossfade_plan  # noqa: E402


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="Crossfade plans for random catalog pairs (no server, no LLM).")
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    features = sorted(Path(settings.AUDIOFEATURES_DIR).glob("*.json"))
    timing_dir = Path(settings.LYRIC_TIMESTAMPS_DIR)
    overlaps, missing = [], 0
    for _ in range(args.pairs):
        a, b = (load(p) for p in random.sample(features, 2))
        plan = crossfade_plan.plan(a, b, load(timing_dir / f"{a['id']}.json"), load(timing_dir / f"{b['id']}.json"))
        if plan is None:
            missing += 1
            print("no plan (generic fade)")
            continue
        overlaps.append(plan["duration_ms"])
        print(f"{a['duration']:6.1f}s song, next starts at {plan['optimal_start_ms'] / 1000:6.1f}s | "
              f"overlap {plan['duration_ms'] / 1000:.1f}s, out {plan['fade_out_ms'] / 1000:.1f}s "
              f"(+{plan['fade_out_delay_ms'] / 1000:.1f}), in {plan['fade_in_ms'] / 1000:.1f}s "
              f"(+{plan['fade_in_delay_ms'] / 1000:.1f}) | {plan['reason']}")
    if overlaps:
        print(f"overlap: min {min(overlaps) / 1000:.1f}s, median {statistics.median(overlaps) / 1000:.1f}s, "
              f"max {max(overlaps) / 1000:.1f}s; {missing} without a plan")


if __name__ == "__main__":
    main()

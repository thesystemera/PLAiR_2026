"""Seed-VC singing voice conversion over a folder of WAVs (writes <name>_vc.wav at 44.1 kHz), models loaded once.

Calls Seed-VC's own inference.main from a clone of github.com/Plachtaa/seed-vc (GPL-3.0; called, not copied), with the
44.1 kHz singing model (pitch follows the source), fp32 for the Pascal P6000 and a fixed seed per file.
"""
import argparse
import glob
import os
import shutil
import sys
import time

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--repo", required=True, help="clone of github.com/Plachtaa/seed-vc")
    parser.add_argument("--reference", required=True, help="the voice to sing in (1-25 s); the source's own voice re-sings it")
    parser.add_argument("--steps", type=int, default=50, help="diffusion steps (Seed-VC recommends 30-50 for singing)")
    parser.add_argument("--cfg", type=float, default=0.7, help="inference cfg rate")
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    folder, reference = os.path.abspath(args.folder), os.path.abspath(args.reference)
    sys.path.insert(0, args.repo)
    os.chdir(args.repo)
    import torch
    import inference

    original_load = inference.load_models
    loaded = {}

    def load_once(run_args):
        if "models" not in loaded:
            loaded["models"] = original_load(run_args)
        return loaded["models"]

    inference.load_models = load_once
    scratch = os.path.join(folder, "_seedvc_out")
    for path in sorted(glob.glob(os.path.join(folder, "*.wav"))):
        if path.endswith("_vc.wav"):
            continue
        shutil.rmtree(scratch, ignore_errors=True)
        torch.manual_seed(args.seed)
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        run_args = argparse.Namespace(source=path, target=reference, output=scratch, diffusion_steps=args.steps, length_adjust=1.0,
                                      inference_cfg_rate=args.cfg, f0_condition=True, auto_f0_adjust=False, semi_tone_shift=0,
                                      checkpoint=None, config=None, fp16=False)
        inference.main(run_args)
        produced = glob.glob(os.path.join(scratch, "*.wav"))
        if len(produced) != 1:
            raise RuntimeError(f"Seed-VC wrote {len(produced)} files for {path}")
        shutil.move(produced[0], path[:-4] + "_vc.wav")
        print(f"{os.path.basename(path)}: {time.perf_counter() - started:.1f} s, GPU peak {torch.cuda.max_memory_allocated() / 2 ** 30:.1f} GB", flush=True)
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()

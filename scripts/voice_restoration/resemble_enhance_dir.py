import argparse
import glob
import os
import sys
import types

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


for _name in ["deepspeed", "deepspeed.accelerator", "deepspeed.runtime", "deepspeed.runtime.engine",
              "deepspeed.runtime.utils"]:
    sys.modules[_name] = _Stub(_name)
sys.modules["deepspeed.accelerator"].get_accelerator = lambda: None
sys.modules["deepspeed.runtime.utils"].clip_grad_norm_ = lambda *a, **k: None

import soundfile as sf
import torch
import torchaudio
from pathlib import Path

SETTINGS = {
    "quality": dict(solver="midpoint", nfe=64, lambd=0.1, tau=0.5),
    "fast": dict(solver="euler", nfe=8, lambd=0.0, tau=0.5),
}


def main():
    parser = argparse.ArgumentParser(description="Resemble Enhance over a folder of WAVs (writes <name>_re.wav at 48 kHz)")
    parser.add_argument("folder")
    parser.add_argument("--repo", required=True, help="clone of github.com/resemble-ai/resemble-enhance")
    parser.add_argument("--weights", required=True, help="folder holding enhancer_stage2")
    parser.add_argument("--setting", choices=list(SETTINGS), default="quality")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)
    from resemble_enhance.enhancer.inference import load_enhancer
    from resemble_enhance.inference import inference, remove_weight_norm_recursively

    enhancer = load_enhancer(Path(args.weights) / "enhancer_stage2", "cuda")
    remove_weight_norm_recursively(enhancer)
    enhancer.configurate_(**SETTINGS[args.setting])
    for path in sorted(glob.glob(os.path.join(args.folder, "*.wav"))):
        if path.endswith("_re.wav"):
            continue
        audio, rate = sf.read(path, dtype="float32")
        with torch.inference_mode():
            enhanced, out_rate = inference(model=enhancer, dwav=torch.tensor(audio), sr=rate, device="cuda")
        sf.write(path[:-4] + "_re.wav", torchaudio.functional.resample(enhanced.cpu(), out_rate, 48000).numpy(), 48000)
        print(os.path.basename(path), flush=True)


if __name__ == "__main__":
    main()

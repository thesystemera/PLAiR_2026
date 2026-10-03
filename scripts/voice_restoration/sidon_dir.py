import argparse
import glob
import os

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

import soundfile as sf
import torch
import torchaudio
import transformers
from huggingface_hub import hf_hub_download

TAIL_PAD_S = 1.5


def main():
    parser = argparse.ArgumentParser(description="Sidon speech restoration over a folder of WAVs (writes <name>_sidon.wav at 48 kHz)")
    parser.add_argument("folder")
    args = parser.parse_args()
    extractor = torch.jit.load(hf_hub_download("sarulab-speech/sidon-v0.1", filename="feature_extractor_cuda.pt"),
                               map_location="cuda").eval()
    decoder = torch.jit.load(hf_hub_download("sarulab-speech/sidon-v0.1", filename="decoder_cuda.pt"),
                             map_location="cuda").eval()
    features = transformers.SeamlessM4TFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0")
    for path in sorted(glob.glob(os.path.join(args.folder, "*.wav"))):
        if path.endswith("_sidon.wav"):
            continue
        audio, rate = sf.read(path, dtype="float32")
        with torch.inference_mode():
            wave = torch.from_numpy(audio).cuda().view(1, -1)
            peak = float(wave.abs().max()) or 1.0
            wave = torchaudio.functional.highpass_biquad(0.9 * wave / peak, rate, 50)
            wave16 = torchaudio.functional.resample(wave, rate, 16000)
            wave16 = torch.nn.functional.pad(wave16, (0, int(16000 * TAIL_PAD_S))).view(-1).cpu()
            inputs = features(torch.nn.functional.pad(wave16, (160, 160)), return_tensors="pt", sampling_rate=16000)
            hidden = extractor(inputs["input_features"].cuda())["last_hidden_state"].transpose(1, 2)
            restored = decoder(hidden).view(-1)[:-960][: int(len(audio) * 48000 / rate)].cpu().numpy()
        sf.write(path[:-4] + "_sidon.wav", restored * (peak / 0.9), 48000)
        print(os.path.basename(path), flush=True)


if __name__ == "__main__":
    main()

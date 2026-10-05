import asyncio, os, sys, time
from pathlib import Path

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1] if len(sys.argv) > 1 else "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, torch  # noqa: E402

SRC = Path(r"D:\catalog\premaster_wav\21955a90ec313b6f9c5bf542276eeec9.wav")
GB = 2 ** 30
HOP = 2048


def peak(fn):
    torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats(); base = torch.cuda.memory_allocated(); t0 = time.time()
    with torch.no_grad():
        out = fn()
    torch.cuda.synchronize()
    return out, (torch.cuda.max_memory_allocated() - base) / GB, time.time() - t0


def encode_tiled(vae, x, tile_frames, margin_frames):
    total = x.shape[-1] // HOP
    out = []
    for start in range(0, total, tile_frames):
        a, b = max(0, start - margin_frames), min(total, start + tile_frames + margin_frames)
        z = vae.encode(x[..., a * HOP:b * HOP]).latent_dist.mode()
        out.append(z[..., start - a:start - a + min(tile_frames, total - start)])
    return torch.cat(out, dim=-1)


def decode_tiled(vae, z, tile_frames, margin_frames):
    total = z.shape[-1]
    out = []
    for start in range(0, total, tile_frames):
        a, b = max(0, start - margin_frames), min(total, start + tile_frames + margin_frames)
        w = vae.decode(z[..., a:b]).sample
        out.append(w[..., (start - a) * HOP:(start - a + min(tile_frames, total - start)) * HOP])
    return torch.cat(out, dim=-1)


def rel_db(a, b):
    return 20 * np.log10(float((a - b).float().pow(2).mean().sqrt()) / float(b.float().pow(2).mean().sqrt()) + 1e-12)


async def main():
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    sonic = SonicMasterService(); sonic.configure(**SUNO_SONIC_SETTINGS); await sonic.initialize()
    vae = sonic.vae
    x, r = sf.read(SRC, dtype="float32", always_2d=True)
    n = (30 * r // HOP) * HOP
    chunk = torch.from_numpy(x[r * 60:r * 60 + n].T.copy()).unsqueeze(0).to("cuda", dtype=next(vae.parameters()).dtype)
    z_full, m, t = peak(lambda: vae.encode(chunk).latent_dist.mode())
    print(f"full encode: +{m:.2f} GB {t:.1f} s", flush=True)
    w_full, m, t = peak(lambda: vae.decode(z_full).sample)
    print(f"full decode: +{m:.2f} GB {t:.1f} s", flush=True)
    for tile_s, margin_s in ((10, 2), (8, 3), (5, 3)):
        tf, mf = int(tile_s * r / HOP), int(margin_s * r / HOP)
        z_t, me, te = peak(lambda: encode_tiled(vae, chunk, tf, mf))
        w_t, md, td = peak(lambda: decode_tiled(vae, z_full, tf, mf))
        print(f"tiles {tile_s}s + {margin_s}s margin: encode +{me:.2f} GB {te:.1f} s, latent diff {rel_db(z_t, z_full):.0f} dB | "
              f"decode +{md:.2f} GB {td:.1f} s, audio diff {rel_db(w_t[..., :w_full.shape[-1]], w_full):.0f} dB", flush=True)
    await sonic.unload()

asyncio.run(main())

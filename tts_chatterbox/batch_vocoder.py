import torch
import torch.nn.functional as F
from chatterbox.models.s3gen.const import S3GEN_SIL
from chatterbox.models.s3gen.utils.mask import make_pad_mask

import s3_patches

s3_patches.apply()


class BatchVocoder:
    """S3Gen token -> waveform for several sentences at once (any mix of voices with equal prompt length)."""

    def __init__(self, s3, max_tokens):
        self.s3 = s3
        self.flow = s3.flow
        self.dev = s3.device
        self.noise = torch.randn(80, 2 * max_tokens, device=self.dev)

    @torch.inference_mode()
    def __call__(self, items):
        """items: list of (tokens: list[int], final: bool, ref_dict). Returns list of 1-D float waveforms."""
        flow, dev = self.flow, self.dev
        toks = []
        for tokens, final, _ in items:
            t = [x for x in tokens if x < 6561] + ([S3GEN_SIL] * 3 if final else [])
            toks.append(torch.tensor(t, dtype=torch.long, device=dev))
        refs = [ref for _, _, ref in items]
        P = refs[0]["prompt_token"].size(1)
        assert all(r["prompt_token"].size(1) == P for r in refs)
        n_mel_prompt = refs[0]["prompt_feat"].size(1)
        B = len(items)
        lens = torch.tensor([len(t) for t in toks], device=dev)
        T = int(lens.max())
        token = torch.zeros(B, P + T, dtype=torch.long, device=dev)
        for i, (t, r) in enumerate(zip(toks, refs)):
            token[i, :P] = r["prompt_token"][0]
            token[i, P:P + len(t)] = t
        token_len = P + lens
        mask = (~make_pad_mask(token_len)).unsqueeze(-1).float()
        emb = torch.cat([r["embedding"] for r in refs]).float()
        emb = flow.spk_embed_affine_layer(F.normalize(emb, dim=1))
        h, h_masks = flow.encoder(flow.input_embedding(token) * mask, token_len)
        h = flow.encoder_proj(h)
        frames = h.size(1)
        cond = torch.zeros(B, frames, 80, device=dev, dtype=h.dtype)
        for i, r in enumerate(refs):
            cond[i, :n_mel_prompt] = r["prompt_feat"][0]
        h_lengths = h_masks.sum(dim=-1).squeeze(dim=-1)
        mmask = (~make_pad_mask(h_lengths)).unsqueeze(1).to(h)
        gen = frames - n_mel_prompt
        noised = torch.zeros(B, 80, gen, device=dev, dtype=h.dtype)
        for i, t in enumerate(toks):
            noised[i, :, :2 * len(t)] = self.noise[:, :2 * len(t)]
        feat, _ = flow.decoder(mu=h.transpose(1, 2).contiguous(), mask=mmask, spks=emb, cond=cond.transpose(1, 2),
                               n_timesteps=2, noised_mels=noised, meanflow=self.s3.meanflow)
        mels = feat[:, :, n_mel_prompt:]
        wavs, _ = self.s3.mel2wav.inference(speech_feat=mels.to(self.s3.dtype), cache_source=torch.zeros(1, 1, 0, device=dev))
        out = []
        fade = self.s3.trim_fade
        for i, t in enumerate(toks):
            w = wavs[i, :2 * len(t) * 480].clone()
            w[:len(fade)] *= fade
            out.append(w)
        return out

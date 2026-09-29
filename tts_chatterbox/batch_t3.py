import math

import torch
import torch.nn.functional as F

SLOT_SIZES = (1, 2, 4, 8)
KV_BUCKETS = (512, 768, 1024, 1536)


class BatchTurboDecoder:
    """Chatterbox-Turbo T3 decoding for up to max(SLOT_SIZES) sentences at once.

    One CUDA graph per (active slot count, KV length bucket). Every graph step decodes
    one token for each of the first n slots with the same maths and sampling order as
    T3.inference_turbo. Sentences join and leave slots independently.
    """

    def __init__(self, t3, top_k=1000, repetition_penalty=1.2):
        self.t3 = t3
        tf = t3.tfmr
        cfg = t3.cfg
        dev = next(t3.parameters()).device
        self.dev = dev
        self.B = max(SLOT_SIZES)
        self.L = max(KV_BUCKETS)
        self.n_layer, self.n_head = cfg.n_layer, cfg.n_head
        self.dim = cfg.hidden_size
        self.hd = self.dim // self.n_head
        self.eps = cfg.layer_norm_epsilon
        self.vocab = t3.hp.speech_tokens_dict_size
        self.stop = t3.hp.stop_speech_token
        self.top_k, self.rep = top_k, repetition_penalty
        self.blocks = tf.h
        self.wpe = tf.wpe.weight
        B, L = self.B, self.L
        self.k = [torch.zeros(B, self.n_head, L, self.hd, device=dev) for _ in range(self.n_layer)]
        self.v = [torch.zeros(B, self.n_head, L, self.hd, device=dev) for _ in range(self.n_layer)]
        self.x = torch.zeros(B, self.dim, device=dev)
        self.pos = torch.zeros(B, dtype=torch.long, device=dev)
        self.step = torch.zeros(B, dtype=torch.long, device=dev)
        self.active = torch.zeros(B, dtype=torch.long, device=dev)
        self.seen = torch.zeros(B, self.vocab, dtype=torch.bool, device=dev)
        self.temp = torch.full((B, 1), 0.8, device=dev)
        self.topp = torch.full((B, 1), 0.95, device=dev)
        self.out = torch.zeros(B, L, dtype=torch.long, device=dev)
        self.rows = torch.arange(B, device=dev)
        self.ones = torch.ones(B, dtype=torch.bool, device=dev)
        self.arange = torch.arange(L, device=dev)
        self.graphs = {}
        self.prefix_cache = {}

    def _sample(self, logits, n):
        vals, idx = torch.sort(logits / self.temp[:n], dim=-1, descending=True)
        vals[:, self.top_k:] = float("-inf")
        probs = vals.softmax(-1)
        vals = vals.masked_fill(probs.cumsum(-1) - probs >= self.topp[:n], float("-inf"))
        seen = torch.gather(self.seen[:n], 1, idx)
        vals = torch.where(seen, torch.where(vals < 0, vals * self.rep, vals / self.rep), vals)
        gumbel = -torch.log(-torch.log(torch.rand_like(vals).clamp_(1e-20, 1.0)))
        return torch.gather(idx, 1, torch.argmax(vals + gumbel, dim=-1, keepdim=True)).view(n)

    def _step(self, n, kv):
        rows = self.rows[:n]
        pos = self.pos[:n]
        h = self.x[:n] + self.wpe.index_select(0, pos)
        mask = (self.arange[:kv].view(1, 1, 1, kv) > pos.view(n, 1, 1, 1))
        for i, block in enumerate(self.blocks):
            a = F.layer_norm(h, (self.dim,), block.ln_1.weight, block.ln_1.bias, self.eps)
            qkv = torch.addmm(block.attn.c_attn.bias, a, block.attn.c_attn.weight)
            q, k, v = qkv.split(self.dim, dim=-1)
            self.k[i][rows, :, pos] = k.view(n, self.n_head, self.hd)
            self.v[i][rows, :, pos] = v.view(n, self.n_head, self.hd)
            K = self.k[i][:n, :, :kv]
            V = self.v[i][:n, :, :kv]
            scores = torch.matmul(q.view(n, self.n_head, 1, self.hd), K.transpose(2, 3)) / math.sqrt(self.hd)
            att = torch.matmul(scores.masked_fill(mask, float("-inf")).softmax(-1), V).reshape(n, self.dim)
            h = h + torch.addmm(block.attn.c_proj.bias, att, block.attn.c_proj.weight)
            m = F.layer_norm(h, (self.dim,), block.ln_2.weight, block.ln_2.bias, self.eps)
            m = F.gelu(torch.addmm(block.mlp.c_fc.bias, m, block.mlp.c_fc.weight), approximate="tanh")
            h = h + torch.addmm(block.mlp.c_proj.bias, m, block.mlp.c_proj.weight)
        h = F.layer_norm(h, (self.dim,), self.t3.tfmr.ln_f.weight, self.t3.tfmr.ln_f.bias, self.eps)
        token = self._sample(self.t3.speech_head(h), n)
        act = self.active[:n]
        self.out[rows, self.step[:n]] = token
        self.seen.index_put_((rows, token), self.ones[:n])
        self.x[:n] = self.t3.speech_emb(token)
        self.pos[:n] += act
        self.step[:n] += act

    @torch.inference_mode()
    def capture(self):
        self.pos.fill_(0)
        self.step.fill_(0)
        pool = None
        for n in SLOT_SIZES:
            for kv in KV_BUCKETS:
                s = torch.cuda.Stream()
                s.wait_stream(torch.cuda.current_stream())
                with torch.cuda.stream(s):
                    for _ in range(2):
                        self._step(n, kv)
                torch.cuda.current_stream().wait_stream(s)
                g = torch.cuda.CUDAGraph()
                with torch.cuda.graph(g, pool=pool):
                    self._step(n, kv)
                pool = g.pool()
                self.graphs[(n, kv)] = g
                self.pos.fill_(0)
                self.step.fill_(0)
        self.out.zero_()
        self.seen.zero_()

    @torch.inference_mode()
    def _prefix(self, key, cond_emb):
        if key not in self.prefix_cache:
            out = self.t3.tfmr(inputs_embeds=cond_emb, use_cache=True)
            self.prefix_cache[key] = out.past_key_values
        return self.prefix_cache[key]

    @torch.inference_mode()
    def admit(self, slot, t3_cond, text_tokens, voice_key, temperature=0.8, top_p=0.95):
        """Prefill one sentence into a free slot and sample its first token."""
        start = self.t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
        embeds, len_cond = self.t3.prepare_input_embeds(t3_cond=t3_cond, text_tokens=text_tokens,
                                                        speech_tokens=start, cfg_weight=0.0)
        prefix = self._prefix(voice_key, embeds[:, :len_cond])
        n = embeds.size(1)
        for i in range(self.n_layer):
            self.k[i][slot, :, :len_cond].copy_(prefix.layers[i].keys[0])
            self.v[i][slot, :, :len_cond].copy_(prefix.layers[i].values[0])
        rest = embeds[:, len_cond:]
        h = rest + self.wpe[len_cond:n].unsqueeze(0)
        for i, block in enumerate(self.blocks):
            a = F.layer_norm(h, (self.dim,), block.ln_1.weight, block.ln_1.bias, self.eps)
            qkv = a @ block.attn.c_attn.weight + block.attn.c_attn.bias
            q, k, v = qkv.split(self.dim, dim=-1)
            T = rest.size(1)
            self.k[i][slot, :, len_cond:n] = k.view(T, self.n_head, self.hd).transpose(0, 1)
            self.v[i][slot, :, len_cond:n] = v.view(T, self.n_head, self.hd).transpose(0, 1)
            K = self.k[i][slot, :, :n]
            V = self.v[i][slot, :, :n]
            qh = q.view(T, self.n_head, self.hd).transpose(0, 1)
            scores = torch.matmul(qh, K.transpose(1, 2)) / math.sqrt(self.hd)
            causal = torch.arange(n, device=self.dev).view(1, n) > torch.arange(len_cond, n, device=self.dev).view(T, 1)
            att = torch.matmul(scores.masked_fill(causal, float("-inf")).softmax(-1), V).transpose(0, 1).reshape(T, self.dim)
            h = h + (att @ block.attn.c_proj.weight + block.attn.c_proj.bias)
            m = F.layer_norm(h, (self.dim,), block.ln_2.weight, block.ln_2.bias, self.eps)
            m = F.gelu(m @ block.mlp.c_fc.weight + block.mlp.c_fc.bias, approximate="tanh")
            h = h + (m @ block.mlp.c_proj.weight + block.mlp.c_proj.bias)
        h = F.layer_norm(h[:, -1], (self.dim,), self.t3.tfmr.ln_f.weight, self.t3.tfmr.ln_f.bias, self.eps)
        logits = self.t3.speech_head(h)
        self.temp[slot] = temperature
        self.topp[slot] = top_p
        self.seen[slot].zero_()
        self.seen[slot, self.t3.hp.start_speech_token] = True
        vals, idx = torch.sort(logits / temperature, dim=-1, descending=True)
        vals[:, self.top_k:] = float("-inf")
        probs = vals.softmax(-1)
        vals = vals.masked_fill(probs.cumsum(-1) - probs >= top_p, float("-inf"))
        seen = torch.gather(self.seen[slot:slot + 1], 1, idx)
        vals = torch.where(seen, torch.where(vals < 0, vals * self.rep, vals / self.rep), vals)
        gumbel = -torch.log(-torch.log(torch.rand_like(vals).clamp_(1e-20, 1.0)))
        token = torch.gather(idx, 1, torch.argmax(vals + gumbel, dim=-1, keepdim=True)).view(1)
        self.seen[slot].zero_()
        self.seen[slot, token] = True
        self.out[slot, 0] = token[0]
        self.step[slot] = 1
        self.pos[slot] = n
        self.x[slot] = self.t3.speech_emb(token)[0]
        self.active[slot] = 1
        return n

    def release(self, slot):
        self.active[slot] = 0

    def replay(self, n_slots, kv_needed, times):
        n = next(s for s in SLOT_SIZES if s >= n_slots)
        kv = next(b for b in KV_BUCKETS if b >= kv_needed)
        g = self.graphs[(n, kv)]
        for _ in range(times):
            g.replay()

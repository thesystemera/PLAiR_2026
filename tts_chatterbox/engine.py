import logging
import queue
import re
import threading
import time
import uuid

import torch
from chatterbox.tts_turbo import punc_norm

from batch_t3 import BatchTurboDecoder, KV_BUCKETS, SLOT_SIZES
from batch_vocoder import BatchVocoder

log = logging.getLogger("chatterbox")

SAMPLE_RATE = 24000
SAMPLES_PER_TOKEN = 960
TOKENS_PER_S = 25
HOLDBACK = 4 * SAMPLES_PER_TOKEN
FADE = 480
CHUNKS = (20, 40, 80, 160)
STEPS_PER_SYNC = 8
URGENT_AHEAD_S = 1.0
VOCODE_BATCH = 8
_WORD = re.compile(r"[A-Za-z0-9']+")


def duration_cap_tokens(text):
    return int(max(5.0, 3.0 + 1.0 * len(_WORD.findall(text))) * TOKENS_PER_S)


class Job:
    def __init__(self, text, voice, temperature, top_p, seed):
        self.id = uuid.uuid4().hex[:8]
        self.text, self.voice, self.temperature, self.top_p, self.seed = text, voice, temperature, top_p, seed
        self.out_q = queue.Queue()
        self.cancelled = False
        self.enqueued_at = time.perf_counter()
        self.started_at = None
        self.first_at = None
        self.slot = None
        self.cap = duration_cap_tokens(text)
        self.requested = 0
        self.chunk = 0
        self.sent = 0
        self.tail = None
        self.finish = None
        self.done = False
        self.vocode_s = 0.0


class Engine:
    def __init__(self, model, conds):
        self.m = model
        self.conds = conds
        self.dev = model.device
        self.dec = BatchTurboDecoder(model.t3)
        self.dec.capture()
        self.vocoder = BatchVocoder(model.s3gen, max(KV_BUCKETS))
        self.fade_in = torch.linspace(0, 1, FADE, device=self.dev)
        self.pending = queue.Queue()
        self.slots = [None] * max(SLOT_SIZES)
        self.pos_host = [0] * max(SLOT_SIZES)
        self.admit_len = [0] * max(SLOT_SIZES)
        self.lock = threading.Lock()
        self.vocode_reqs = {}

    def submit(self, job):
        self.pending.put(job)

    def running(self):
        with self.lock:
            return [j for j in self.slots if j is not None]

    def _admit(self, job):
        slot = self.slots.index(None)
        job.slot = slot
        job.started_at = time.perf_counter()
        if job.seed is not None:
            torch.cuda.manual_seed(job.seed)
        tokens = self.m.tokenizer(punc_norm(job.text), return_tensors="pt").input_ids.to(self.dev)
        n = self.dec.admit(slot, self.conds[job.voice].t3, tokens, job.voice,
                           temperature=job.temperature or 0.8, top_p=job.top_p or 0.95)
        self.admit_len[slot] = n - 1
        self.pos_host[slot] = n
        with self.lock:
            self.slots[slot] = job

    def _release(self, job, finish):
        job.finish = finish
        self.dec.release(job.slot)
        with self.lock:
            self.slots[job.slot] = None

    def _request_vocode(self, job, tokens, final):
        self.vocode_reqs[job.id] = (job, tokens, final)

    def start(self):
        threading.Thread(target=self._loop, name="engine", daemon=True).start()

    def _urgent(self, job, now):
        return job.first_at is None or job.sent / SAMPLE_RATE - (now - job.first_at) < URGENT_AHEAD_S

    def _urgency(self, job, now):
        if job.first_at is None:
            return (0, job.enqueued_at)
        return (1, job.sent / SAMPLE_RATE - (now - job.first_at))

    def _loop(self):
        with torch.inference_mode():
            while True:
                active = self.running()
                while None in self.slots:
                    try:
                        job = self.pending.get(block=not active and not self.vocode_reqs)
                    except queue.Empty:
                        break
                    if job.cancelled:
                        job.out_q.put(None)
                        continue
                    self._admit(job)
                    active = self.running()
                now = time.perf_counter()
                if self.vocode_reqs:
                    keys = sorted(self.vocode_reqs, key=lambda k: self._urgency(self.vocode_reqs[k][0], now))
                    if not active or self._urgent(self.vocode_reqs[keys[0]][0], now):
                        self._vocode_batch([self.vocode_reqs.pop(k) for k in keys[:VOCODE_BATCH]])
                        continue
                if active:
                    self._t3_chunk(active)

    def _vocode_batch(self, reqs):
        work = []
        for job, tokens, final in reqs:
            if job.done:
                continue
            if tokens is None or job.cancelled:
                job.done = True
                job.out_q.put(None)
                continue
            work.append((job, tokens, final))
        if not work:
            return
        t0 = time.perf_counter()
        wavs = self.vocoder([(tokens, final, self.conds[job.voice].gen) for job, tokens, final in work])
        took = time.perf_counter() - t0
        for (job, tokens, final), wav in zip(work, wavs):
            job.vocode_s += took / len(work)
            self._emit(job, wav, final)
            if final:
                job.done = True
                job.out_q.put(None)
                self._log(job)

    def _t3_chunk(self, active):
        n = max(j.slot for j in active) + 1
        kv = max(self.pos_host[j.slot] for j in active) + STEPS_PER_SYNC + 1
        self.dec.replay(n, kv, STEPS_PER_SYNC)
        steps = self.dec.step[:n].tolist()
        for j in active:
            self.pos_host[j.slot] = self.admit_len[j.slot] + steps[j.slot]
        out = self.dec.out[:n, :max(steps)].tolist()
        for job in active:
            if job.cancelled:
                self._release(job, "cancelled")
                self._request_vocode(job, None, True)
                continue
            toks = out[job.slot][:steps[job.slot]]
            stop = self.dec.stop in toks
            if stop:
                toks = toks[:toks.index(self.dec.stop)]
            final = stop or len(toks) >= job.cap
            need = CHUNKS[min(job.chunk, len(CHUNKS) - 1)]
            if final or len(toks) - job.requested >= need:
                job.requested = len(toks)
                job.chunk += 1
                self._request_vocode(job, toks, final)
            if final:
                self._release(job, "stop" if stop else "max_tokens")

    def _emit(self, job, wav, final):
        start = job.sent
        end = len(wav) if final else len(wav) - HOLDBACK
        if not final and end - start <= FADE:
            return
        piece = wav[start:end].clone()
        if job.tail is not None:
            n = min(FADE, len(piece), len(job.tail))
            piece[:n] = job.tail[:n] * (1 - self.fade_in[:n]) + piece[:n] * self.fade_in[:n]
        job.tail = None if final else wav[end:end + FADE].clone()
        job.sent = end
        if len(piece):
            data = (piece.clamp(-1, 1) * 32767).to(torch.int16).cpu().numpy().tobytes()
            if job.first_at is None:
                job.first_at = time.perf_counter()
            job.out_q.put(data)

    def _log(self, job):
        secs = job.sent / SAMPLE_RATE
        total = time.perf_counter() - job.started_at
        log.info("[%s] %s | voice=%s slot=%d wait=%dms ttfa=%s audio=%.1fs rtf=%.2fx vocode=%.2fs end=%s | %s",
                 job.id, "ok" if job.finish != "cancelled" else "cancelled", job.voice, job.slot,
                 (job.started_at - job.enqueued_at) * 1000,
                 f"{(job.first_at - job.enqueued_at) * 1000:.0f}ms" if job.first_at else "-",
                 secs, total / secs if secs else 0, job.vocode_s, job.finish, job.text[:60])
        if job.finish == "max_tokens":
            log.warning("[%s] hit the length cap - runaway take cut short", job.id)

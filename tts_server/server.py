import contextlib
import ctypes
import logging
import queue
import random
import re
import threading
import time
import uuid

logging.basicConfig(level=logging.INFO, format="[TTS_SERVER] %(message)s")
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = logging.getLogger("tts_server")

import config
import llama_patches  # noqa: F401

import llama_cpp
import numpy as np
from flask import Flask, Response, jsonify, request
from llama_cpp import _internals as llama_internals
from orpheus_cpp import OrpheusCpp
import orpheus_cpp.model as _orph_model

_MODEL_MAP = {
    "FASTEST": ("lex-au/Orpheus-3b-FT-Q2_K.gguf", "Orpheus-3b-FT-Q2_K.gguf"),
    "AVERAGE": ("isaiahbjork/orpheus-3b-0.1-ft-Q4_K_M-GGUF", "orpheus-3b-0.1-ft-q4_k_m.gguf"),
    "BEST": ("lex-au/Orpheus-3b-FT-Q8_0.gguf", "Orpheus-3b-FT-Q8_0.gguf"),
}

_repo_id, _filename = _MODEL_MAP.get(config.QUALITY, _MODEL_MAP["AVERAGE"])
_orph_model.OrpheusCpp.lang_to_model["en"] = _repo_id
_orig_hf_hub_download = _orph_model.hf_hub_download


def _patched_hf_hub_download(repo_id, filename=None, **kwargs):
    if repo_id == _repo_id and filename and filename.lower() == _filename.lower():
        filename = _filename
    return _orig_hf_hub_download(repo_id, filename=filename, **kwargs)


_orph_model.hf_hub_download = _patched_hf_hub_download

VOICES = ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]
TOP_K = 40
KV_PAD = 256
MAX_BATCHED_KV = 512
MAX_BATCH = 2
MULTIBYTE_PATTERNS = ((2, 192), (3, 224), (4, 240))
END_OF_SPEECH = 128258
AUDIO_TOKENS_PER_S = config.SAMPLE_RATE / 2048 * 7
TOKEN_OVERHEAD = 30
_WORD = re.compile(r"[A-Za-z0-9']+")
_EMOTION_TAG = re.compile(r"<(laugh|chuckle|sigh|gasp|groan|yawn|cough|sniffle)>")


def duration_cap_tokens(text: str) -> int:
    tags = len(_EMOTION_TAG.findall(text))
    words = len(_WORD.findall(re.sub(r"</?\w+>", " ", text)))
    seconds = max(config.DURATION_CAP_FLOOR_S, config.DURATION_CAP_BASE_S
                  + config.DURATION_CAP_PER_WORD_S * words + config.DURATION_CAP_PER_TAG_S * tags)
    return int(seconds * AUDIO_TOKENS_PER_S) + TOKEN_OVERHEAD


class Job:
    __slots__ = ("id", "text", "voice", "temperature", "top_p", "max_tokens", "min_p", "seed",
                 "out_q", "cancelled", "enqueued_at", "started_at", "first_audio_at", "samples", "slot",
                 "finish")

    def __init__(self, text, voice, temperature, top_p, max_tokens, min_p, seed=None):
        self.id = uuid.uuid4().hex[:8]
        self.text = text
        self.voice = voice
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.min_p = min_p
        self.seed = seed
        self.out_q: "queue.Queue[bytes | None]" = queue.Queue()
        self.cancelled = False
        self.enqueued_at = time.perf_counter()
        self.started_at = None
        self.first_audio_at = None
        self.samples = 0
        self.slot = -1
        self.finish = "cancelled"


class Slot:
    __slots__ = ("idx", "job", "sampler", "pos", "last_token", "max_tokens", "completion", "returned",
                 "text", "multibyte_fix", "codes", "count", "history")

    def __init__(self, idx):
        self.idx = idx
        self.job = None
        self.history: list[int] = []


class BatchEngine:
    def __init__(self, n_slots: int):
        self.n_slots = n_slots
        self.orpheus = OrpheusCpp(n_gpu_layers=-1, verbose=False, lang="en")
        self.llm = self.orpheus._llm
        self._use_slot_context(n_slots)
        self.ctx = self.llm._ctx
        self.vocab = self.llm._model.vocab
        self.n_ctx_seq = self.llm.context_params.n_ctx // n_slots
        self.batch = llama_cpp.llama_batch_init(max(self.llm.n_batch, n_slots), 0, 1)
        self.snac = self.orpheus._snac_session
        self.snac_inputs = [x.name for x in self.snac.get_inputs()]
        self.slots = [Slot(i) for i in range(n_slots)]
        self.seed = self.llm._seed
        self.pieces: dict[int, bytes] = {}
        self._piece_buf = (ctypes.c_char * 32)()
        self.snac_q: "queue.Queue[tuple[Job, list[int] | None]]" = queue.Queue()
        self.bos = self.llm.token_bos()
        self.prompt_bos, self.prompt_eos = self._special_prompt_tokens()

    def _use_slot_context(self, n_slots):
        params = llama_cpp.llama_context_params.from_buffer_copy(self.llm.context_params)
        params.n_ctx = params.n_ctx * n_slots
        params.n_seq_max = n_slots
        self.llm._ctx.close()
        self.llm._ctx = self.llm._stack.enter_context(
            contextlib.closing(
                llama_internals.LlamaContext(model=self.llm._model, params=params, verbose=self.llm.verbose)))
        self.llm.context_params = params

    def _special_prompt_tokens(self):
        model = self.llm._model
        cls_id, sep_id = model.token_cls(), model.token_sep()
        bos = [cls_id if cls_id != -1 else self.bos]
        eos = [sep_id if sep_id != -1 else self.llm.token_eos()]
        if not model.add_bos_token() or bos[:1] == [-1]:
            bos = []
        if not model.add_eos_token() and sep_id == -1:
            eos = []
        return bos, eos

    def prompt_tokens(self, job: Job) -> list[int]:
        prompt = f"<|audio|>{job.voice}: {job.text}<|eot_id|><custom_token_4>"
        return self.prompt_bos + self.llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True) + self.prompt_eos

    def piece(self, token: int) -> bytes:
        p = self.pieces.get(token)
        if p is None:
            n = llama_cpp.llama_token_to_piece(self.vocab, llama_cpp.llama_token(token), self._piece_buf, 32, 0, False)
            p = bytes(self._piece_buf[:n])
            self.pieces[token] = p
        return p

    def detokenize(self, tokens) -> bytes:
        out = b"".join(self.piece(t) for t in tokens)
        return out[1:] if tokens and tokens[0] == self.bos and out[0:1] == b" " else out

    def _job_seed(self, job: Job) -> int:
        if job.seed is not None:
            return random.Random(job.seed).randint(0, 2 ** 32)
        self.seed = random.Random(self.seed).randint(0, 2 ** 32)
        return self.seed

    def _decode(self, items):
        b = self.batch
        for n, (token, pos, seq, want) in enumerate(items):
            b.token[n] = token
            b.pos[n] = pos
            b.n_seq_id[n] = 1
            b.seq_id[n][0] = seq
            b.logits[n] = want
        b.n_tokens = len(items)
        rc = llama_cpp.llama_decode(self.ctx.ctx, b)
        if rc != 0:
            raise RuntimeError(f"llama_decode returned {rc}")

    def _start(self, slot: Slot, job: Job):
        prompt = self.prompt_tokens(job)
        if len(prompt) >= self.n_ctx_seq:
            raise ValueError(f"prompt of {len(prompt)} tokens exceeds the context window")
        max_tokens = job.max_tokens
        if max_tokens is None or max_tokens <= 0:
            max_tokens = self.n_ctx_seq - len(prompt)
        if max_tokens + len(prompt) >= self.n_ctx_seq:
            max_tokens = self.n_ctx_seq - len(prompt)
        self.llm.set_seed(self._job_seed(job))
        slot.sampler = self.llm._init_sampler(
            top_k=TOP_K, top_p=job.top_p, min_p=job.min_p, typical_p=1.0, temp=job.temperature,
            repeat_penalty=config.REPEAT_PENALTY, frequency_penalty=config.FREQUENCY_PENALTY,
            presence_penalty=0.0)
        slot.job = job
        slot.max_tokens = max_tokens
        slot.completion = []
        slot.returned = 0
        slot.text = bytearray()
        slot.multibyte_fix = 0
        slot.codes = []
        slot.count = 0
        job.slot = slot.idx
        job.started_at = time.perf_counter()
        start = 0
        for a, b in zip(slot.history, prompt[:-1]):
            if a != b:
                break
            start += 1
        if start == 0 or not self.ctx.kv_cache_seq_rm(slot.idx, start, -1):
            start = 0
        self.ctx.kv_cache_seq_rm(slot.idx, start, -1)
        slot.history = list(prompt)
        n_batch = self.llm.n_batch
        for i in range(start, len(prompt), n_batch):
            chunk = prompt[i:i + n_batch]
            self._decode([(t, i + k, slot.idx, k == len(chunk) - 1) for k, t in enumerate(chunk)])
        slot.pos = len(prompt)
        self._accept(slot, slot.sampler.sample(self.ctx, -1))

    def _finish(self, slot: Slot):
        job = slot.job
        slot.job = None
        slot.sampler.close()
        slot.sampler = None
        self.ctx.kv_cache_seq_rm(slot.idx, len(slot.history), -1)
        self.snac_q.put((job, None))

    def _accept(self, slot: Slot, token: int):
        if token == END_OF_SPEECH and config.STOP_ON_END_OF_SPEECH:
            slot.job.finish = "end_of_speech"
            self._finish(slot)
            return
        if llama_cpp.llama_vocab_is_eog(self.vocab, token):
            slot.job.finish = "eos"
            self._finish(slot)
            return
        slot.completion.append(token)
        slot.text += self.piece(token)
        for k, char in enumerate(slot.text[-3:]):
            k = 3 - k
            for num, pattern in MULTIBYTE_PATTERNS:
                if num > k and pattern & char == pattern:
                    slot.multibyte_fix = num - k
        if slot.multibyte_fix > 0:
            slot.multibyte_fix -= 1
            slot.last_token = token
            return
        self._emit_text(slot)
        if len(slot.completion) >= slot.max_tokens:
            slot.job.finish = "max_tokens"
            self._finish(slot)
            return
        slot.last_token = token

    def _emit_text(self, slot: Slot):
        remaining = slot.completion[slot.returned:]
        remaining_length = len(self.detokenize(remaining))
        end_position = 0
        while remaining:
            for i in range(1, len(remaining) + 1):
                try:
                    bs = self.detokenize(remaining[:i])
                    ts = bs.decode("utf-8")
                    break
                except UnicodeError:
                    pass
            else:
                break
            end_position += len(bs)
            if end_position > remaining_length:
                break
            remaining = remaining[i:]
            slot.returned += i
            self._orpheus_token(slot, ts)

    def _orpheus_token(self, slot: Slot, text: str):
        code = self.orpheus._token_to_id(text, slot.count)
        if code is not None and code > 0:
            slot.codes.append(code)
            if len(slot.codes) > 28:
                del slot.codes[0]
            slot.count += 1
            if slot.count % 7 == 0 and slot.count > 27:
                self.snac_q.put((slot.job, slot.codes[-28:]))

    def _admit(self):
        while True:
            free = next((s for s in self.slots if s.job is None), None)
            if free is None:
                return True
            idle = all(s.job is None for s in self.slots)
            try:
                job = job_queue.get(block=idle)
            except queue.Empty:
                return True
            if job is None:
                return False
            with _current_jobs_lock:
                current_jobs.add(job)
            if job.cancelled:
                self.snac_q.put((job, None))
                continue
            try:
                self._start(free, job)
            except Exception as e:
                log.error("[%s] start failed: %s", job.id, e)
                if free.job is job:
                    self._finish(free)
                else:
                    self.snac_q.put((job, None))

    def llm_loop(self):
        while self._admit():
            active = [s for s in self.slots if s.job is not None]
            for s in active:
                if s.job.cancelled:
                    self._finish(s)
            groups: dict[int, list[Slot]] = {}
            for s in active:
                if s.job is not None:
                    kv = (s.pos + KV_PAD) // KV_PAD * KV_PAD
                    key = kv if kv <= MAX_BATCHED_KV else -1 - s.idx
                    group = groups.setdefault(key, [])
                    if len(group) == MAX_BATCH:
                        group = groups.setdefault(-100 - s.idx, [])
                    group.append(s)
            for group in groups.values():
                try:
                    self._decode([(s.last_token, s.pos, s.idx, True) for s in group])
                except Exception as e:
                    log.error("decode failed: %s", e)
                    for s in group:
                        self._finish(s)
                    continue
                for i, s in enumerate(group):
                    s.pos += 1
                    self._accept(s, s.sampler.sample(self.ctx, i))

    def snac_audio(self, codes: list[int]) -> bytes | None:
        frame = np.asarray(codes, dtype=np.int64).reshape(-1, 7)
        c0 = frame[:, 0].reshape(1, -1)
        c1 = frame[:, [1, 4]].reshape(1, -1)
        c2 = frame[:, [2, 3, 5, 6]].reshape(1, -1)
        if (c0 < 0).any() or (c0 > 4096).any() or (c1 < 0).any() or (c1 > 4096).any() \
                or (c2 < 0).any() or (c2 > 4096).any():
            return None
        audio_hat = self.snac.run(None, dict(zip(self.snac_inputs, (c0, c1, c2))))[0]
        return (audio_hat[:, :, 2048:4096] * 32767).astype(np.int16).tobytes()

    def snac_loop(self):
        pending: dict[str, bytearray] = {}
        while True:
            job, codes = self.snac_q.get()
            acc = pending.setdefault(job.id, bytearray())
            if codes is None:
                pending.pop(job.id, None)
                if acc and not job.cancelled:
                    job.out_q.put(bytes(acc))
                self._done(job)
                continue
            if job.cancelled:
                continue
            try:
                audio = self.snac_audio(codes)
            except Exception as e:
                log.error("[%s] SNAC decode failed: %s", job.id, e)
                job.cancelled = True
                continue
            if not audio:
                continue
            if job.first_audio_at is None:
                job.first_audio_at = time.perf_counter()
            job.samples += len(audio) // 2
            acc.extend(audio)
            if len(acc) >= 4096:
                job.out_q.put(bytes(acc))
                acc.clear()

    def _done(self, job: Job):
        job.out_q.put(None)
        with _current_jobs_lock:
            current_jobs.discard(job)
            jobs_by_id.pop(job.id, None)
        if job.started_at is None:
            return
        audio_s = job.samples / config.SAMPLE_RATE
        gen_s = time.perf_counter() - job.started_at
        wait_ms = (job.started_at - job.enqueued_at) * 1000
        ttfa_ms = (job.first_audio_at - job.started_at) * 1000 if job.first_audio_at else 0
        log.info(
            "[%s] slot=%d %s | voice=%s wait=%.0fms ttfa=%.0fms audio=%.1fs rtf=%.2fx end=%s | %s",
            job.id, job.slot, "cancelled" if job.cancelled else "ok", job.voice, wait_ms, ttfa_ms,
            audio_s, (gen_s / audio_s) if audio_s else 0, job.finish, job.text[:60],
        )
        if job.finish == "max_tokens" and not job.cancelled:
            log.warning("[%s] hit the length cap (%d tokens) - runaway take cut short", job.id, job.max_tokens)


job_queue: "queue.Queue[Job | None]" = queue.Queue()
engine: BatchEngine | None = None
current_jobs: set[Job] = set()
_current_jobs_lock = threading.Lock()
jobs_by_id: dict[str, Job] = {}
_ready = False

app = Flask(__name__)


@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = data.get("voice")
    try:
        temperature = float(data.get("temperature", config.TEMPERATURE))
        top_p = float(data.get("top_p", config.TOP_P))
        max_tokens = int(data.get("max_tokens", config.MAX_TOKENS))
        min_p = max(0.0, min(1.0, float(data.get("min_p", config.MIN_P))))
        seed = int(data["seed"]) if data.get("seed") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "bad numeric params"}), 400

    if not text:
        return jsonify({"error": "empty text"}), 400
    if not voice:
        return jsonify({"error": "missing voice"}), 400
    if voice not in VOICES:
        return jsonify({"error": f"unknown voice '{voice}'", "voices": VOICES}), 400
    if job_queue.qsize() >= config.MAX_QUEUE_DEPTH:
        return jsonify({"error": "queue full", "queue_depth": job_queue.qsize()}), 503
    if config.DURATION_CAP:
        max_tokens = min(max_tokens, duration_cap_tokens(text)) if max_tokens > 0 else duration_cap_tokens(text)

    job = Job(text, voice, temperature, top_p, max_tokens, min_p, seed)
    with _current_jobs_lock:
        jobs_by_id[job.id] = job
    job_queue.put(job)

    def stream():
        try:
            while True:
                chunk = job.out_q.get()
                if chunk is None:
                    break
                yield chunk
        except GeneratorExit:
            job.cancelled = True

    headers = {
        "Content-Type": f"audio/L16;rate={config.SAMPLE_RATE};channels=1",
        "X-Job-Id": job.id,
        "X-Sample-Rate": str(config.SAMPLE_RATE),
        "Cache-Control": "no-store",
    }
    return Response(stream(), headers=headers)


@app.route("/abort", methods=["POST"])
def abort():
    drained = 0
    while True:
        try:
            j = job_queue.get_nowait()
        except queue.Empty:
            break
        if j is not None:
            j.cancelled = True
            j.out_q.put(None)
            drained += 1
            with _current_jobs_lock:
                jobs_by_id.pop(j.id, None)

    with _current_jobs_lock:
        for job in current_jobs:
            job.cancelled = True
        cancelled = len(current_jobs)

    return jsonify({"status": "ok", "drained": drained, "current_cancelled": cancelled})


@app.route("/abort/<job_id>", methods=["POST"])
def abort_job(job_id):
    with _current_jobs_lock:
        job = jobs_by_id.get(job_id)
        if job is None:
            return jsonify({"status": "not_found", "job_id": job_id}), 404
        job.cancelled = True
        started = job.started_at is not None
    if not started:
        job.out_q.put(None)
    log.info("[%s] abort requested (%s)", job_id, "running" if started else "queued")
    return jsonify({"status": "ok", "job_id": job_id, "started": started})


@app.route("/health", methods=["GET"])
def health():
    with _current_jobs_lock:
        active = len(current_jobs)
    return jsonify({
        "status": "ok" if _ready else "loading",
        "workers": engine.n_slots if engine else 0,
        "active_jobs": active,
        "queue_depth": job_queue.qsize(),
        "voices": VOICES,
        "sample_rate": config.SAMPLE_RATE,
        "quality": config.QUALITY,
    })


def start_engine() -> BatchEngine:
    global engine
    log.info("Loading Orpheus [%s] with %d parallel stream(s) on CUDA_VISIBLE_DEVICES=%s",
             config.QUALITY, config.NUM_WORKERS, config.GPU)
    t0 = time.perf_counter()
    engine = BatchEngine(max(1, config.NUM_WORKERS))
    log.info("engine loaded in %.1fs", time.perf_counter() - t0)
    threading.Thread(target=engine.llm_loop, daemon=True, name="orpheus-llm").start()
    threading.Thread(target=engine.snac_loop, daemon=True, name="orpheus-snac").start()
    warm = [Job("Hello.", config.DEFAULT_VOICE, config.TEMPERATURE, config.TOP_P, 200, config.MIN_P)
            for _ in range(engine.n_slots)]
    for job in warm:
        job_queue.put(job)
    for job in warm:
        while job.out_q.get() is not None:
            pass
    log.info("engine warm")
    return engine


def main():
    global _ready
    start_engine()
    _ready = True
    log.info("Orpheus TTS server ready on %s:%s | parallel streams=%d | voices=%s",
             config.HOST, config.PORT, engine.n_slots, ", ".join(VOICES))
    app.run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()

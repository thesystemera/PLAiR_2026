import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = os.getenv("TTS_CUDA_VISIBLE_DEVICES") or os.getenv("CUDA_VISIBLE_DEVICES") or "0"

logging.basicConfig(level=logging.INFO, format="[TTS_SERVER] %(message)s")
for noisy in ("werkzeug", "httpx", "huggingface_hub"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("chatterbox")

import torch
from flask import Flask, Response, jsonify, request
from chatterbox.tts_turbo import ChatterboxTurboTTS

from engine import Engine, Job, SAMPLE_RATE

PORT = int(os.getenv("TTS_SERVER_PORT", "8090"))
VOICES_DIR = HERE / "voices"
BUILTIN_VOICE = "jess"
MAX_QUEUE_DEPTH = 64

state = {"engine": None, "voices": []}
jobs = {}
jobs_lock = threading.Lock()


def load():
    model = ChatterboxTurboTTS.from_pretrained(device="cuda")
    conds = {BUILTIN_VOICE: model.conds}
    for wav in sorted(VOICES_DIR.glob("*.wav")):
        model.prepare_conditionals(str(wav))
        conds[wav.stem] = model.conds
    engine = Engine(model, conds)
    engine.start()
    state["voices"] = list(conds)
    state["engine"] = engine
    log.info("Chatterbox-Turbo ready on %s, %d slots, voices: %s", torch.cuda.get_device_name(0),
             len(engine.slots), ", ".join(conds))


app = Flask(__name__)


@app.route("/tts", methods=["POST"])
def tts():
    engine = state["engine"]
    if engine is None:
        return jsonify({"error": "loading"}), 503
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = data.get("voice")
    try:
        temperature = float(data["temperature"]) if data.get("temperature") is not None else None
        top_p = float(data["top_p"]) if data.get("top_p") is not None else None
        seed = int(data["seed"]) if data.get("seed") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "bad numeric params"}), 400
    if not text:
        return jsonify({"error": "empty text"}), 400
    if voice not in state["voices"]:
        return jsonify({"error": f"unknown voice '{voice}'", "voices": state["voices"]}), 400
    if engine.pending.qsize() >= MAX_QUEUE_DEPTH:
        return jsonify({"error": "queue full", "queue_depth": engine.pending.qsize()}), 503
    job = Job(text, voice, temperature, top_p, seed)
    with jobs_lock:
        jobs[job.id] = job
    engine.submit(job)

    def stream():
        try:
            while True:
                chunk = job.out_q.get()
                if chunk is None:
                    break
                yield chunk
        except GeneratorExit:
            job.cancelled = True
        finally:
            with jobs_lock:
                jobs.pop(job.id, None)

    return Response(stream(), headers={
        "Content-Type": f"audio/L16;rate={SAMPLE_RATE};channels=1",
        "X-Job-Id": job.id,
        "X-Sample-Rate": str(SAMPLE_RATE),
        "Cache-Control": "no-store",
    })


@app.route("/abort", methods=["POST"])
def abort():
    with jobs_lock:
        for job in jobs.values():
            job.cancelled = True
        n = len(jobs)
    return jsonify({"status": "ok", "cancelled": n})


@app.route("/abort/<job_id>", methods=["POST"])
def abort_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"status": "not_found", "job_id": job_id}), 404
    job.cancelled = True
    return jsonify({"status": "ok", "job_id": job_id, "started": job.started_at is not None})


@app.route("/health", methods=["GET"])
def health():
    engine = state["engine"]
    return jsonify({"status": "ok" if engine else "loading",
                    "workers": len(engine.slots) if engine else 0,
                    "active_jobs": len(engine.running()) if engine else 0,
                    "queue_depth": engine.pending.qsize() if engine else 0,
                    "voices": state["voices"], "sample_rate": SAMPLE_RATE, "engine": "chatterbox-turbo"})


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)

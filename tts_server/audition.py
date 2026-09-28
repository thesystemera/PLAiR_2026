import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import config

VOICES = ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]
LINES = {
    "shaquille": "Yo, yo, yo! PLAiR dot FM, you are locked in! That was Nine Inch Nails, and trust me, "
                 "we are just getting warmed up. <laugh> Terry, tell 'em what's next!",
    "terry": "<sigh> Oh, sure, let me just... check my notes. Right. It's grim out there, folks, "
             "so grab a jacket. <chuckle> And maybe hide your ears, he's picking the next track.",
}


def synthesize(text: str, voice: str) -> tuple[bytes, float]:
    request = urllib.request.Request(
        f"http://{config.HOST}:{config.PORT}/tts",
        data=json.dumps({"text": text, "voice": voice}).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(request, timeout=300) as response:
        pcm = response.read()
    return pcm, time.perf_counter() - t0


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "auditions"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for voice in VOICES:
        for host, text in LINES.items():
            pcm, elapsed = synthesize(text, voice)
            audio_s = len(pcm) / 2 / config.SAMPLE_RATE
            path = out_dir / f"{host}_{voice}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(config.SAMPLE_RATE)
                w.writeframes(pcm)
            rtf = elapsed / audio_s if audio_s else 0
            results.append({"voice": voice, "host": host, "audio_s": round(audio_s, 2),
                            "gen_s": round(elapsed, 2), "rtf": round(rtf, 2)})
            print(f"{host:9s} {voice:5s} audio={audio_s:5.1f}s gen={elapsed:5.1f}s rtf={rtf:.2f}", flush=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

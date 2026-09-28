import argparse
import asyncio
import base64
import json
import time
import uuid

import httpx
import websockets

DEFAULT_TURNS = [
    "Any good gigs on this weekend? I'm into jazz and soul.",
    "Where can I grab a late night bite near me?",
    "What's the weather doing tonight?",
    "What's Auckland been listening to lately?",
    "Play something by Radiohead",
]

AUCKLAND = {"latitude": -36.8570, "longitude": 174.7600, "accuracy": 30, "timezone": "Pacific/Auckland"}


async def run_turn(base: str, ws, guest: str, device: str, text: str, timeout: float) -> dict:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base}/api/dj/talk", json={"text": text},
                              headers={"X-Guest-ID": guest, "X-Device-ID": device})
    result = {"text": text, "status": r.status_code, "reply": None, "commands": None, "first_audio_s": None,
              "audio_kb": 0, "streams": 0}
    if r.status_code != 200:
        return result
    deadline = t0 + timeout
    idle_after_reply = None
    while time.perf_counter() < deadline:
        wait = max(0.1, deadline - time.perf_counter())
        if idle_after_reply is not None:
            wait = min(wait, max(0.1, idle_after_reply - time.perf_counter()))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=wait)
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        kind, data = msg.get("type"), msg.get("data") or {}
        if kind == "conversation_update" and data.get("bot_response") and result["reply"] is None:
            result["reply"] = data.get("bot_response")
            result["commands"] = data.get("commands") or data.get("command")
            result["reply_s"] = round(time.perf_counter() - t0, 1)
            idle_after_reply = time.perf_counter() + 12
        elif kind == "tts_stream_start":
            result["streams"] += 1
        elif kind == "tts_stream_audio_chunk":
            chunk = data.get("chunk") or data.get("audio") or data.get("data")
            if isinstance(chunk, str):
                result["audio_kb"] += len(base64.b64decode(chunk)) // 1024
                if result["first_audio_s"] is None:
                    result["first_audio_s"] = round(time.perf_counter() - t0, 1)
            if idle_after_reply is not None:
                idle_after_reply = time.perf_counter() + 6
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="DJ tool-mode + City Pulse live test (guest in Auckland)")
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("turns", nargs="*")
    args = parser.parse_args()
    guest = f"guest_{uuid.uuid4()}"
    device = f"pulse-test-{uuid.uuid4().hex[:6]}"
    ws_url = args.base.replace("http", "ws", 1) + \
        f"/ws/playback?guest_id={guest}&device_id={device}&device_name=pulse-test&device_type=desktop"
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "listener_location", "data": AUCKLAND}))
        await asyncio.sleep(1.5)
        for text in args.turns or DEFAULT_TURNS:
            result = await run_turn(args.base, ws, guest, device, text, args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())

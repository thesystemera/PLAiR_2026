import argparse
import asyncio
import base64
import json
import os
import secrets
import time
import uuid
from pathlib import Path

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


async def run_turn(base: str, ws, headers: dict, text: str, timeout: float) -> dict:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base}/api/dj/talk", json={"text": text},
                              headers=headers)
    result = {"text": text, "status": r.status_code, "reply": None, "commands": None, "first_audio_s": None,
              "audio_kb": 0, "streams": 0, "stream_starts": [], "activity": []}
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
        elif kind == "playback_state" and isinstance(data.get("queue"), list):
            queue, current = data["queue"], (data.get("current_track") or {}).get("id")
            index = next((i for i, t in enumerate(queue) if t.get("id") == current), -1)
            label = lambda t: (f"{(t.get('generation_params') or {}).get('title') or t.get('title') or t.get('name')} "
                               f"[{str(t.get('id'))[:6]}] by {(t.get('generation_params') or {}).get('artist_name') or t.get('artist_name') or '?'}")
            result["now_playing"] = label(queue[index]) if index >= 0 else None
            result["up_next"] = [label(t) for t in queue[index + 1:index + 7]]
        elif kind == "dj_activity":
            if data.get("phase") == "result":
                result["activity"].append(f"{data.get('tool')}: {data.get('outcome')} {data.get('summary') or ''}".strip())
            elif data.get("phase") == "start":
                result["activity"].append(f"{data.get('tool')} > {data.get('label')}")
        elif kind == "tts_stream_start":
            result["streams"] += 1
            result["stream_starts"].append(round(time.perf_counter() - t0, 1))
        elif kind == "tts_stream_audio_chunk":
            chunk = data.get("chunk") or data.get("audio") or data.get("data")
            if isinstance(chunk, str):
                result["audio_kb"] += len(base64.b64decode(chunk)) // 1024
                if result["first_audio_s"] is None:
                    result["first_audio_s"] = round(time.perf_counter() - t0, 1)
            if idle_after_reply is not None:
                idle_after_reply = time.perf_counter() + 6
    return result


ACCOUNT_FILE = Path(__file__).with_name(".dj_test_account.json")
LOG_FILE = Path(__file__).resolve().parent.parent / "data" / "logs" / "radio.log"
ERROR_MARKERS = ("[ERROR]", "Traceback", "[CRITICAL]")


def new_log_errors(offset: int) -> tuple[int, list]:
    if not LOG_FILE.exists():
        return offset, []
    size = LOG_FILE.stat().st_size
    if size < offset:
        offset = 0
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        lines = handle.read().splitlines()
    return size, [line[:300] for line in lines if any(marker in line for marker in ERROR_MARKERS)]


async def signed_in_token(base: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        if os.environ.get("DJ_TEST_USERNAME") and os.environ.get("DJ_TEST_PASSWORD"):
            account = {"username": os.environ["DJ_TEST_USERNAME"], "password": os.environ["DJ_TEST_PASSWORD"]}
            r = await client.post(f"{base}/api/auth/login", json=account)
        elif ACCOUNT_FILE.exists():
            account = json.loads(ACCOUNT_FILE.read_text())
            r = await client.post(f"{base}/api/auth/login", json=account)
        else:
            account = {"username": f"djtest_{secrets.token_hex(3)}", "password": secrets.token_urlsafe(18)}
            r = await client.post(f"{base}/api/auth/register", json=account)
            if r.status_code == 200:
                ACCOUNT_FILE.write_text(json.dumps(account))
        r.raise_for_status()
        token = r.json()["token"]
        cleared = await client.post(f"{base}/api/manage_user_data", json={"action": "delete_conversations"},
                                    headers={"Authorization": f"Bearer {token}"})
        cleared.raise_for_status()
        return token


async def main() -> None:
    parser = argparse.ArgumentParser(description="DJ tool-mode + City Pulse live test (guest in Auckland)")
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--signed-in", action="store_true",
                        help="talk as the test account in tests/.dj_test_account.json (created on first use)")
    parser.add_argument("--keep-going", action="store_true", help="don't stop at the first error in radio.log")
    parser.add_argument("turns", nargs="*")
    args = parser.parse_args()
    guest = f"guest_{uuid.uuid4()}"
    device = f"pulse-test-{uuid.uuid4().hex[:6]}"
    headers = {"X-Device-ID": device}
    protocols = None
    query = f"device_id={device}&device_name=pulse-test&device_type=desktop"
    if args.signed_in:
        token = await signed_in_token(args.base)
        headers["Authorization"] = f"Bearer {token}"
        protocols = ["plair.v1", f"auth.{token}"]
    else:
        headers["X-Guest-ID"] = guest
        query = f"guest_id={guest}&" + query
    ws_url = args.base.replace("http", "ws", 1) + f"/ws/playback?{query}"
    async with websockets.connect(ws_url, max_size=None, subprotocols=protocols) as ws:
        await ws.send(json.dumps({"type": "listener_location", "data": AUCKLAND}))
        await asyncio.sleep(1.5)
        log_offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
        for text in args.turns or DEFAULT_TURNS:
            result = await run_turn(args.base, ws, headers, text, args.timeout)
            log_offset, errors = new_log_errors(log_offset)
            result["log_errors"] = errors
            print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
            if errors and not args.keep_going:
                print(f"STOPPED after '{text}': {len(errors)} error line(s) in radio.log", flush=True)
                break


if __name__ == "__main__":
    asyncio.run(main())

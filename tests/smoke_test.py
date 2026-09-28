import argparse
import asyncio
import base64
import json
import sys
import time
import uuid

import httpx
import websockets

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


async def http_checks(client: httpx.AsyncClient, base: str, headers: dict) -> None:
    r = await client.get(f"{base}/api/health")
    check("backend health", r.status_code == 200 and r.json().get("status") == "ok", str(r.status_code))

    r = await client.get(f"{base}/api/catalog/stats")
    total = r.json().get("total_tracks", 0) if r.status_code == 200 else 0
    check("catalog loaded", total > 0, f"{total} tracks")

    r = await client.get(f"{base}/api/catalog/tracks", params={"limit": 1})
    tracks = r.json().get("tracks") if r.status_code == 200 else None
    track_id = (tracks[0].get("id") or tracks[0].get("track_id")) if tracks else None
    check("catalog listing", bool(track_id), track_id or str(r.status_code))
    if track_id:
        for path, kind in ((f"/api/stream/{track_id}", "audio/mpeg"), (f"/api/stream/{track_id}/opus", "audio/")):
            r = await client.get(f"{base}{path}", headers={"Range": "bytes=0-1023"})
            check(f"stream {path.split('/')[-1] if path.endswith('opus') else 'mp3'}",
                  r.status_code in (200, 206) and kind in r.headers.get("content-type", ""), str(r.status_code))

    r = await client.post(f"{base}/api/search/semantic", json={"query": "melancholy synthwave", "limit": 3}, headers=headers)
    body = r.json() if r.status_code == 200 else {}
    results = body.get("results") or body.get("tracks") or (body if isinstance(body, list) else [])
    check("semantic search", r.status_code == 200 and len(results) > 0, f"{len(results)} results")


async def tts_check(client: httpx.AsyncClient, tts_url: str) -> None:
    try:
        r = await client.get(f"{tts_url}/health")
        data = r.json()
        check("tts engine health", data.get("status") == "ok", f"{data.get('workers')} workers")
    except httpx.HTTPError as e:
        check("tts engine health", False, type(e).__name__)


async def dj_check(base: str, message: str, timeout: float) -> None:
    guest = f"guest_{uuid.uuid4()}"
    device = f"smoke-device-{uuid.uuid4().hex[:6]}"
    ws_url = base.replace("http", "ws", 1) + f"/ws/playback?guest_id={guest}&device_id={device}&device_name=smoke&device_type=desktop"
    got_reply = got_audio = False
    audio_bytes = 0
    first_audio = None
    async with websockets.connect(ws_url, max_size=None) as ws:
        async with httpx.AsyncClient(timeout=30) as client:
            t0 = time.perf_counter()
            r = await client.post(f"{base}/api/dj/talk", json={"text": message},
                                  headers={"X-Guest-ID": guest, "X-Device-ID": device})
        if not check("dj talk accepted", r.status_code == 200, str(r.status_code)):
            return
        deadline = t0 + timeout
        while time.perf_counter() < deadline and not (got_reply and got_audio):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.perf_counter()))
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            kind, data = msg.get("type"), msg.get("data") or {}
            if kind == "conversation_update" and data.get("bot_response"):
                got_reply = True
            elif kind == "tts_stream_audio_chunk":
                chunk = data.get("chunk") or data.get("audio") or data.get("data")
                if isinstance(chunk, str):
                    audio_bytes += len(base64.b64decode(chunk))
                    first_audio = first_audio or time.perf_counter() - t0
                    got_audio = True
    check("dj text reply", got_reply)
    check("dj spoke", got_audio, f"first audio {first_audio:.1f}s, {audio_bytes // 1024} KB" if got_audio else "no audio")


async def main() -> int:
    parser = argparse.ArgumentParser(description="PLAiR end-to-end smoke test")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--tts", default="http://127.0.0.1:8090")
    parser.add_argument("--message", default="Hey, play me something chilled and say hi")
    parser.add_argument("--dj-timeout", type=float, default=120)
    parser.add_argument("--skip-dj", action="store_true", help="skip the LLM/TTS conversation check")
    args = parser.parse_args()

    headers = {"X-Guest-ID": f"guest_{uuid.uuid4()}", "X-Device-ID": "smoke-device"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await http_checks(client, args.base, headers)
        except httpx.HTTPError as e:
            check("backend reachable", False, type(e).__name__)
            return 1
        await tts_check(client, args.tts)
    if not args.skip_dj:
        try:
            await dj_check(args.base, args.message, args.dj_timeout)
        except (OSError, websockets.WebSocketException) as e:
            check("dj conversation", False, type(e).__name__)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

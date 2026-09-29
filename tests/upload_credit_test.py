import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_FILE = ROOT / "tests" / ".dj_test_account.json"


def account():
    if os.getenv("DJ_TEST_USERNAME") and os.getenv("DJ_TEST_PASSWORD"):
        return os.environ["DJ_TEST_USERNAME"], os.environ["DJ_TEST_PASSWORD"]
    data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    return data["username"], data["password"]


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def main():
    parser = argparse.ArgumentParser(description="Music upload: artist credit, edits, artist profiles")
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--file", required=True, help="audio file to upload (runs the full pipeline)")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    username, password = account()
    token = requests.post(f"{base}/api/auth/login", json={"username": username, "password": password},
                          timeout=30).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "X-Device-ID": "upload-test-device"}
    results = []

    r = requests.post(f"{base}/api/user/music/upload", files={"file": ("x.mp3", b"0" * 2048)}, timeout=30)
    results.append(check("upload without login refused before reading", r.status_code == 401, str(r.status_code)))

    band = f"Test Band {uuid.uuid4().hex[:6]}"
    artist = requests.post(f"{base}/api/artists", headers=headers, json={"name": band, "bio": "test",
                                                                         "links": ["https://example.com"]}, timeout=30)
    results.append(check("artist profile created", artist.status_code == 200, artist.text[:100]))
    artist = artist.json()
    setup = requests.get(f"{base}/api/user/music/setup", headers=headers, timeout=30).json()
    results.append(check("setup lists the artist", any(a["id"] == artist["id"] for a in setup["artists"])))

    started = time.time()
    with open(args.file, "rb") as f:
        r = requests.post(f"{base}/api/user/music/upload", headers=headers, timeout=1200,
                          files={"file": (Path(args.file).name, f)},
                          data={"upload_id": uuid.uuid4().hex, "artist_profile_id": str(artist["id"]),
                                "enable_upscaling": "false"})
    ok = r.status_code == 200
    results.append(check("upload processed", ok, f"{r.status_code} in {time.time() - started:.0f}s {r.text[:160] if not ok else ''}"))
    if not ok:
        sys.exit(1)
    body = r.json()
    track_id, meta = body["track_id"], body["metadata"]
    print(f"      title={meta['title']!r} artist={meta['artist']!r} genre={meta['primary_genre']!r} "
          f"tags={meta.get('embedded_tags')}")
    results.append(check("credited to the chosen artist", meta["artist"] == band, meta["artist"]))

    track = requests.get(f"{base}/api/track/{track_id}", timeout=30)
    track = track.json() if track.ok else {}
    results.append(check("artist_name is the field the app reads",
                         (track.get("generation_params") or {}).get("artist_name") == band))

    r = requests.put(f"{base}/api/user/music/tracks/{track_id}", headers=headers, timeout=30,
                     json={"title": "Fixed Title", "mood_keywords": ["warm", "hazy"], "lyrics": ""})
    results.append(check("edit title, moods, lyrics", r.status_code == 200, r.text[:100]))

    renamed = requests.put(f"{base}/api/artists/{artist['id']}", headers=headers, json={"name": band + " II"},
                           timeout=60).json()
    results.append(check("rename re-credits the track", renamed.get("tracks_updated") == 1, str(renamed)[:120]))

    page = requests.get(f"{base}/api/artists/{renamed['slug']}", timeout=30).json()
    results.append(check("artist page lists the track", any(t["id"] == track_id for t in page.get("tracks", []))))

    blocked = requests.delete(f"{base}/api/artists/{artist['id']}", headers=headers, timeout=30)
    results.append(check("artist with tracks can't be deleted", blocked.status_code == 400, blocked.text[:80]))

    if not args.keep:
        d = requests.delete(f"{base}/api/user/music/tracks/{track_id}", headers=headers, timeout=60)
        d2 = requests.delete(f"{base}/api/artists/{artist['id']}", headers=headers, timeout=30)
        print(f"      cleanup track {d.status_code}, artist {d2.status_code}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

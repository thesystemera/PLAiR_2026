import argparse
import base64
import json
import os
import sys
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
    parser = argparse.ArgumentParser(description="Shoutouts, replies and reviews end to end")
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--recording", help="webm/opus recording to use as a spoken review")
    parser.add_argument("--keep", action="store_true", help="keep the created items")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    username, password = account()
    login = requests.post(f"{base}/api/auth/login", json={"username": username, "password": password}, timeout=30)
    login.raise_for_status()
    token = login.json().get("access_token") or login.json().get("token")
    headers = {"Authorization": f"Bearer {token}"}
    results, created = [], []

    tracks = requests.get(f"{base}/api/catalog/tracks?limit=1", timeout=30).json()
    track_list = tracks.get("tracks") if isinstance(tracks, dict) else tracks
    track_id = (track_list or [{}])[0].get("id")
    results.append(check("catalog track for reviews", bool(track_id), track_id))

    r = requests.post(f"{base}/api/tracks/{track_id}/reviews/text", headers=headers, timeout=120,
                      json={"text": "hey DJ save this as my review: this chorus is pure summer, I love this song"})
    ok = r.status_code == 200 and r.json().get("kind") == "review"
    results.append(check("typed review saved", ok, f"{r.status_code} {r.text[:120]}"))
    if ok:
        created.append(r.json()["id"])
        results.append(check("process talk trimmed from typed review",
                             "save this" not in r.json()["transcription"].lower(), r.json()["transcription"]))

    if args.recording:
        audio = base64.b64encode(Path(args.recording).read_bytes()).decode()
        r = requests.post(f"{base}/api/tracks/{track_id}/reviews/upload", headers=headers, timeout=300,
                          json={"audio": audio})
        ok = r.status_code == 200
        results.append(check("spoken review saved", ok, f"{r.status_code} {r.text[:160]}"))
        if ok:
            created.append(r.json()["id"])

    reviews = requests.get(f"{base}/api/tracks/{track_id}/reviews", timeout=30).json()
    ids = [x["id"] for x in reviews.get("reviews", [])]
    results.append(check("reviews listed for the track", all(i in ids for i in created), f"{len(ids)} review(s)"))
    for item in reviews.get("reviews", []):
        if item["id"] in created:
            leaked = any(k in json.dumps(item.get("user_data") or {}) for k in ("latitude", "longitude"))
            results.append(check(f"review {item['id']} has no coordinates", not leaked))
            if item.get("has_audio"):
                print(f"      sting: {item.get('sting') or 'none'}")

    found = requests.post(f"{base}/api/user_content/shoutouts/search", headers=headers, timeout=60,
                          json={"query": "hello everyone", "n_results": 5}).json()
    shoutouts = found.get("results", [])
    results.append(check("shoutout search returns only shoutouts",
                         bool(shoutouts) and all(s.get("kind") == "shoutout" for s in shoutouts), f"{len(shoutouts)}"))
    if shoutouts:
        parent_id = shoutouts[0]["id"]
        r = requests.post(f"{base}/api/user_content/shoutouts/{parent_id}/reply/text", headers=headers, timeout=120,
                          json={"text": "reply to that one: congrats, that made my day!"})
        ok = r.status_code == 200 and r.json().get("kind") == "reply"
        results.append(check("typed reply saved", ok, f"{r.status_code} {r.text[:120]}"))
        if ok:
            created.append(r.json()["id"])
            replies = requests.get(f"{base}/api/user_content/shoutouts/{parent_id}/replies?sort_by=popularity",
                                   timeout=30).json()
            rows = replies.get("replies", [])
            mine = next((x for x in rows if x["id"] == r.json()["id"]), None)
            results.append(check("reply listed under its parent with engagement",
                                 bool(mine) and "engagement" in mine and mine.get("has_audio") is False,
                                 f"{len(rows)} repl(ies)"))
            results.append(check("reply shows its parent", bool(mine and mine.get("parent_preview"))))
            r2 = requests.post(f"{base}/api/user_content/shoutouts/{r.json()['id']}/reply/text", headers=headers,
                               timeout=60, json={"text": "a reply to a reply"})
            results.append(check("reply to a reply refused", r2.status_code == 400, str(r2.status_code)))

    if not args.keep:
        for item_id in created:
            d = requests.delete(f"{base}/api/user_content/shoutouts/{item_id}", headers=headers, timeout=30)
            print(f"      cleanup {item_id}: {d.status_code}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

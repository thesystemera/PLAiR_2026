"""Settings per device kind (docs/SETTINGS.md): two kinds stay independent and survive a fresh login.

    E:/AI_RADIO/.venv/Scripts/python.exe tests/device_settings_test.py [--base URL]

No LLM calls. Uses the test account in tests/.dj_test_account.json and puts its settings back afterwards.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

import requests

ACCOUNT = json.loads((Path(__file__).parent / ".dj_test_account.json").read_text())


def login(base):
    res = requests.post(f"{base}/api/auth/login", json=ACCOUNT, timeout=20)
    res.raise_for_status()
    return res.json()["token"]


def headers(token, kind):
    return {"Authorization": f"Bearer {token}", "X-Device-ID": str(uuid.uuid4()), "X-Device-Kind": kind,
            "X-Guest-ID": f"guest_{uuid.uuid4()}"}


def get(base, token, kind):
    res = requests.get(f"{base}/api/settings", headers=headers(token, kind), timeout=20)
    res.raise_for_status()
    return res.json()


def put(base, token, kind, settings):
    res = requests.put(f"{base}/api/settings", headers=headers(token, kind), json={"settings": settings}, timeout=20)
    res.raise_for_status()
    return res.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    base = parser.parse_args().base
    failures = []

    def check(label, ok):
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    token = login(base)
    before = {kind: get(base, token, kind)["settings"] for kind in ("windows-pc", "android-phone")}

    put(base, token, "windows-pc", {"litArtwork": False, "visualQuality": "low"})
    put(base, token, "android-phone", {"litArtwork": True, "visualQuality": "high"})

    fresh = login(base)
    desktop = get(base, fresh, "windows-pc")
    phone = get(base, fresh, "android-phone")
    check("windows-pc keeps 3D off after a new login", desktop["settings"]["litArtwork"] is False and desktop["stored"])
    check("android-phone keeps 3D on after a new login", phone["settings"]["litArtwork"] is True and phone["stored"])
    check("visual quality differs per kind", desktop["settings"]["visualQuality"] == "low" and phone["settings"]["visualQuality"] == "high")
    check("a new device ID of the same kind gets the same settings", get(base, fresh, "windows-pc")["settings"] == desktop["settings"])

    rejected = put(base, fresh, "windows-pc", {"visualQuality": "ultra", "litArtwork": "yes", "unknown": True})
    check("invalid values and unknown keys are ignored", rejected["settings"]["visualQuality"] == "low" and rejected["settings"]["litArtwork"] is False)

    unauth = requests.get(f"{base}/api/settings", headers={"X-Device-ID": str(uuid.uuid4()), "X-Device-Kind": "iphone",
                                                           "X-Guest-ID": f"guest_{uuid.uuid4()}"}, timeout=20)
    check("guests get no server settings", unauth.status_code == 401)

    for kind, values in before.items():
        put(base, fresh, kind, values)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

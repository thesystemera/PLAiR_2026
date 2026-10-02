"""Account flows against a running backend, with a software passkey (no LLM calls).

Signs up with a passkey, signs in with it, adds a second one, links a "new device" by code,
sets a password and signs in with it, posts a typed shoutout, then deletes the account and
checks that the passkey, the password and the post are gone.

    E:/AI_RADIO/.venv/Scripts/python.exe tests/account_test.py --base http://127.0.0.1:8011
"""
import argparse
import base64
import hashlib
import json
import secrets
import sys
import uuid

import cbor2
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ORIGIN = "http://localhost:3000"
RP_ID = "localhost"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftPasskey:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.user_handle = None
        self.count = 0

    def _cose(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    @staticmethod
    def _client_data(kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        self.user_handle = options["user"]["id"]
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        auth_data = (rp_hash + bytes([0x45]) + (0).to_bytes(4, "big") + bytes(16)
                     + len(self.credential_id).to_bytes(2, "big") + self.credential_id + self._cose())
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(self.credential_id), "rawId": b64u(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(self._client_data("webauthn.create", options["challenge"])),
                         "attestationObject": b64u(attestation), "transports": ["internal"]},
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }

    def get(self, options: dict) -> dict:
        self.count += 1
        auth_data = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x05]) + self.count.to_bytes(4, "big")
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64u(self.credential_id), "rawId": b64u(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(client_data), "authenticatorData": b64u(auth_data),
                         "signature": b64u(signature), "userHandle": self.user_handle},
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = None
        self.guest = f"guest_{uuid.uuid4()}"

    def call(self, method: str, path: str, body=None, expect=200):
        headers = {"Origin": ORIGIN, "X-Guest-ID": self.guest, "User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        res = requests.request(method, f"{self.base}/api{path}", json=body, headers=headers, timeout=60)
        if res.status_code != expect:
            raise AssertionError(f"{method} {path}: expected {expect}, got {res.status_code} {res.text[:300]}")
        return res.json() if res.content else {}


def check(label: str, ok: bool):
    print(f"  {'OK  ' if ok else 'FAIL'} {label}")
    if not ok:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    args = parser.parse_args()

    posted = None
    track_id = None
    username = f"passkey_test_{secrets.token_hex(3)}"
    phone = Client(args.base)
    key = SoftPasskey()

    print("Sign up with a passkey")
    start = phone.call("POST", "/auth/passkey/signup/options", {"username": username})
    signed = phone.call("POST", "/auth/passkey/signup", {"request_id": start["request_id"], "credential": key.create(start["options"])})
    phone.token = signed["token"]
    me = phone.call("GET", "/auth/me")
    check(f"account {username} created without a password", me["username"] == username and me["has_password"] is False)
    user_id = me["id"]

    try:
        print("Sign in with the passkey")
        laptop = Client(args.base)
        opts = laptop.call("POST", "/auth/passkey/login/options", {})
        result = laptop.call("POST", "/auth/passkey/login", {"request_id": opts["request_id"], "credential": key.get(opts["options"])})
        check("passkey sign-in returns the same user", result["user"]["id"] == user_id)
        replay = laptop.call("POST", "/auth/passkey/login", {"request_id": opts["request_id"], "credential": key.get(opts["options"])}, expect=401)
        check("a used sign-in request can't be replayed", "expired" in replay.get("detail", ""))

        print("Add a second passkey")
        second = SoftPasskey()
        add = phone.call("POST", "/auth/passkeys/options", {})
        check("existing passkey is excluded", any(c["id"] == b64u(key.credential_id) for c in add["options"].get("excludeCredentials", [])))
        phone.call("POST", "/auth/passkeys", {"request_id": add["request_id"], "credential": second.create(add["options"])})
        passkeys = phone.call("GET", "/auth/passkeys")["passkeys"]
        check("two passkeys listed", len(passkeys) == 2)

        print("Link a new device by code")
        tv = Client(args.base)
        link = tv.call("POST", "/auth/link/start", {})
        check("pending before approval", tv.call("POST", "/auth/link/poll", {"code": link["code"], "poll_key": link["poll_key"]})["status"] == "pending")
        check("signed-in device sees the new device", "Chrome" in phone.call("GET", f"/auth/link/{link['code'].lower()}")["device"])
        phone.call("POST", f"/auth/link/{link['code']}/approve", {})
        wrong = tv.call("POST", "/auth/link/poll", {"code": link["code"], "poll_key": "nope"})
        check("wrong poll key gets nothing", wrong["status"] == "expired")
        approved = tv.call("POST", "/auth/link/poll", {"code": link["code"], "poll_key": link["poll_key"]})
        check("new device signed in", approved["status"] == "approved" and approved["user"]["id"] == user_id)
        check("code is single use", tv.call("POST", "/auth/link/poll", {"code": link["code"], "poll_key": link["poll_key"]})["status"] == "expired")

        print("Optional password")
        phone.call("PUT", "/auth/password", {"password": "hunter22"})
        check("me reports a password", phone.call("GET", "/auth/me")["has_password"] is True)
        laptop.token = None
        check("password sign-in works", laptop.call("POST", "/auth/login", {"username": username, "password": "hunter22"})["user"]["id"] == user_id)

        print("Remove a passkey")
        phone.call("DELETE", f"/auth/passkeys/{passkeys[1]['id']}")
        check("one passkey left", len(phone.call("GET", "/auth/passkeys")["passkeys"]) == 1)

        print("Review a song, then delete the account")
        track_id = phone.call("GET", "/catalog/tracks?limit=1")["tracks"][0]["id"]
        phone.call("POST", f"/tracks/{track_id}/reviews/text",
                   {"text": "Love how the chorus opens up, the synth line stays in my head all day."})
        reviews = phone.call("GET", "/user/community").get("reviews", [])
        posted = reviews[0]["id"] if reviews else None
        print(f"  review {'saved' if posted else 'not kept by the editor'}")
    finally:
        summary = phone.call("DELETE", "/auth/account")
        print(f"  deleted: {summary}")

    print("Everything is gone")
    phone.call("GET", "/auth/me", expect=401)
    check("token no longer works", True)
    opts = laptop.call("POST", "/auth/passkey/login/options", {})
    laptop.token = None
    gone = laptop.call("POST", "/auth/passkey/login", {"request_id": opts["request_id"], "credential": key.get(opts["options"])}, expect=401)
    check("passkey no longer signs in", "isn't linked" in gone.get("detail", ""))
    laptop.call("POST", "/auth/login", {"username": username, "password": "hunter22"}, expect=401)
    check("password no longer signs in", True)
    if posted:
        on_track = laptop.call("GET", f"/tracks/{track_id}/reviews")
        ids = [r.get("id") for r in (on_track.get("reviews") if isinstance(on_track, dict) else on_track) or []]
        check("review removed from the song", posted not in ids)
    print("All account checks passed.")


if __name__ == "__main__":
    main()

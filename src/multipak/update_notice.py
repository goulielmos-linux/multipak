"""Signed release policy and shared reminder state for the desktop applications."""

import base64
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

POLICY_URL = "https://goulielmos-linux.github.io/multipak/update-policy.json"
APP_ID = "io.github.goulielmos_linux.multipak"
DAY = 24 * 60 * 60
CHECK_INTERVAL = 60 * 60
MAX_POLICY = 65536


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,8}\.\d{1,8}\.\d{1,8}", value):
        raise ValueError("Invalid stable release version")
    return tuple(map(int, value.split(".")))


@dataclass(frozen=True)
class Policy:
    latest: str
    minimum: str

    def available(self, current):
        return version_tuple(current) < version_tuple(self.latest)

    def required(self, current):
        return version_tuple(current) < version_tuple(self.minimum)


def parse_policy(payload):
    if len(payload) > MAX_POLICY:
        raise ValueError("Update policy exceeds its size limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("app_id") != APP_ID:
        raise ValueError("Unknown update policy")
    latest, minimum = value.get("latest_version"), value.get("minimum_supported_version")
    if version_tuple(minimum) > version_tuple(latest):
        raise ValueError("Minimum supported version exceeds latest release")
    return Policy(latest, minimum)


def verify_policy(payload, signature, key):
    if not payload or len(payload) > MAX_POLICY or not signature or len(signature) > 16384:
        raise ValueError("Invalid signed update policy")
    with tempfile.TemporaryDirectory(prefix="multipak-policy-") as directory:
        home = Path(directory)
        data, sig = home / "policy.json", home / "policy.asc"
        data.write_bytes(payload)
        sig.write_bytes(signature)
        subprocess.run(
            ["gpgv", "--homedir", str(home), "--keyring", str(key.resolve()), str(sig), str(data)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=5,
        )
    return parse_policy(payload)


class _SameOrigin(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit

        if urlsplit(newurl)[:2] != urlsplit(POLICY_URL)[:2]:
            raise ValueError("Unexpected update-policy redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_policy(key):
    opener = urllib.request.build_opener(_SameOrigin())
    result = []
    for url in (POLICY_URL, POLICY_URL + ".asc"):
        request = urllib.request.Request(url, headers={"User-Agent": "MultiPak update check"})
        with opener.open(request, timeout=3) as response:
            content = response.read(MAX_POLICY + 1)
        if len(content) > MAX_POLICY:
            raise ValueError("Update response exceeds its size limit")
        result.append(content)
    policy = verify_policy(*result, key)
    return policy, *result


class NoticeState:
    def __init__(self, path=None):
        self.path = (
            path
            or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "multipak/update-notice.json"
        )

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def save(self, **changes):
        with self.locked():
            value = self.read() | changes
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                try:
                    json.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)

    def remember_later(self, now=None):
        self.save(snooze_until=(time.time() if now is None else now) + DAY)

    def should_show(self, policy, current, now=None):
        now = time.time() if now is None else now
        if policy.required(current):
            return True
        until = self.read().get("snooze_until", 0)
        return policy.available(current) and (not isinstance(until, (int, float)) or now >= until)

    def checked_policy(self, key, now=None):
        now = time.time() if now is None else now
        state = self.read()
        cached = None
        try:
            payload = base64.b64decode(state.get("payload", ""), validate=True)
            signature = base64.b64decode(state.get("signature", ""), validate=True)
            cached = verify_policy(payload, signature, key)
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
        last = state.get("checked_at", 0)
        if cached and isinstance(last, (int, float)) and 0 <= now - last < CHECK_INTERVAL:
            return cached
        try:
            policy, payload, signature = fetch_policy(key)
            self.save(
                checked_at=now,
                payload=base64.b64encode(payload).decode(),
                signature=base64.b64encode(signature).decode(),
            )
            return policy
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            return cached

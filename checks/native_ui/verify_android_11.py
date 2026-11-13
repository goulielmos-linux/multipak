"""Download and boot a disposable Android 11 environment with the normal provider."""

import json
import sys
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from multipak import android_instances as environments  # noqa: E402
from multipak import host  # noqa: E402
from multipak.process import Runner  # noqa: E402

host.is_flatpak = lambda: False
output = PROJECT.parent / "work/waydroid-instances-ui"
log = (output / "android-11.log").open("w", buffering=1)
runner = Runner(threading.Event(), lambda s: log.write(s + "\n"))
last = [None]


def progress(percent, status):
    bucket = (percent // 10 if percent >= 0 else -1, status.split(":")[0] if percent < 0 else "")
    if bucket != last[0]:
        print(percent, status, flush=True)
        last[0] = bucket


entries = environments.discover(runner)
if not any(e["id"] == "mpwtestc" and e.get("initialized") for e in entries):
    environments.create(
        runner,
        "android-11",
        "MultiPak - Teste Android 11",
        instance_id="mpwtestc",
        progress=progress,
    )
android = environments.InstanceWaydroid(runner, "mpwtestc")
android.ready(progress)
sdk = android.command("prop", "get", "ro.build.version.sdk").strip()
assert sdk == "30", sdk
other = environments.InstanceWaydroid(runner, "mpwtestb")
other.ready(progress)
assert other.command("prop", "get", "ro.build.version.sdk").strip() == "33"
assert android.apps() and other.apps()
print("MULTIPAK_ANDROID_11_AND_13_PARALLEL_VERIFIED", flush=True)
(output / "android-versions-result.json").write_text(
    json.dumps({"android-11": sdk, "android-13": "33", "simultaneous_boot": True}, indent=2)
)
android.stop()
other.stop()

"""Verify two prepared, disposable mpwtesta/mpwtestb Android environments on the host."""

import json
import sys
import tempfile
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
from multipak import android_instances as environments  # noqa: E402
from multipak import host  # noqa: E402
from multipak.android_bundle import inspect_android  # noqa: E402
from multipak.engine import Engine  # noqa: E402
from multipak.process import Runner  # noqa: E402
from multipak.store import Store  # noqa: E402

host.is_flatpak = lambda: False
out = PROJECT.parent / "work/waydroid-instances-ui"
out.mkdir(exist_ok=True)
log = (out / "live-instances.log").open("w", buffering=1)
runner = Runner(threading.Event(), lambda s: log.write(s + "\n"))
original = Path("/var/lib/waydroid/waydroid.cfg").read_bytes()
applications = Path.home() / ".local/share/applications"
personal = {p.name: p.read_bytes() for p in applications.glob("waydroid.*.desktop")}
entries = environments.discover(runner)
assert {e["id"] for e in entries} >= {"mpwtesta", "mpwtestb"}
with tempfile.TemporaryDirectory(prefix="multipak-live-instances-") as temp:
    store = Store(Path(temp) / "data", Path(temp) / "applications")
    engine = Engine(store, threading.Event(), progress=lambda n, s: print(n, s, flush=True))
    engine.runner = runner
    package = inspect_android(PROJECT.parent / "work/android-parser-sample.apk")
    devices = {}
    for identifier in ("mpwtesta", "mpwtestb"):
        print("Starting", identifier, flush=True)
        android = environments.InstanceWaydroid(runner, identifier)
        android.ready(lambda n, s: print(n, s, flush=True))
        assert android.command("prop", "get", "ro.build.version.sdk").strip() == "33"
        devices[identifier] = android
    first = engine.install_android(
        package,
        instance_id="mpwtesta",
        android_profile="android-13",
        android_label="MultiPak - Teste A",
    )
    assert package.desktop_entry in devices["mpwtesta"].apps()
    assert package.desktop_entry not in devices["mpwtestb"].apps()
    second = engine.install_android(
        package,
        instance_id="mpwtestb",
        android_profile="android-13",
        android_label="MultiPak - Teste B",
    )
    assert package.desktop_entry in devices["mpwtesta"].apps()
    assert package.desktop_entry in devices["mpwtestb"].apps()
    engine.cleanup(first, preserve_environment=True)
    assert package.desktop_entry not in devices["mpwtesta"].apps()
    assert package.desktop_entry in devices["mpwtestb"].apps()
    devices["mpwtesta"].stop()
    assert package.desktop_entry in devices["mpwtestb"].apps()
    engine.cleanup(second, preserve_environment=True)
    devices["mpwtestb"].stop()
assert Path("/var/lib/waydroid/waydroid.cfg").read_bytes() == original
assert all((applications / name).read_bytes() == content for name, content in personal.items())
print("MULTIPAK_TWO_LIVE_INSTANCES_VERIFIED", flush=True)
(out / "live-instances-result.json").write_text(
    json.dumps(
        {
            "instances": ["mpwtesta", "mpwtestb"],
            "simultaneous_boot": True,
            "separate_apps": True,
            "independent_stop": True,
            "personal_config_preserved": True,
            "personal_shortcuts_preserved": True,
        },
        indent=2,
    )
)

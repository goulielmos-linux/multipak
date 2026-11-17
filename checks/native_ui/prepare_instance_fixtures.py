"""Prepare two private Android 13 test instances from read-only local image copies.

Run through pkexec. This fixture avoids downloading identical system images twice.
It never changes the system Waydroid configuration, images, data or services.
"""

import configparser
import hashlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src/multipak"))
import android_instances_helper as helper  # noqa: E402

owner = helper.caller()
helper.deploy(PROJECT / "src/multipak/assets/waydroid-runtime.tar.gz")
source = Path("/var/lib/waydroid")
original = (source / "waydroid.cfg").read_bytes()
cfg = configparser.ConfigParser()
cfg.read_string(original.decode())
# Known official Android 13 image dates in the local test setup.
assert cfg["waydroid"]["system_datetime"] == "1790539062"
assert cfg["waydroid"]["vendor_datetime"] == "1792667170"
checksums = {
    name: hashlib.file_digest((source / "images" / name).open("rb"), "sha256").hexdigest()
    for name in ("system.img", "vendor.img")
}
for identifier, label in (("mpwtesta", "MultiPak - Teste A"), ("mpwtestb", "MultiPak - Teste B")):
    helper.allocate(owner, identifier, label, "android-13")
    # The background Android may be frozen while idle.
    subprocess.run(
        [
            "/usr/bin/lxc-unfreeze",
            "-P",
            str(helper.BASE / "instances" / identifier / "lxc"),
            "-n",
            identifier,
        ],
        capture_output=True,
        timeout=10,
    )
    # Remove only the fixture APK so repeated runs start with neither instance owning it.
    subprocess.run(
        [
            "/usr/bin/lxc-attach",
            "-P",
            str(helper.BASE / "instances" / identifier / "lxc"),
            "-n",
            identifier,
            "--clear-env",
            "--",
            "/system/bin/pm",
            "uninstall",
            "de.rhab.helloworld",
        ],
        capture_output=True,
        timeout=20,
    )
    helper.run(["/usr/bin/systemctl", "stop", f"multipak-waydroid@{identifier}.service"])
    directory = helper.BASE / "instances" / identifier
    images = directory / "images"
    images.mkdir(exist_ok=True)
    for name in checksums:
        target = images / name
        if not target.exists():
            helper.run(
                [
                    "/usr/bin/cp",
                    "--reflink=auto",
                    "--sparse=always",
                    "--",
                    str(source / "images" / name),
                    str(target),
                ]
            )
        assert target.stat().st_ino != (source / "images" / name).stat().st_ino
        assert hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == checksums[name]
    new = configparser.ConfigParser()
    new["waydroid"] = {
        key: cfg["waydroid"][key]
        for key in ("system_ota", "vendor_ota", "system_datetime", "vendor_datetime")
    }
    new["properties"] = {"persist.waydroid.multi_windows": "true"}
    text = io.StringIO()
    new.write(text)
    helper.atomic(directory / "waydroid.cfg", text.getvalue().encode())
    with tempfile.TemporaryDirectory(prefix="multipak-fixture-watch-") as temp:
        watch = Path(temp) / "running"
        watch.touch()
        result = helper.initialize(identifier, str(watch))
        assert result == 0, result
    assert (directory / "lxc" / identifier / "config").is_file()
    helper.run(["/usr/bin/systemctl", "enable", "--now", f"multipak-waydroid@{identifier}.service"])
assert (source / "waydroid.cfg").read_bytes() == original
assert all(
    hashlib.file_digest((source / "images" / name).open("rb"), "sha256").hexdigest() == digest
    for name, digest in checksums.items()
)
print("MULTIPAK_TEST_FIXTURES_READY", flush=True)

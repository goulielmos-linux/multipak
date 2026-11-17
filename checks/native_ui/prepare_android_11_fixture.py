"""Prepare one disposable Android 11 environment from verified official archives."""

import configparser
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src/multipak"))
import android_instances_helper as helper  # noqa: E402

owner = helper.caller()
identifier = "mpwtestc"
helper.allocate(owner, identifier, "MultiPak - Teste Android 11", "android-11")
directory = helper.BASE / "instances" / identifier
images = directory / "images"
images.mkdir(exist_ok=True)
values = {}
for channel, filename in (
    ("android-system-channel.json", "system.img"),
    ("android-vendor-channel.json", "vendor.img"),
):
    data = json.loads((PROJECT.parent / "work/arm-reference" / channel).read_text())
    record = max(
        (r for r in data["response"] if r["version"] == "18.1"), key=lambda r: r["datetime"]
    )
    archive = PROJECT.parent / "work/arm-reference/android11-images" / record["filename"]
    with archive.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == record["id"]
    with zipfile.ZipFile(archive) as payload:
        payload.extract(filename, images)
    prefix = "system" if filename == "system.img" else "vendor"
    values[prefix + "_ota"] = (
        "https://ota.waydro.id/system/lineage/waydroid_x86_64/VANILLA.json"
        if prefix == "system"
        else "https://ota.waydro.id/vendor/waydroid_x86_64/MAINLINE.json"
    )
    values[prefix + "_datetime"] = str(record["datetime"])
cfg = configparser.ConfigParser()
cfg["waydroid"] = values
cfg["properties"] = {"persist.waydroid.multi_windows": "true"}
text = io.StringIO()
cfg.write(text)
helper.atomic(directory / "waydroid.cfg", text.getvalue().encode())
with tempfile.TemporaryDirectory(prefix="multipak-11-watch-") as temp:
    watch = Path(temp) / "running"
    watch.touch()
    assert helper.initialize(identifier, str(watch)) == 0
helper.run(["/usr/bin/systemctl", "enable", "--now", f"multipak-waydroid@{identifier}.service"])
print("MULTIPAK_ANDROID_11_FIXTURE_READY", flush=True)

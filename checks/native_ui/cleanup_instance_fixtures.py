"""Remove only the disposable Android environments created by native checks."""

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src/multipak"))
import android_instances_helper as helper  # noqa: E402

owner = helper.caller()
original = Path("/var/lib/waydroid/waydroid.cfg").read_bytes()
for identifier in ("mpwtesta", "mpwtestb", "mpwtestc"):
    if not (helper.BASE / "instances" / identifier / "instance.json").is_file():
        continue
    info = helper.metadata(identifier, owner)
    assert info["label"].startswith("MultiPak - Teste")
    helper.remove(owner, identifier, True)
    applications = Path(info["home"]) / ".local/share/applications"
    for shortcut in applications.glob(f"multipak-waydroid.{identifier}.*.desktop"):
        shortcut.unlink()
assert Path("/var/lib/waydroid/waydroid.cfg").read_bytes() == original
print("MULTIPAK_TEST_ENVIRONMENTS_CLEANED", flush=True)

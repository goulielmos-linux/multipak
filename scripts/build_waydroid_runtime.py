"""Build the reviewed, vendored Waydroid runtime as a deterministic resource."""

import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "vendor/waydroid"
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
    for path in sorted(source.rglob("*")):
        if (
            not path.is_file()
            or {"__pycache__", ".ruff_cache", ".pytest_cache"}.intersection(path.parts)
            or path.suffix == ".pyc"
        ):
            continue
        member = tarfile.TarInfo(str(path.relative_to(source)))
        data = path.read_bytes()
        member.size = len(data)
        member.mtime = 0
        member.mode = 0o755 if path.suffix == ".sh" or path.name == "multipak-waydroid" else 0o644
        archive.addfile(member, io.BytesIO(data))
data = gzip.compress(buffer.getvalue(), mtime=0)
(root / "src/multipak/assets/waydroid-runtime.tar.gz").write_bytes(data)
helper = root / "src/multipak/android_instances_helper.py"
helper.write_text(
    re.sub(
        r"RUNTIME_SHA256 = ['\"][^'\"]+['\"]",
        "RUNTIME_SHA256 = " + repr(hashlib.sha256(data).hexdigest()),
        helper.read_text(),
    )
)
print(
    "Waydroid 1.6.3 instance runtime:", len(data), "bytes; sha256", hashlib.sha256(data).hexdigest()
)

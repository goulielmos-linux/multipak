"""Build the deterministic privileged installation bundle for system RPM installation."""

import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/multipak"
FILES = ["privileged_helper.py", "privileged_files.py", "system_install_helper.py"]
files = {name: (SOURCE / name).read_bytes() for name in FILES}
files["multipak.policy"] = (ROOT / "packaging/polkit/io.github.multipak.manage.policy").read_bytes()
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
    for name, data in sorted(files.items()):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(data))
data = gzip.compress(buffer.getvalue(), mtime=0)
digest = hashlib.sha256(data).hexdigest()
(SOURCE / "assets/privileged.tar.gz").write_bytes(data)
(SOURCE / "assets/privileged.sha256").write_text(digest + "\n")
bootstrap = SOURCE / "bootstrap_helpers.py"
bootstrap.write_text(
    re.sub(r'BUNDLE_SHA256 = "[^"]+"', 'BUNDLE_SHA256 = "' + digest + '"', bootstrap.read_text())
)
print("Privileged bundle:", digest)

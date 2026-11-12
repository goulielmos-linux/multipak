"""One-time authenticated installation of the reviewed privileged code bundle."""

import errno
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

BUNDLE_SHA256 = "168ac6e87f2cb91bffaf7918b53c9fd6f364e820e2fcdd005c3dbe652afc6a14"
BASE = Path("/usr/local/libexec/multipak-system")


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise PermissionError("Administrative installation is required")
    with Path(sys.argv[1]).open("rb") as stream:
        data = stream.read(8 * 1024**2 + 1)
    if len(data) > 8 * 1024**2 or hashlib.sha256(data).hexdigest() != BUNDLE_SHA256:
        raise ValueError("The privileged code bundle failed verification")
    base = BASE.resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o755)
    for path in (base, *base.parents):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError("The privileged installation path is not protected")
    versions = base / "versions"
    versions.mkdir(mode=0o755, exist_ok=True)
    target = versions / BUNDLE_SHA256
    with tempfile.TemporaryDirectory(prefix=".install-", dir=versions) as directory:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.size > 4 * 1024**2:
                    raise ValueError("Unexpected privileged bundle member")
            archive.extractall(directory, filter="data")
        for path in Path(directory).rglob("*"):
            path.chmod(0o755 if path.is_dir() or path.name == "privileged_helper.py" else 0o644)
        Path(directory).chmod(0o755)
        if not target.exists():
            Path(directory).rename(target)
    link = base / ".manage-new"
    link.unlink(missing_ok=True)
    link.symlink_to(target / "privileged_helper.py")
    link.replace(base / "manage")
    policy_installed = False
    policy = Path("/usr/share/polkit-1/actions/io.github.multipak.system-install.policy")
    try:
        descriptor, name = tempfile.mkstemp(prefix=".multipak-", dir=policy.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((target / "multipak.policy").read_bytes())
                os.fchmod(stream.fileno(), 0o644)
            os.replace(name, policy)
            policy_installed = True
        finally:
            Path(name).unlink(missing_ok=True)
    except OSError as error:
        if error.errno not in (errno.EROFS, errno.ENOENT):
            raise
        # Atomic systems still require normal administrative authorization for
        # the fixed installed executable. Never grant generic Python execution.
    descriptor, name = tempfile.mkstemp(prefix=".manifest-", dir=base)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"bundle_sha256": BUNDLE_SHA256, "policy_installed": policy_installed}, stream)
        os.fchmod(stream.fileno(), 0o644)
    os.replace(name, base / "manifest.json")
    print("MULTIPAK_PRIVILEGED_HELPERS_READY", flush=True)


if __name__ == "__main__":
    main()

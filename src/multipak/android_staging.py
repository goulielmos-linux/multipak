"""Unprivileged APK exchange, including when the frontend runs in Flatpak."""

import re
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from . import host


def remote(waydroid):
    return bool(getattr(waydroid, "instance_id", "")) and host.is_flatpak()


def _destination(waydroid, name):
    if not re.fullmatch(r"multipak-(?:obb-)?[A-Za-z0-9]{12}", name):
        raise ValueError("Invalid Android staging directory")
    return waydroid.staging_directory() / name


@contextmanager
def directory(waydroid, prefix):
    shared = waydroid.staging_directory()
    if not remote(waydroid):
        shared.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=prefix, dir=shared) as name:
            path = Path(name)
            if getattr(waydroid, "instance_id", ""):
                path.chmod(0o755)
            yield path
        return
    # Only the regular user runs these host file operations. The root service
    # continues to mount a fixed exchange directory with a root-owned parent.
    output = waydroid.runner.run(
        ["mktemp", "-d", "--tmpdir=" + str(shared), prefix + "XXXXXXXXXXXX"], timeout=30
    ).strip()
    target = Path(output)
    if target.parent != shared or target != _destination(waydroid, target.name):
        raise ValueError("Unexpected Android staging directory")
    try:
        waydroid.runner.run(["chmod", "0755", "--", str(target)], timeout=30)
        with TemporaryDirectory(
            prefix="multipak-exchange-", dir=host.temporary_directory()
        ) as local:
            path = Path(local) / target.name
            path.mkdir()
            yield path
    finally:
        # Cancellation of the main runner must not suppress temporary-file cleanup.
        result = host.run(
            ["rm", "-rf", "--", str(target)], capture_output=True, text=True, timeout=30
        )
        if result.returncode:
            waydroid.runner.log(result.stderr)


def publish(waydroid, path):
    if not remote(waydroid):
        return
    target = _destination(waydroid, path.name)
    for item in sorted(path.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError("Unexpected Android staging entry")
        with host.accessible_file(item) as source:
            waydroid.runner.run(
                [
                    "cp",
                    "--reflink=auto",
                    "--no-clobber",
                    "--",
                    str(source),
                    str(target / item.name),
                ],
                timeout=1200,
            )

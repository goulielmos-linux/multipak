"""Atomic journals, a single-writer lock, and strictly scoped application data."""

import dataclasses
import fcntl
import json
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from multipak.localization import tr

from . import host
from .models import Transaction, valid_environment_name

_TRANSACTION_FIELDS = frozenset(field.name for field in dataclasses.fields(Transaction))


def _allow_owner(root) -> None:
    """Give the owner full access to every folder under root, without following links."""
    pending = [os.fspath(root)]
    while pending:
        current = pending.pop()
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            continue
        if not stat.S_ISDIR(mode):
            continue
        if mode & 0o700 != 0o700:
            try:
                os.chmod(current, stat.S_IMODE(mode) | 0o700)
            except OSError:
                continue
        try:
            with os.scandir(current) as entries:
                pending.extend(e.path for e in entries if e.is_dir(follow_symlinks=False))
        except OSError:
            continue


def remove_tree(path, *, ignore_errors: bool = False) -> None:
    """shutil.rmtree that also removes folders their owner cannot read or change.

    Snaps ship such folders: Ubuntu's bases have var/lib/snapd/void, which nobody may
    open. Extracted as a normal user, it keeps that mode and plain rmtree stops on it.
    """
    try:
        shutil.rmtree(path)
    except PermissionError:
        _allow_owner(path)
        shutil.rmtree(path, ignore_errors=ignore_errors)
    except OSError:
        if not ignore_errors:
            raise


class Store:
    def __init__(self, root: Path | None = None, applications: Path | None = None):
        data = host.directories()[0]
        self.root = (root or data / "multipak" / "installations").resolve()
        self.applications = (applications or data / "applications").resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.applications.mkdir(parents=True, exist_ok=True)

    def directory(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
            raise ValueError(tr("Invalid installation ID."))
        path = self.root / transaction_id
        if path.is_symlink():
            raise ValueError(tr("The installation directory must not be a symbolic link."))
        return path

    def save(self, tx: Transaction) -> None:
        directory = self.directory(tx.id)
        directory.mkdir(mode=0o700, exist_ok=True)
        temporary = directory / "manifest.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(tx.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "manifest.json")
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def environment_directory(self, name: str) -> Path:
        if not valid_environment_name(name):
            raise ValueError(tr("Invalid managed environment name."))
        path = self.root / "environments" / name
        if path.is_symlink():
            raise ValueError(tr("The environment directory must not be a symbolic link."))
        return path

    def appimage_directory(self, transaction_id: str) -> Path:
        self.directory(transaction_id)  # Validate the ID before constructing a managed path.
        path = self.root.parent / "appimages" / transaction_id
        if path.is_symlink():
            raise ValueError(tr("The AppImage directory must not be a symbolic link."))
        return path

    def snap_directory(self, transaction_id: str) -> Path:
        self.directory(transaction_id)  # Validate the ID before constructing a managed path.
        path = self.root.parent / "snaps" / transaction_id
        if path.is_symlink():
            raise ValueError(tr("The snap directory must not be a symbolic link."))
        return path

    def snap_runtime_directory(self) -> Path:
        """Bases and content snaps shared by the installed snaps."""
        path = self.root.parent / "snap-runtime"
        if path.is_symlink():
            raise ValueError(tr("The snap directory must not be a symbolic link."))
        return path

    def snap_download_directory(self) -> Path:
        """Snaps downloaded from the Snap Store, until they are installed."""
        path = self.root.parent / "snap-downloads"
        if path.is_symlink():
            raise ValueError(tr("The snap directory must not be a symbolic link."))
        return path

    def other_references(self, tx: Transaction) -> list[Transaction]:
        return [
            item
            for item in self.transactions()
            if item.id != tx.id
            and item.method == "distrobox"
            and item.container_id
            and item.container_id == tx.container_id
        ]

    def transactions(self) -> list[Transaction]:
        result = []
        for path in sorted(self.root.glob("*/manifest.json")):
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    continue
                # Ignore fields written by another MultiPak version instead of
                # hiding the whole installation from the manager.
                tx = Transaction(**{k: v for k, v in data.items() if k in _TRANSACTION_FIELDS})
                if self.directory(tx.id) != path.parent:
                    continue
                result.append(tx)
            except (ValueError, TypeError, OSError):
                continue  # A malformed record is never authority to remove anything.
        return result

    def launcher(self, tx: Transaction, name: str) -> Path:
        expected = f"multipak-{tx.id}-"
        android_launcher = (
            tx.method == "waydroid"
            and re.fullmatch(r"[a-z][a-z0-9]{0,10}", tx.android_instance_id)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", tx.android_package_id)
            and name
            == f"multipak-waydroid.{tx.android_instance_id}.{tx.android_package_id}.desktop"
        )
        if (
            not (name.startswith(expected) or android_launcher)
            or Path(name).name != name
            or not name.endswith(".desktop")
        ):
            raise ValueError(tr("This shortcut does not belong to this installation."))
        return self.applications / name

    def remove_directory(self, tx: Transaction) -> None:
        remove_tree(self.directory(tx.id))

    @contextmanager
    def lock(self, wait=0):
        """The lock of every operation. wait: seconds to wait for another operation
        to end, for requests from a running app that should not fail at once."""
        with (self.root / "operations.lock").open("a") as stream:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            tr("Another installation is already in progress.")
                        ) from exc
                    time.sleep(0.5)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

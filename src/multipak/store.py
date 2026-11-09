"""Atomic journals, a single-writer lock, and strictly scoped application data."""

import fcntl
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

from multipak.localization import tr

from . import host
from .models import Transaction, valid_environment_name


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
                tx = Transaction(**json.loads(path.read_text()))
                if self.directory(tx.id) != path.parent:
                    continue
                result.append(tx)
            except (ValueError, TypeError, OSError):
                continue  # A malformed record is never authority to remove anything.
        return result

    def launcher(self, tx: Transaction, name: str) -> Path:
        expected = f"multipak-{tx.id}-"
        if (
            not name.startswith(expected)
            or Path(name).name != name
            or not name.endswith(".desktop")
        ):
            raise ValueError(tr("This shortcut does not belong to this installation."))
        return self.applications / name

    def remove_directory(self, tx: Transaction) -> None:
        shutil.rmtree(self.directory(tx.id))

    @contextmanager
    def lock(self):
        with (self.root / "operations.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(tr("Another installation is already in progress.")) from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

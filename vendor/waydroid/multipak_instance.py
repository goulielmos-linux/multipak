# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit instance context for the MultiPak Waydroid runtime."""

import dataclasses
import json
import os
import re
import shlex
from pathlib import Path

BASE = Path("/var/lib/multipak-waydroid/instances")
CLI = "/usr/local/bin/multipak-waydroid"
_current = None


@dataclasses.dataclass(frozen=True)
class Instance:
    id: str
    owner: int
    label: str
    release: str
    network_slot: int
    home: str
    data_layout: int = 1

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9]{0,10}", self.id):
            raise ValueError("Invalid Android instance ID")
        if not 1 <= self.network_slot <= 200 or self.owner < 1:
            raise ValueError("Invalid Android instance allocation")
        if self.data_layout not in (1, 2):
            raise ValueError("Unsupported Android data layout")
        if self.release not in ("18.1", "20.0"):
            raise ValueError("Unsupported Android release")
        if not Path(self.home).is_absolute() or any(c in self.home for c in "\r\n\0"):
            raise ValueError("Invalid Android data location")
        if not self.label or len(self.label) > 64 or any(ord(c) < 32 for c in self.label):
            raise ValueError("Invalid Android environment label")

    @property
    def work(self):
        return BASE / self.id

    @property
    def user_state(self):
        return Path(self.home) / ".local/share/multipak/android-instances" / self.id

    @property
    def data(self):
        return self.work / "private/data"

    @property
    def exchange(self):
        return self.work / "exchange"

    @property
    def sockets(self):
        return Path("/run/multipak-waydroid-" + self.id) / "sockets"

    @property
    def container_bus(self):
        return "id.waydro.MultiPak.Container." + self.id

    @property
    def session_bus(self):
        return "id.waydro.MultiPak.Session." + self.id

    @property
    def bridge(self):
        return "mpw" + self.id

    @property
    def subnet(self):
        return f"10.203.{self.network_slot}.0/24"

    @property
    def gateway(self):
        return f"10.203.{self.network_slot}.1"

    @property
    def binderfs(self):
        return "/dev/multipak-binder-" + self.id

    @property
    def initialized(self):
        return (self.work / "rootfs").is_dir() and all(
            (self.work / name).is_file() and (self.work / name).stat().st_size > 0
            for name in (
                "waydroid.cfg",
                "waydroid_base.prop",
                "images/system.img",
                "images/vendor.img",
                f"lxc/{self.id}/config",
                f"lxc/{self.id}/config_nodes",
            )
        )

    @property
    def desktop_glob(self):
        return f"multipak-waydroid.{self.id}.*.desktop"

    def desktop_name(self, package):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
            raise ValueError("Invalid Android package ID")
        return f"multipak-waydroid.{self.id}.{package}.desktop"

    def command(self, *args):
        return shlex.join([CLI, "--instance", self.id, *args])

    def to_dict(self):
        return dataclasses.asdict(self)


def configure(identifier):
    global _current
    if not re.fullmatch(r"[a-z][a-z0-9]{0,10}", identifier):
        raise ValueError("An explicit named Android instance is required")
    path = BASE / identifier / "instance.json"
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError("Unexpected Android instance path")
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("Android instance metadata must be root-owned")
    instance = Instance(**json.loads(path.read_text()))
    if instance.id != identifier or os.geteuid() not in (0, instance.owner):
        raise PermissionError("Android instance belongs to another user")
    _current = instance
    return instance


def current():
    if _current is None:
        raise RuntimeError("An explicit Android instance is required")
    return _current


def select_release(records):
    result = [r for r in records if r.get("version") == current().release]
    if not result:
        raise RuntimeError("No images found for the selected Android release")
    return sorted(result, key=lambda r: int(r["datetime"]), reverse=True)

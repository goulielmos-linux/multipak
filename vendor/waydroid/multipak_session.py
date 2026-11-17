# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated sessions and pinned sockets for the named-instance root service."""

import os
import pwd
import re
import signal
import stat
import subprocess
from pathlib import Path

from multipak_instance import current as instance

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def _directory(path):
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            following = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _protected(path, *, private=False):
    descriptor = _directory(path)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or info.st_mode & (0o077 if private else 0o022):
            raise PermissionError("An Android control directory is not protected")
    finally:
        os.close(descriptor)


def validate_layout(*, sockets=False):
    inst = instance()
    _protected(inst.work)
    _protected(inst.data.parent, private=True)
    _protected(inst.exchange)
    for path in (inst.data, inst.exchange / "apks", inst.exchange / "icons"):
        descriptor = _directory(path)
        os.close(descriptor)
    if sockets:
        _protected(inst.sockets.parent)
        _protected(inst.sockets, private=True)
        for name in ("wayland", "pulse"):
            path = inst.sockets / name
            if name == "wayland" or os.path.ismount(path):
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != inst.owner:
                    raise PermissionError("An Android desktop socket is not pinned")
                if not os.path.ismount(path):
                    raise PermissionError("An Android desktop socket is not mounted")


def _pin(runtime, relative, target, uid):
    directory = _directory(runtime)
    descriptor = None
    try:
        for part in relative[:-1]:
            following = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        descriptor = os.open(relative[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            raise PermissionError("The desktop endpoint must be a socket owned by the caller")
        if target.is_symlink():
            raise ValueError("Unexpected socket staging path")
        if os.path.ismount(target):
            subprocess.run(["/usr/bin/umount", str(target)], check=True, env=ENV, timeout=10)
        target.touch(mode=0o600)
        subprocess.run(
            ["/usr/bin/mount", "--no-canonicalize", "--bind", f"/proc/self/fd/{descriptor}", str(target)],
            pass_fds=(descriptor,),
            check=True,
            env=ENV,
            timeout=10,
            stdin=subprocess.DEVNULL,
            cwd="/",
        )
        pinned = target.stat()
        if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino):
            raise PermissionError("The staged socket does not match the opened endpoint")
    finally:
        os.close(directory)
        if descriptor is not None:
            os.close(descriptor)


def notify_stopped(session):
    # Never signal a host process solely by a PID which may already have been reused.
    pid = int(session["pid"])
    descriptor = os.pidfd_open(pid)
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        match = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
        if not match or any(int(value) != instance().owner for value in match.groups()):
            raise PermissionError("The session process no longer belongs to its owner")
        signal.pidfd_send_signal(descriptor, signal.SIGUSR1)
    finally:
        os.close(descriptor)


def release_sockets():
    root = instance().sockets
    for name in ("wayland", "pulse"):
        target = root / name
        if os.path.ismount(target):
            subprocess.run(["/usr/bin/umount", str(target)], check=True, env=ENV, timeout=10)
        if not target.is_symlink():
            target.unlink(missing_ok=True)


def prepare(session, uid, pid):
    inst = instance()
    if uid != inst.owner or uid <= 0 or str(session.get("user_id")) != str(uid):
        raise PermissionError("Android instance belongs to another user")
    if str(session.get("pid")) != str(pid) or pid <= 1:
        raise ValueError("Invalid session process")
    if inst.data_layout != 2:
        raise RuntimeError("Update and migrate this Android environment before starting it")
    display = session.get("wayland_display", "")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", display) or ".." in display:
        raise ValueError("Invalid Wayland display name")
    density = str(session.get("lcd_density", "0"))
    if not density.isdigit() or not 0 <= int(density) <= 1000:
        raise ValueError("Invalid display density")
    background = session.get("background_start", "true")
    if background not in ("true", "false"):
        raise ValueError("Invalid session mode")
    owner = pwd.getpwuid(uid)
    # No host mount source or identity comes from the caller's dictionary.
    runtime = Path("/run/user") / str(uid)
    validate_layout()
    directory = _directory(runtime)
    try:
        if os.fstat(directory).st_uid != uid:
            raise PermissionError("Invalid desktop runtime owner")
    finally:
        os.close(directory)
    inst.sockets.parent.mkdir(mode=0o755, exist_ok=True)
    _protected(inst.sockets.parent)
    inst.sockets.mkdir(mode=0o700, exist_ok=True)
    if inst.sockets.is_symlink() or inst.sockets.stat().st_uid != 0:
        raise PermissionError("Invalid socket staging directory")
    os.chmod(inst.sockets, 0o700)
    try:
        _pin(runtime, (display,), inst.sockets / "wayland", uid)
        try:
            _pin(runtime, ("pulse", "native"), inst.sockets / "pulse", uid)
        except FileNotFoundError:
            # Audio is optional; discard any previous session's staged endpoint.
            old = inst.sockets / "pulse"
            if os.path.ismount(old):
                subprocess.run(["/usr/bin/umount", str(old)], check=True, env=ENV, timeout=10)
            old.unlink(missing_ok=True)
    except BaseException:
        release_sockets()
        raise
    return {
        "user_id": str(uid),
        "group_id": str(owner.pw_gid),
        "user_name": owner.pw_name,
        "pid": str(pid),
        "host_user": str(Path(owner.pw_dir).resolve()),
        "xdg_runtime_dir": str(runtime),
        "wayland_display": display,
        "pulse_runtime_path": str(runtime / "pulse"),
        "waydroid_data": str(inst.data),
        "waydroid_user_state": str(inst.user_state),
        "xdg_data_home": str(Path(owner.pw_dir).resolve() / ".local/share"),
        "lcd_density": density,
        "background_start": background,
        "state": "STOPPED",
    }

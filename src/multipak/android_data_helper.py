"""Descriptor-based migration from user data to a root-protected Android data area."""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

if __package__:
    from .privileged_files import open_directory
else:
    from privileged_files import open_directory


def _entries(directory):
    # A directory FD can retain a readdir cookie from another consumer. On the
    # host's Btrfs/Python combination, listdir starts at that cookie and only
    # rewinds afterwards. Every independent traversal must start at zero.
    os.lseek(directory, 0, os.SEEK_SET)
    return sorted(os.listdir(directory))


def _metadata(source, target):
    info = os.fstat(source)
    os.fchown(target, info.st_uid, info.st_gid)
    os.fchmod(target, stat.S_IMODE(info.st_mode))
    for attribute in os.listxattr(source):
        os.setxattr(target, attribute, os.getxattr(source, attribute))
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.fsync(target)


def _copy_tree(source, target, _links=None):
    """Copy through pinned directories, preserving ownership, attributes and hardlinks."""
    links = {} if _links is None else _links
    try:
        for name in _entries(source):
            before = os.stat(name, dir_fd=source, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                os.symlink(os.readlink(name, dir_fd=source), name, dir_fd=target)
                os.chown(name, before.st_uid, before.st_gid, dir_fd=target, follow_symlinks=False)
                os.utime(
                    name,
                    ns=(before.st_atime_ns, before.st_mtime_ns),
                    dir_fd=target,
                    follow_symlinks=False,
                )
                continue
            if stat.S_ISSOCK(before.st_mode):
                continue
            if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
                raise ValueError("Unexpected special file in Android data")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(before.st_mode):
                flags |= os.O_DIRECTORY
            incoming = os.open(name, flags, dir_fd=source)
            outgoing = None
            try:
                info = os.fstat(incoming)
                if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("Android data changed during migration")
                identity = (info.st_dev, info.st_ino)
                if stat.S_ISDIR(info.st_mode):
                    os.mkdir(name, 0o700, dir_fd=target)
                    outgoing = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target
                    )
                    _copy_tree(incoming, outgoing, links)
                elif identity in links:
                    original, leaf = links[identity]
                    os.link(
                        leaf, name, src_dir_fd=original, dst_dir_fd=target, follow_symlinks=False
                    )
                else:
                    outgoing = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=target,
                    )
                    while data := os.read(incoming, 1024 * 1024):
                        view = memoryview(data)
                        while view:
                            view = view[os.write(outgoing, view) :]
                    after = os.fstat(incoming)
                    if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Android data changed during migration")
                    _metadata(incoming, outgoing)
                    if info.st_nlink > 1:
                        links[identity] = (os.dup(target), name)
            finally:
                os.close(incoming)
                if outgoing is not None:
                    os.close(outgoing)
        _metadata(source, target)
    finally:
        if _links is None:
            for descriptor, _ in links.values():
                os.close(descriptor)


def _fingerprint(directory):
    """Frame every name and hash each payload separately to avoid ambiguous trees."""
    digest = hashlib.sha256()
    for name in _entries(directory):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISSOCK(info.st_mode):
            continue
        encoded = os.fsencode(name)
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        if stat.S_ISLNK(info.st_mode):
            payload = hashlib.sha256(os.fsencode(os.readlink(name, dir_fd=directory))).digest()
            digest.update(b"L" + payload)
        elif stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            fd = os.open(name, flags, dir_fd=directory)
            try:
                opened = os.fstat(fd)
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("Android data changed during verification")
                if stat.S_ISDIR(info.st_mode):
                    digest.update(b"D" + _fingerprint(fd))
                else:
                    content = hashlib.sha256()
                    while data := os.read(fd, 1024 * 1024):
                        content.update(data)
                    after = os.fstat(fd)
                    if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Android data changed during verification")
                    digest.update(b"F" + content.digest())
            finally:
                os.close(fd)
        else:
            raise ValueError("Unexpected special file in Android data")
    return digest.digest()


def prepare_layout(directory, owner):
    private = directory / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    if private.is_symlink() or private.stat().st_uid != 0:
        raise ValueError("The private Android data parent must be root-owned")
    private.chmod(0o700)
    exchange = directory / "exchange"
    exchange.mkdir(mode=0o755, exist_ok=True)
    if exchange.is_symlink() or exchange.stat().st_uid != 0:
        raise ValueError("The Android exchange parent must be root-owned")
    exchange.chmod(0o755)
    for name in ("apks", "icons"):
        path = exchange / name
        path.mkdir(mode=0o755, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Unexpected Android exchange path")
        path.chmod(0o755)
        # Android's system service writes icons as UID 1000. Other host users
        # only need to read them; APK staging belongs to the authenticated user.
        uid, gid = (owner.pw_uid, owner.pw_gid) if name == "apks" else (1000, 1000)
        os.chown(path, uid, gid)
    return private / "data"


def _export_icons(source, target):
    # This is a public image cache, not a second copy of arbitrary Android data.
    for name in _entries(source):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+\.png", name):
            continue
        info = os.stat(name, dir_fd=source, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024**2:
            continue
        incoming = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
        try:
            opened = os.fstat(incoming)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("An Android icon changed during migration")
            with os.fdopen(os.dup(incoming), "rb") as stream:
                content = stream.read(4 * 1024**2 + 1)
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 4 * 1024**2:
                continue
            try:
                outgoing = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=target
                )
            except FileExistsError:
                continue
            with os.fdopen(outgoing, "wb") as stream:
                stream.write(content)
                os.fchown(stream.fileno(), 1000, 1000)
        finally:
            os.close(incoming)
    os.fchown(target, 1000, 1000)
    os.fchmod(target, 0o755)


def migrate(directory, info, owner, *, is_mounted):
    old = Path(info["home"]) / ".local/share/multipak/android-instances" / info["id"] / "data"
    if is_mounted(old) or is_mounted(directory):
        raise RuntimeError("Stop all Android mounts before migration")
    target = prepare_layout(directory, owner)
    if info.get("data_layout", 1) == 2:
        if not target.is_dir() or target.is_symlink():
            raise ValueError("Private Android data is unavailable")
        return info
    try:
        source = open_directory(old)
    except FileNotFoundError:
        if (directory / "waydroid_base.prop").exists() or target.exists():
            raise ValueError("Original Android data is missing; nothing was overwritten") from None
        target.mkdir(mode=0o771)
        for name in ("icons", "waydroid_tmp"):
            (target / name).mkdir(mode=0o755)
        return {**info, "data_layout": 2}
    try:
        checkpoint = target.parent / "migration.json"
        verified = _fingerprint(source).hex()
        saved = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {}
        if target.exists() and saved.get("source_sha256") != verified:
            destination = open_directory(target)
            try:
                if _fingerprint(source) != _fingerprint(destination):
                    raise ValueError(
                        "A previous migration needs inspection; original data was preserved"
                    )
            finally:
                os.close(destination)
        elif not target.exists():
            with tempfile.TemporaryDirectory(prefix=".migration-", dir=target.parent) as temp:
                destination = open_directory(temp)
                try:
                    print("Copying Android data to its protected location…", flush=True)
                    _copy_tree(source, destination)
                    print("Verifying migrated Android data…", flush=True)
                    if _fingerprint(source).hex() != verified or _fingerprint(
                        source
                    ) != _fingerprint(destination):
                        raise ValueError("Android data changed; original data was preserved")
                finally:
                    os.close(destination)
                Path(temp).rename(target)
        temporary = checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps({"source_sha256": verified}))
        temporary.replace(checkpoint)
        # Copy existing launcher icons to the small shared area without following links.
        try:
            icons = os.open("icons", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source)
        except FileNotFoundError:
            icons = None
        if icons is not None:
            destination = open_directory(directory / "exchange/icons")
            try:
                _export_icons(icons, destination)
            finally:
                os.close(icons)
                os.close(destination)
        for name in ("icons", "waydroid_tmp"):
            path = target / name
            if path.is_symlink() or not path.is_dir():
                path.unlink(missing_ok=True)
            path.mkdir(mode=0o755, exist_ok=True)
        # Source root ownership, modes and xattrs were copied as well.
        info = {**info, "data_layout": 2}
        print("Original Android data retained at: " + str(old), flush=True)
        return info
    finally:
        os.close(source)

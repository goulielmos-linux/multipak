"""File-descriptor boundaries used by the installed privileged helpers."""

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path

CLEAN_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def open_directory(path):
    """Pin every component; never traverse a symlink as root."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("An absolute directory is required")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_user_file(owner, path):
    """Apply the caller's filesystem permissions before opening untrusted input."""
    uid, gid, groups = os.geteuid(), os.getegid(), os.getgroups()
    descriptor = None
    try:
        if uid == 0:
            os.setgroups([])
            os.setegid(owner.pw_gid)
            os.seteuid(owner.pw_uid)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    finally:
        if uid == 0:
            os.seteuid(uid)
            os.setegid(gid)
            os.setgroups(groups)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner.pw_uid:
            raise ValueError("The input must be a regular file owned by the caller")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def cancellation_watch(owner, path):
    descriptor = open_user_file(owner, path)
    try:
        yield lambda: os.fstat(descriptor).st_nlink == 0
    finally:
        os.close(descriptor)


def copy_verified_file(owner, source, target, digest, limit=4 * 1024**3):
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid content digest")
    descriptor = open_user_file(owner, source)
    checksum = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as incoming, Path(target).open("xb") as outgoing:
            remaining = limit
            while data := incoming.read(1024 * 1024):
                remaining -= len(data)
                if remaining < 0:
                    raise ValueError("The input exceeds the permitted size")
                checksum.update(data)
                outgoing.write(data)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if checksum.hexdigest() != digest:
            raise ValueError("The input changed after verification")
    except BaseException:
        Path(target).unlink(missing_ok=True)
        raise

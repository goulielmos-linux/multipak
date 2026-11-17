"""Ask the launcher of a running snap application to close it."""

import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .store import remove_tree

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _directory(store, tx):
    return store.snap_directory(tx.id) / "running-applications"


@contextmanager
def close_channel(store, tx):
    directory = _directory(store, tx)
    directory.mkdir(exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise OSError("Invalid snap application control directory.")
    with TemporaryDirectory(prefix="app-", dir=directory) as channel:
        fifo = Path(channel) / "close"
        os.mkfifo(fifo, 0o600)
        descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)

        def requested():
            try:
                return b"close" in os.read(descriptor, 4096).splitlines()
            except BlockingIOError:
                return False

        try:
            yield requested
        finally:
            os.close(descriptor)


def _request_close(store, tx):
    try:
        directory = os.open(_directory(store, tx), _DIRECTORY)
    except FileNotFoundError:
        return
    try:
        for name in os.listdir(directory):
            if not re.fullmatch(r"app-[a-z0-9_]+", name):
                continue
            try:
                channel = os.open(name, _DIRECTORY, dir_fd=directory)
            except OSError:
                continue
            try:
                descriptor = os.open(
                    "close", os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=channel
                )
                try:
                    if stat.S_ISFIFO(os.fstat(descriptor).st_mode):
                        os.write(descriptor, b"close\n")
                finally:
                    os.close(descriptor)
            except OSError:
                continue
            finally:
                os.close(channel)
    finally:
        os.close(directory)


def close(store, tx, *, wait=10):
    """Ask only this snap's application launchers to close, then wait for their locks."""
    from .snap import ApplicationRunning, application_lock

    _request_close(store, tx)
    deadline = time.monotonic() + wait
    while True:
        try:
            with application_lock(store, tx, update=True):
                return True
        except ApplicationRunning:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


def clear_orphans(store, tx):
    """Remove close channels left by launchers that were killed. Only for a caller
    that holds the application lock exclusively: no launcher of the snap runs then."""
    directory = _directory(store, tx)
    if directory.is_symlink() or not directory.is_dir():
        return
    for path in directory.iterdir():
        if re.fullmatch(r"app-[a-z0-9_]+", path.name) and not path.is_symlink() and path.is_dir():
            remove_tree(path, ignore_errors=True)

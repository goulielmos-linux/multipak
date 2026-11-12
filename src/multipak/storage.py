"""Free-space checks and readable errors for package storage."""

import errno
import shutil

from .localization import tr
from .process import CommandError


class InsufficientSpace(RuntimeError):
    """A package operation could not fit on the destination filesystem."""


def require_copy_space(directory, size):
    try:
        available = shutil.disk_usage(directory).free
    except OSError:
        return
    if available < size:
        raise InsufficientSpace(tr("There is not enough free space to copy the package."))


def _is_disk_full(error):
    if isinstance(error, OSError):
        return error.errno in (errno.ENOSPC, errno.EDQUOT)
    if isinstance(error, CommandError):
        output = error.output.lower()
        return any(
            text in output
            for text in (
                "no space left on device",
                "disk quota exceeded",
                "you don't have enough free space",
                "more space needed on",
                "partition / too full",
            )
        )
    return False


def record_install_failure(store, tx, error):
    """Record failure when possible, preserving the cause if writing also fails."""
    reported = (
        InsufficientSpace(tr("The disk is full. Free up disk space, then try again."))
        if _is_disk_full(error)
        else error
    )
    tx.status, tx.error = "failed", str(reported)
    try:
        store.save(tx)
    except OSError as write_error:
        reported.add_note(str(write_error))
    return reported

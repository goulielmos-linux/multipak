"""snapctl user-open: links and files a snap asks to open, opened on the desktop.

Only what snapd would accept is passed to xdg-open: allowed kinds of link and files in
the home folder."""

import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from multipak.localization import tr

# snapd's allowedURLSchemes (usersession/userd/launcher.go).
SCHEMES = (
    "apt",
    "help",
    "http",
    "https",
    "mailto",
    "msteams",
    "slack",
    "snap",
    "zoommtg",
    "zoomphonecall",
    "zoomus",
)
MAX_LENGTH = 8192


def _home():
    return Path(os.environ.get("HOME") or Path.home()).resolve()


def target(text, home=None):
    """What to open for text, a link or a path; ValueError when it is not allowed."""
    if not text or len(text) > MAX_LENGTH or not text.isprintable():
        raise ValueError(tr("This cannot be opened: {0}").format(text[:200]))
    parts = urlsplit(text)
    if parts.scheme and parts.scheme != "file":
        if parts.scheme.lower() not in SCHEMES:
            raise ValueError(
                tr("Links of this kind are not opened for snaps: {0}").format(parts.scheme)
            )
        return text
    path = unquote(parts.path) if parts.scheme == "file" else text
    if not os.path.isabs(path):
        raise ValueError(tr("This cannot be opened: {0}").format(text[:200]))
    home = Path(home or _home())
    resolved = Path(path).resolve()
    # The home folder is the only place the snap and the desktop see alike.
    if not resolved.is_relative_to(home) or not resolved.exists():
        raise ValueError(tr("Only files in the home folder can be opened: {0}").format(path))
    return str(resolved)


def run(text):
    """(exit code, message) for snapctl user-open text."""
    try:
        what = target(text)
    except ValueError as error:
        return 1, f"user-open error: {error}\n"
    try:
        result = subprocess.run(
            ["xdg-open", what],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"user-open error: {error}\n"
    if result.returncode:
        return 1, f"user-open error: {result.stderr.strip() or result.returncode}\n"
    return 0, ""

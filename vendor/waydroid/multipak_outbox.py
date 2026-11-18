# SPDX-License-Identifier: GPL-3.0-or-later
"""Links and media state the helper app leaves in its private outbox, read by root.

Nothing from Android is trusted: folders are opened without following links, files must be
small, regular and the helper's, and only web and e-mail addresses and short media texts pass."""

import collections
import itertools
import json
import logging
import os
import re
import stat
import time
import unicodedata

from multipak_input import MAPPER as HELPER
from multipak_instance import current as instance

OUTBOX = ("data", HELPER, "files", "outbox")
LINK_SCHEMES = ("http", "https", "mailto")
WEB_SCHEMES = ("http", "https")
MAX_URI = 4096
MAX_FILE = 8 * 1024
MAX_LIST = 1024 * 1024
MAX_SCAN = 64  # Files read and deleted per call.
MAX_AGE = 120  # Seconds; older links (a session that crashed) are dropped unopened.
BURST, BURST_SECONDS = 16, 30  # Links opened at most per window.
DELAY_MS = 250  # Signals are sent at most this often.
MAX_TEXT = 256  # Characters in a media title or artist.
MEDIA_STATES = ("playing", "paused", "stopped")
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}(?:\.[A-Za-z0-9_]{1,100}){1,20}")
FIRST_APP_UID = 10000  # Android apps run as 10000-19999.
SYSTEM_UIDS = (0, 1000)  # Owners Android gives packages.list.
NAME = re.compile(r"[0-9]{1,20}-[0-9a-f]{1,64}\.json")
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC

_opened = collections.deque(maxlen=BURST)
# Newest checked media state and the time in its file name; {} means nothing is playing.
_media = {"stamp": -1, "state": {}}


def check_link(uri, schemes=LINK_SCHEMES):
    """Return the address if the desktop may open it, or raise ValueError."""
    if not isinstance(uri, str) or not 0 < len(uri) <= MAX_URI:
        raise ValueError("Invalid address length")
    if not uri.isascii() or any(not 32 < ord(c) < 127 for c in uri):
        raise ValueError("Addresses must not contain spaces or control characters")
    scheme, _, rest = uri.partition(":")
    scheme = scheme.lower()
    if scheme not in schemes:
        raise ValueError("Only web and e-mail addresses open on Linux")
    if scheme in WEB_SCHEMES and not (rest.startswith("//") and len(rest) > 2):
        raise ValueError("Web addresses need a host")
    if not rest:
        raise ValueError("Empty address")
    return uri


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("Repeated key")
    return dict(pairs)


def _constant(name):
    raise ValueError("Unexpected JSON constant " + name)


def _text(value):
    # Any language and emoji pass; control characters and broken UTF-16 halves do not.
    if not isinstance(value, str) or not 0 < len(value) <= MAX_TEXT:
        raise ValueError("Invalid media text")
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in value):
        raise ValueError("Media texts must not contain control characters")
    return value


def check_media(value):
    """Return the media state as plain strings, or raise ValueError; {} means none."""
    if not isinstance(value, dict) or not set(value) <= {"package", "state", "title", "artist"}:
        raise ValueError("Unexpected media format")
    if not value:
        return {}
    package, state = value.get("package"), value.get("state")
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        raise ValueError("Invalid media package")
    if state not in MEDIA_STATES:
        raise ValueError("Invalid media state")
    checked = {"package": package, "state": state}
    for key in ("title", "artist"):
        if key in value:
            checked[key] = _text(value[key])
    return checked


def parse(data):
    """Read one request: {"v":1,"uri":"..."} gives ("link", address) and
    {"v":1,"media":{...}} gives ("media", state)."""
    value = json.loads(data.decode("ascii"), object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        raise ValueError("Unexpected request format")
    if set(value) == {"v", "uri"}:
        return "link", check_link(value["uri"])
    if set(value) == {"v", "media"}:
        return "media", check_media(value["media"])
    raise ValueError("Unexpected request format")


def _read(directory, name, limit):
    """Read a regular file below an already opened folder, never following a link."""
    descriptor = os.open(name, FILE, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Not a small regular file")
        data = b""
        while len(data) <= limit:
            part = os.read(descriptor, limit + 1 - len(data))
            if not part:
                break
            data += part
        if len(data) > limit:
            raise ValueError("File too large")
        return data, info
    finally:
        os.close(descriptor)


def _descend(start, names):
    """Open each folder in turn from start; a folder replaced by a link fails."""
    descriptor = start
    try:
        for name in names:
            child = os.open(name, DIRECTORY, dir_fd=descriptor)
            if descriptor != start:
                os.close(descriptor)
            descriptor = child
    except BaseException:
        if descriptor != start:
            os.close(descriptor)
        raise
    return descriptor


def package_uid(data, packages):
    """The Android user of the first listed app found in packages.list, or None;
    never guessed from file owners."""
    system = _descend(data, ("system",))
    try:
        text, info = _read(system, "packages.list", MAX_LIST)
    finally:
        os.close(system)
    if info.st_uid not in SYSTEM_UIDS:
        raise ValueError("packages.list has an unexpected owner")
    found = {}
    for line in text.decode("utf-8", "replace").splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[0] in packages and re.fullmatch(r"[0-9]{1,9}", fields[1]):
            uid = int(fields[1])
            if FIRST_APP_UID <= uid < FIRST_APP_UID + 10000:
                found.setdefault(fields[0], uid)
    return next((found[name] for name in packages if name in found), None)


def helper_uid(data):
    uid = package_uid(data, (HELPER,))
    if uid is None:
        raise ValueError("The MultiPak helper is not installed")
    return uid


def _allowed(now):
    if len(_opened) == BURST and now - _opened[0] < BURST_SECONDS:
        return False
    _opened.append(now)
    return True


def _request(outbox, name, uid, now):
    data, info = _read(outbox, name, MAX_FILE)
    if info.st_uid != uid or info.st_nlink != 1:
        raise ValueError("Not written by the MultiPak helper")
    kind, value = parse(data)
    # An old link is left from a session that crashed; a media state holds until it changes.
    if kind == "link" and not -5 < now - info.st_mtime <= MAX_AGE:
        raise ValueError("Request too old")
    return kind, value


def _keep_media(name, state):
    # Batches are read in folder order, so a newer state may come before an older one.
    stamp = int(name.partition("-")[0])
    if stamp >= _media["stamp"]:
        _media["stamp"], _media["state"] = stamp, state


def _drain(outbox, uid, now):
    with os.scandir(outbox) as entries:
        names = sorted(entry.name for entry in itertools.islice(entries, MAX_SCAN))
    links, dropped = [], 0
    for name in names:
        uri = None
        if NAME.fullmatch(name):
            try:
                kind, value = _request(outbox, name, uid, now)
                if kind == "link":
                    uri = value
                else:
                    _keep_media(name, value)
            except Exception as error:  # Anything Android wrote may be malformed.
                logging.warning("Ignoring an Android outbox request: %s", error)
        try:
            os.unlink(name, dir_fd=outbox)
        except OSError as error:
            logging.warning("Could not remove an Android link request: %s", error)
        if uri and _allowed(now):
            links.append(uri)
        elif uri:
            dropped += 1
    if dropped:
        logging.warning("Too many links from Android; dropped %d", dropped)
    return links


def take(now=None):
    """Read, check and delete the waiting requests; return the addresses to open.

    A media state found on the way is kept for media()."""
    now = time.time() if now is None else now
    try:
        data = os.open(instance().data, DIRECTORY)
    except OSError:
        return []
    try:
        uid = helper_uid(data)
        outbox = _descend(data, OUTBOX)
    except FileNotFoundError:
        return []  # No link yet.
    except (OSError, ValueError) as error:
        # Helper missing or a folder replaced by a link.
        logging.warning("Android outbox refused: %s", error)
        return []
    finally:
        os.close(data)
    try:
        return _drain(outbox, uid, now)
    finally:
        os.close(outbox)


def media():
    """The newest checked media state, as a copy; {} when nothing is playing."""
    return dict(_media["state"])


def watch(notify):
    """Call notify (no data) when a request arrives; works before the folder exists.

    The monitor only wakes the owner's session; take() does the checking. A new
    session starts with no media state: the helper sends its state again."""
    _media["stamp"], _media["state"] = -1, {}
    return monitor(OUTBOX, notify)


def monitor(parts, notify):
    """Watch a folder below Android's data for files renamed or created in."""
    from gi.repository import Gio, GLib

    path = os.path.join(instance().data, *parts)
    watcher = Gio.File.new_for_path(path).monitor_directory(
        Gio.FileMonitorFlags.WATCH_MOVES, None
    )
    pending = []

    def fire():
        pending.clear()
        notify()
        return GLib.SOURCE_REMOVE

    def changed(_monitor, _file, _other, event):
        # The helper renames finished files in; CREATED also covers the folder's first file.
        arrived = (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.MOVED_IN)
        if event in arrived and not pending:
            pending.append(GLib.timeout_add(DELAY_MS, fire))

    watcher.connect("changed", changed)
    return watcher

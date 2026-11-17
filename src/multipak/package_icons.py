"""Read desktop icons from package archives without installing any files."""

import configparser
import io
import logging
import re
import shutil
import subprocess
import tarfile
from contextlib import ExitStack, contextmanager
from pathlib import PurePosixPath
from threading import Timer
from time import monotonic
from xml.etree import ElementTree

from .desktop import parse_desktop
from .metadata import _deb_members, _tar_stream
from .process import Cancelled

LOG = logging.getLogger(__name__)

MAX_SCAN = 512 * 1024 * 1024
MAX_MEMBERS = 30_000
MAX_ICON = 1024 * 1024
MAX_DESKTOP = 64 * 1024
MAX_LAUNCHERS = 64
SCAN_SECONDS = 8
ICON_SUFFIXES = (".svg", ".png")


class _BoundedReader:
    def __init__(self, stream, limit, check=None):
        self.stream, self.remaining, self.check = stream, limit, check

    def read(self, size):
        if self.check is not None:
            self.check()
        data = self.stream.read(min(size, self.remaining))
        self.remaining -= len(data)
        return data


@contextmanager
def _members(package, cancelled):
    deadline = monotonic() + SCAN_SECONDS

    def check():
        if cancelled is not None and cancelled.is_set():
            raise Cancelled
        if monotonic() >= deadline:
            raise TimeoutError("Package icon scan timed out")

    with ExitStack() as stack:
        stream = stack.enter_context(package.path.open("rb"))
        if package.kind == "rpm":
            command = shutil.which("rpm2archive")
            if not command:
                yield iter(())
                return
            process = stack.enter_context(
                subprocess.Popen(
                    [command, "-n", "-"],
                    stdin=stream,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            )

            def stop():
                if process.poll() is None:
                    process.kill()

            timer = Timer(SCAN_SECONDS, stop)
            timer.daemon = True
            timer.start()
            stack.callback(stop)
            stack.callback(timer.cancel)
            stream = process.stdout
        elif package.kind == "deb":
            for name, size in _deb_members(stream):
                if name in (
                    "data.tar",
                    "data.tar.gz",
                    "data.tar.bz2",
                    "data.tar.xz",
                    "data.tar.zst",
                ):
                    header = stream.read(min(size, 16))
                    stream.seek(-len(header), io.SEEK_CUR)
                    stream = _tar_stream(stack, _BoundedReader(stream, size), header)
                    break
            else:
                yield iter(())
                return
        else:
            header = stream.read(16)
            stream.seek(0)
            stream = _tar_stream(stack, stream, header)
        reader = _BoundedReader(stream, MAX_SCAN, check)
        archive = stack.enter_context(tarfile.open(fileobj=reader, mode="r|"))
        # A Puppy Linux PET keeps its files under one package folder.
        yield _regular_members(archive, check, strip=package.kind == "puppy")


def _regular_members(archive, check, strip=False):
    for count, member in enumerate(archive):
        check()
        if count >= MAX_MEMBERS:
            break
        path = PurePosixPath(member.name)
        if strip:
            path = PurePosixPath(*path.parts[1:]) if len(path.parts) > 1 else PurePosixPath()
        if member.isfile() and not path.is_absolute() and ".." not in path.parts:
            yield archive, member, path


def _launcher_icon(archive, member):
    try:
        with archive.extractfile(member) as stream:
            entry = parse_desktop(stream.read().decode("utf-8-sig"))["Desktop Entry"]
        if entry.get("Type") == "Application" and entry.get("Hidden", "").lower() != "true":
            return entry.get("Icon", ""), entry.get("NoDisplay", "").lower() == "true"
    except (configparser.Error, KeyError, ValueError):
        pass
    return "", False


def _icon_rank(path):
    geometry = re.fullmatch(r"(\d{1,4})x\d{1,4}", path.parts[-3]) if len(path.parts) > 2 else None
    distance = abs(int(geometry[1]) - 128) if geometry else 128
    return ("hicolor" not in path.parts, path.suffix != ".svg", distance, str(path))


def _candidates(package, cancelled):
    launchers, icons = [], set()
    with _members(package, cancelled) as members:
        for archive, member, path in members:
            if path.suffix in ICON_SUFFIXES and 0 < member.size <= MAX_ICON:
                icons.add(path)
            elif (
                path.parts[:3] == ("usr", "share", "applications")
                and path.suffix == ".desktop"
                and 0 < member.size <= MAX_DESKTOP
                and len(launchers) < MAX_LAUNCHERS
            ):
                icon, hidden = _launcher_icon(archive, member)
                if icon:
                    stem = path.stem.casefold()
                    rank = (
                        hidden,
                        stem != package.name.casefold(),
                        stem.rsplit(".", 1)[-1] != package.name.casefold(),
                        str(path),
                    )
                    launchers.append((rank, icon))

    selected = {}
    for _, icon in sorted(launchers):
        declared = PurePosixPath(icon)
        if declared.is_absolute():
            exact = declared.relative_to("/")
            if exact in icons:
                selected[exact] = None
        elif len(declared.parts) == 1:
            names = (icon,) if declared.suffix in ICON_SUFFIXES else (icon + ".svg", icon + ".png")
            matches = (
                path
                for path in icons
                if path.name in names
                and path.parts[:3] in (("usr", "share", "icons"), ("usr", "share", "pixmaps"))
            )
            selected.update(dict.fromkeys(sorted(matches, key=_icon_rank)))
    return {path: rank for rank, path in enumerate(selected)}


def _format(data, suffix):
    if suffix == ".png":
        if len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"):
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return 0 < width <= 4096 and 0 < height <= 4096
        return False

    if b"<!DOCTYPE" in data or b"\0" in data:
        return False
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return False
    return root.tag in ("svg", "{http://www.w3.org/2000/svg}svg") and all(
        value.startswith("#")
        for element in root.iter()
        for key, value in element.attrib.items()
        if key.rsplit("}", 1)[-1] == "href"
    )


def _read_icon(package, cancelled):
    candidates = _candidates(package, cancelled)
    icon, suffix, best = b"", "", len(candidates)
    if not candidates:
        return icon, suffix
    with _members(package, cancelled) as members:
        for archive, member, path in members:
            rank = candidates.get(path, best)
            if rank >= best or not 0 < member.size <= MAX_ICON:
                continue
            with archive.extractfile(member) as stream:
                data = stream.read()
            if _format(data, path.suffix):
                icon, suffix, best = data, path.suffix, rank
                if best == 0:
                    break
    return icon, suffix


def read_package_icon(package, cancelled=None):
    try:
        return _read_icon(package, cancelled)
    except Cancelled:
        raise
    except Exception:
        LOG.debug("Could not read the package icon", exc_info=True)
        return b"", ""

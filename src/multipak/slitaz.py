"""Inspect SliTaz binary packages without executing the shell receipt."""

import gzip
import io
import lzma
import re
import stat
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from multipak.localization import tr

CPIO_MAGICS = (b"070701", b"070702")
CPIO_HEADER_SIZE = 110
MAX_ARCHIVE_MEMBERS = 1024
MAX_MEMBER_NAME = 4096
MAX_RECEIPT = 256 * 1024
PAYLOAD_NAMES = (b"fs.cpio.lzma", b"fs.cpio.gz")
RECEIPT_FIELDS = {"PACKAGE", "VERSION", "EXTRAVERSION", "SHORT_DESC", "DEPENDS"}
# The receipt does not say the architecture. The first program in the payload does;
# the file name (name-version-x86_64.tazpkg) is the fallback for packages without one.
MAX_PAYLOAD_SCAN = 256 * 1024 * 1024
MAX_PAYLOAD_MEMBERS = 65536
ELF_ARCHITECTURES = {(1, 3): "i486", (2, 62): "x86_64"}

EMPTY_LINE = re.compile(r"[ \t]*(?:#[^\n]*)?(?:\n|\Z)")
ASSIGNMENT_NAME = re.compile(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)=")
ASSIGNMENT_VALUE = re.compile(
    r"""("(?:[^"\\]|\\[\s\S])*"|'[^']*'|[^"'\s\\;&|<>()`]*)"""
    r"(?:[ \t]+(?:#[^\n]*)?)?(?:\n|\Z)"
)
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]*")
EXTRAVERSION = re.compile(r"[A-Za-z0-9+._~-]*")


@dataclass(frozen=True)
class Info:
    name: str
    version: str
    architecture: str
    summary: str
    dependencies: str


def read_info(path: Path) -> Info:
    """Read the receipt, and the architecture from the payload or the file name."""
    try:
        with path.open("rb") as stream:
            receipt, payload = _cpio_receipt(stream)
            fields = _receipt_fields(receipt.decode("utf-8").replace("\r\n", "\n"))
            name = fields.get("PACKAGE", "")
            version = fields.get("VERSION", "")
            extra = fields.get("EXTRAVERSION", "")
            if (
                not PACKAGE_NAME.fullmatch(name)
                or not VERSION.fullmatch(version)
                or not EXTRAVERSION.fullmatch(extra)
            ):
                raise ValueError(tr("Invalid SliTaz package name or version."))
            version += extra
            architecture = _payload_architecture(stream, *payload)
    except (OSError, EOFError, UnicodeError, lzma.LZMAError, zlib.error) as error:
        raise ValueError(tr("The SliTaz package could not be read: {0}").format(error)) from error
    if architecture is None:
        # No program inside: the package is architecture-independent unless its
        # canonical name says otherwise.
        names = {path.name, path.stem}
        architecture = "x86_64" if f"{name}-{version}-x86_64" in names else "any"
    return Info(
        name,
        version,
        architecture,
        " ".join(fields.get("SHORT_DESC", "").split()),
        "\n".join(fields.get("DEPENDS", "").split()),
    )


def _payload_architecture(stream: BinaryIO, name: bytes, offset: int, size: int) -> str | None:
    """The architecture of the first ELF program in the compressed payload, if any."""
    source = io.BufferedReader(_Window(stream, offset, size))
    if name.endswith(b".lzma"):
        payload = lzma.LZMAFile(source, format=lzma.FORMAT_ALONE)
    else:
        payload = gzip.GzipFile(fileobj=source)
    with payload:
        try:
            return _first_elf(_Limited(payload, MAX_PAYLOAD_SCAN))
        except _LimitReached:
            return None


class _LimitReached(Exception):
    pass


def _first_elf(stream: "_Limited") -> str | None:
    for _ in range(MAX_PAYLOAD_MEMBERS):
        try:
            header = stream.read_exact(CPIO_HEADER_SIZE)
        except EOFError:
            return None
        if header[:6] not in CPIO_MAGICS or not re.fullmatch(rb"[0-9A-Fa-f]{104}", header[6:]):
            raise ValueError(tr("Invalid SliTaz CPIO header."))
        mode = int(header[14:22], 16)
        size = int(header[54:62], 16)
        name_size = int(header[94:102], 16)
        if not 1 < name_size <= MAX_MEMBER_NAME:
            raise ValueError(tr("Invalid SliTaz CPIO member name."))
        name = stream.read_exact(name_size)[:-1]
        stream.read_exact(-(CPIO_HEADER_SIZE + name_size) % 4)
        if name == b"TRAILER!!!":
            return None
        data = b""
        if stat.S_ISREG(mode) and size >= 20:
            data = stream.read_exact(20)
        stream.skip(size - len(data) + (-size % 4))
        if data.startswith(b"\x7fELF"):
            machine = int.from_bytes(data[18:20], "little" if data[5] == 1 else "big")
            architecture = ELF_ARCHITECTURES.get((data[4], machine))
            if architecture is None:
                raise ValueError(tr("Unsupported SliTaz package architecture."))
            return architecture
    return None


class _Window(io.RawIOBase):
    """A read-only view of part of a stream."""

    def __init__(self, stream: BinaryIO, offset: int, size: int):
        self.stream, self.position, self.end = stream, offset, offset + size

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self.stream.seek(self.position)
        data = self.stream.read(min(len(buffer), self.end - self.position))
        buffer[: len(data)] = data
        self.position += len(data)
        return len(data)


class _Limited:
    """Reads a decompressed stream, refusing to go past a size limit."""

    def __init__(self, stream, limit: int):
        self.stream, self.left = stream, limit

    def read_exact(self, size: int) -> bytes:
        if size > self.left:
            raise _LimitReached
        data = self.stream.read(size)
        if len(data) != size:
            raise EOFError(tr("The SliTaz CPIO archive is truncated."))
        self.left -= size
        return data

    def skip(self, size: int) -> None:
        while size:
            size -= len(self.read_exact(min(size, 1024 * 1024)))


def _cpio_receipt(stream: BinaryIO) -> tuple[bytes, tuple[bytes, int, int]]:
    """The receipt and the payload member's name, offset and size."""
    length = stream.seek(0, io.SEEK_END)
    stream.seek(0)
    seen: set[bytes] = set()
    receipt = b""
    payload = (b"", 0, 0)
    for _ in range(MAX_ARCHIVE_MEMBERS + 1):
        header = _read_exact(stream, CPIO_HEADER_SIZE)
        magic = header[:6]
        if magic not in CPIO_MAGICS or not re.fullmatch(rb"[0-9A-Fa-f]{104}", header[6:]):
            raise ValueError(tr("Invalid SliTaz CPIO header."))
        mode = int(header[14:22], 16)
        size = int(header[54:62], 16)
        name_size = int(header[94:102], 16)
        checksum = int(header[102:110], 16)
        if not 1 < name_size <= MAX_MEMBER_NAME:
            raise ValueError(tr("Invalid SliTaz CPIO member name."))
        raw_name = _read_exact(stream, name_size)
        name = raw_name[:-1].removeprefix(b"./") or b"."
        if (
            not raw_name.endswith(b"\0")
            or b"\0" in name
            or name.startswith(b"/")
            or b".." in name.split(b"/")
        ):
            raise ValueError(tr("Invalid SliTaz CPIO member name."))
        _read_exact(stream, -(CPIO_HEADER_SIZE + name_size) % 4)
        end = stream.tell() + size + (-size % 4)
        if end > length:
            raise EOFError(tr("The SliTaz CPIO archive is truncated."))
        if name == b"TRAILER!!!":
            if size:
                raise ValueError(tr("Invalid SliTaz CPIO trailer."))
            break
        if name in (b"receipt", b"files.list", *PAYLOAD_NAMES):
            if name in seen or not stat.S_ISREG(mode) or (not size and name != b"files.list"):
                raise ValueError(tr("Invalid or duplicate SliTaz package member."))
            seen.add(name)
        if name == b"receipt":
            if size > MAX_RECEIPT:
                raise ValueError(tr("The SliTaz receipt exceeds the read limit."))
            receipt = _read_exact(stream, size)
            if magic == b"070702" and sum(receipt) % (2**32) != checksum:
                raise ValueError(tr("Invalid SliTaz receipt checksum."))
        elif name in PAYLOAD_NAMES:
            payload = (name, stream.tell(), size)
        stream.seek(end)
    else:
        raise ValueError(tr("The SliTaz CPIO archive contains too many members."))
    if not receipt or b"files.list" not in seen or sum(name in seen for name in PAYLOAD_NAMES) != 1:
        raise ValueError(tr("The CPIO archive does not contain the required SliTaz package files."))
    return receipt, payload


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise EOFError(tr("The SliTaz CPIO archive is truncated."))
    return data


def _receipt_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    offset = 0
    # The packaged receipt starts with assignments. Stop before any shell command or hook.
    while offset < len(text):
        if empty := EMPTY_LINE.match(text, offset):
            offset = empty.end()
            continue
        name = ASSIGNMENT_NAME.match(text, offset)
        if name is None:
            break
        key = name[1]
        value = ASSIGNMENT_VALUE.match(text, name.end())
        if value is None:
            if key in RECEIPT_FIELDS:
                raise ValueError(tr("The SliTaz receipt field {0} is not a literal.").format(key))
            break
        offset = value.end()
        if key not in RECEIPT_FIELDS:
            continue
        if key in fields:
            raise ValueError(tr("Duplicate SliTaz receipt field: {0}.").format(key))
        fields[key] = _literal(key, value[1])
    return fields


def _literal(key: str, value: str) -> str:
    if value.startswith("'"):
        return value[1:-1]

    def unescape(match: re.Match[str]) -> str:
        fragment = match[0]
        if len(fragment) == 1:
            raise ValueError(tr("The SliTaz receipt field {0} is not a literal.").format(key))
        return fragment[1:] if fragment[1:] != "\n" else ""

    if value.startswith('"'):
        return re.sub(r"\\[$`\"\\\n]|[$`]", unescape, value[1:-1])
    if "$" in value:
        raise ValueError(tr("The SliTaz receipt field {0} is not a literal.").format(key))
    return value

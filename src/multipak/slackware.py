"""Read Slackware package metadata from the file name and install/slack-desc.

A Slackware package is an ordinary compressed tar archive. MultiPak recognizes one only
when its file name has the name-version-arch-build form and install/slack-desc
describes that same package. Package scripts are never executed here.
"""

import gzip
import lzma
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from multipak.localization import tr

MAX_SLACK_DESC = 1024 * 1024
MAX_METADATA_SCAN = 128 * 1024 * 1024
COMPRESSIONS = {
    b"\x1f\x8b": gzip.open,
    b"\xfd7zXZ\x00": lzma.open,
}
SUFFIXES = (".tar.gz", ".tar.xz", ".tgz", ".txz")
NAME = r"[A-Za-z0-9][A-Za-z0-9+._-]*"
VERSION = r"[A-Za-z0-9][A-Za-z0-9+._~]*"
ARCHITECTURE = r"[A-Za-z0-9_]+"
BUILD = r"[A-Za-z0-9][A-Za-z0-9+._]*"
# Optional list of required packages that some third-party repositories ship.
REQUIRED = "install/slack-required"


@dataclass(frozen=True)
class Info:
    name: str
    version: str
    arch: str
    build: str
    summary: str
    required: tuple[str, ...] = ()


class _MetadataReader:
    """Limit decompression while looking for install/slack-desc."""

    def __init__(self, source: BinaryIO):
        self.source = source
        self.remaining = MAX_METADATA_SCAN

    def read(self, size: int) -> bytes:
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError(tr("Slackware package metadata exceeds the read limit."))
        return data


def split_name(filename: str) -> tuple[str, str, str, str] | None:
    """name, version, arch and build from a package file name, or None."""
    suffix = next((suffix for suffix in SUFFIXES if filename.endswith(suffix)), "")
    fields = filename.removesuffix(suffix).rsplit("-", 3)
    if not suffix or len(fields) != 4:
        return None
    name, version, arch, build = fields
    if not all(
        re.fullmatch(pattern, value)
        for pattern, value in zip((NAME, VERSION, ARCHITECTURE, BUILD), fields, strict=True)
    ):
        return None
    return name, version, arch, build


def install_name(filename: str) -> str:
    """The name installpkg needs: name-version-arch-build with a short extension."""
    fields = split_name(filename)
    if fields is None:
        raise ValueError(
            tr("The file name does not identify a Slackware package (name-version-arch-build).")
        )
    extension = ".tgz" if filename.endswith((".tgz", ".tar.gz")) else ".txz"
    return "-".join(fields) + extension


def is_candidate(path: Path, header: bytes) -> bool:
    return path.name.endswith(SUFFIXES) and header.startswith(tuple(COMPRESSIONS))


def read_info(path: Path) -> Info:
    """Read metadata without extracting payloads or executing package scripts."""
    fields = split_name(path.name)
    if fields is None:
        if path.name.endswith(SUFFIXES) and len(path.name.rsplit("-", 3)) == 4:
            raise ValueError(
                tr("The Slackware package has an invalid name, version, architecture or build.")
            )
        raise ValueError(
            tr("The file name does not identify a Slackware package (name-version-arch-build).")
        )
    name, version, arch, build = fields
    try:
        with path.open("rb") as raw:
            header = raw.read(6)
            raw.seek(0)
            for magic, opener in COMPRESSIONS.items():
                if header.startswith(magic):
                    with opener(raw, "rb") as stream:
                        summary, required = _read_metadata(stream, name)
                    return Info(name, version, arch, build, summary, required)
            raise ValueError(tr("The file is not a compressed Slackware package."))
    except (tarfile.TarError, OSError, EOFError, lzma.LZMAError) as error:
        raise ValueError(
            tr("The Slackware package could not be read: {0}").format(error)
        ) from error


def _read_text(archive, member, message: str) -> str:
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
        raise ValueError(message)
    if member.size > MAX_SLACK_DESC:
        raise ValueError(tr("The package description exceeds the read limit."))
    source = archive.extractfile(member)
    if source is None:
        raise ValueError(message)
    return source.read(MAX_SLACK_DESC + 1).decode("utf-8", errors="replace")


def _required(text: str) -> tuple[str, ...]:
    """Package names from slack-required; alternatives and version limits are dropped."""
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for choice in line.split(","):
            candidate = re.split(r"[\s<>=|]", choice.strip(), maxsplit=1)[0]
            if re.fullmatch(NAME, candidate) and candidate not in names:
                names.append(candidate)
    return tuple(names)


def _read_metadata(stream: BinaryIO, name: str) -> tuple[str, tuple[str, ...]]:
    summary = None
    required: tuple[str, ...] = ()
    with tarfile.open(fileobj=_MetadataReader(stream), mode="r|") as archive:
        for member in archive:
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise ValueError(tr("The package contains an unsafe path."))
            relative = member.name.removeprefix("./")
            if summary is not None and not relative.startswith("install/"):
                # makepkg sorts the archive, so install/ entries are next to each other.
                break
            if relative == REQUIRED:
                required = _required(
                    _read_text(archive, member, tr("Invalid install/slack-required file."))
                )
                continue
            if relative != "install/slack-desc":
                continue
            text = _read_text(archive, member, tr("Invalid install/slack-desc file."))
            lines = [
                line.removeprefix(name + ":").strip()
                for line in text.splitlines()
                if line.startswith(name + ":")
            ]
            if not lines:
                raise ValueError(tr("install/slack-desc does not describe the named package."))
            summary = next((line for line in lines if line), "")
    if summary is None:
        raise ValueError(tr("The package does not contain install/slack-desc."))
    return summary, required

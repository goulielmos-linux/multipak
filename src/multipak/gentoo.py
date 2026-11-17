"""Read metadata from Gentoo GPKG binary packages (GLEP 78)."""

import bz2
import gzip
import io
import lzma
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

from multipak.localization import tr

MAX_METADATA_ARCHIVE = 16 * 1024 * 1024
MAX_METADATA_FILE = 1024 * 1024
MAX_CONTAINER_READ = MAX_METADATA_ARCHIVE + 1024 * 1024
REGULAR_FILES = (tarfile.REGTYPE, tarfile.AREGTYPE)
FIELD_NAMES = {
    "CATEGORY",
    "PF",
    "CHOST",
    "SLOT",
    "BUILD_ID",
    "repository",
    "DESCRIPTION",
    "USE",
    "RDEPEND",
    "PDEPEND",
    "IDEPEND",
}
VERSION = r"[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?"
PACKAGE_FILE = re.compile(rf"([A-Za-z0-9_][A-Za-z0-9_+-]*?)-({VERSION})")
IDENTIFIER = r"[A-Za-z0-9_][A-Za-z0-9_+.-]*"


@dataclass(frozen=True)
class Info:
    category: str
    name: str
    version: str
    chost: str
    slot: str
    build_id: str
    repository: str
    summary: str
    use: str
    dependencies: str

    @property
    def architecture(self) -> str:
        return normalize_architecture(self.chost.partition("-")[0])


def normalize_architecture(arch: str) -> str:
    if re.fullmatch(r"i[3-6]86", arch):
        return "x86"
    return {"amd64": "x86_64", "arm64": "aarch64", "powerpc64le": "ppc64le"}.get(arch, arch)


class _ContainerReader(io.BufferedReader):
    """Limit bytes read from the outer TAR while allowing seeks over the image."""

    def __init__(self, source):
        super().__init__(source)
        self.remaining = MAX_CONTAINER_READ

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self.remaining:
            raise ValueError(tr("Gentoo package metadata exceeds the read limit."))
        data = super().read(size)
        self.remaining -= len(data)
        return data


def is_candidate(path: Path) -> bool:
    """An uncompressed TAR named .gpkg.tar, or one with a gpkg-1 member.

    Only the member headers are read, and only the first few.
    """
    try:
        with path.open("rb") as stream:
            block = stream.read(512)
            if block[257:262] != b"ustar":
                return False
            if path.name.endswith(".gpkg.tar"):
                return True
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                for _, member in zip(range(16), archive, strict=False):
                    if member.name.partition("/")[2] == "gpkg-1":
                        return True
    except (tarfile.TarError, OSError, EOFError):
        return False
    return False


def read_info(path: Path) -> Info:
    """Inspect package metadata without unpacking the image or verifying signatures."""
    try:
        with _ContainerReader(path.open("rb", buffering=0)) as stream:
            size = stream.seek(0, io.SEEK_END)
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                member = _metadata_member(archive, size)
                raw = _read_member(archive, member, MAX_METADATA_ARCHIVE)
        fields = _read_fields(_decompress(Path(member.name).suffix, raw))
    except (tarfile.TarError, OSError, EOFError, lzma.LZMAError, RecursionError) as error:
        raise ValueError(tr("The Gentoo package could not be read: {0}").format(error)) from error

    for key in ("CATEGORY", "PF", "CHOST", "SLOT"):
        if not 0 < len(fields.get(key, "")) <= 1024:
            raise ValueError(tr("Missing or invalid Gentoo metadata: {0}.").format(key))
    category, pf, chost, slot = (fields[key] for key in ("CATEGORY", "PF", "CHOST", "SLOT"))
    match = PACKAGE_FILE.fullmatch(pf)
    if (
        match is None
        or re.search(rf"-{VERSION}$", match[1])
        or not re.fullmatch(IDENTIFIER, category)
        or not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_.+]+)+", chost)
        or not re.fullmatch(rf"{IDENTIFIER}(?:/{IDENTIFIER})?", slot)
    ):
        raise ValueError(tr("Invalid Gentoo package identity."))
    build_id = fields.get("BUILD_ID", "")
    if build_id and not re.fullmatch(r"[0-9]+", build_id):
        raise ValueError(tr("Missing or invalid Gentoo metadata: {0}.").format("BUILD_ID"))
    dependencies = "\n".join(
        fields[key] for key in ("RDEPEND", "PDEPEND", "IDEPEND") if fields.get(key)
    )
    return Info(
        category,
        match[1],
        match[2],
        chost,
        slot,
        build_id,
        fields.get("repository", ""),
        fields.get("DESCRIPTION", ""),
        fields.get("USE", ""),
        dependencies,
    )


def _metadata_member(archive: tarfile.TarFile, size: int) -> tarfile.TarInfo:
    members: dict[str, tarfile.TarInfo] = {}
    root = ""
    for member in archive:
        prefix, _, name = member.name.partition("/")
        if (
            prefix in ("", ".", "..")
            or name in ("", ".", "..")
            or "/" in name
            or (root and prefix != root)
            or name in members
            or member.type not in REGULAR_FILES
            or member.size < 0
            or member.offset_data + member.size > size
        ):
            raise ValueError(tr("Invalid member in Gentoo GPKG archive."))
        root = prefix
        members[name] = member
    metadata = [item for name, item in members.items() if _is_inner_archive(name, "metadata")]
    images = [item for name, item in members.items() if _is_inner_archive(name, "image")]
    if (
        "gpkg-1" not in members
        or "Manifest" not in members
        or members["Manifest"].size == 0
        or len(metadata) != 1
        or len(images) != 1
        or images[0].size == 0
    ):
        raise ValueError(tr("The file does not contain the required Gentoo GPKG members."))
    return metadata[0]


def _is_inner_archive(name: str, stem: str) -> bool:
    return name == f"{stem}.tar" or (name.startswith(f"{stem}.tar.") and not name.endswith(".sig"))


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if member.size > limit:
        raise ValueError(tr("Gentoo package metadata exceeds the read limit."))
    source = archive.extractfile(member)
    if source is None:
        raise ValueError(tr("Invalid Gentoo metadata file."))
    with source:
        return source.read()


def _decompress(suffix: str, raw: bytes) -> bytes:
    if suffix == ".tar":
        return raw
    source = io.BytesIO(raw)
    if suffix == ".zst":
        if sys.version_info >= (3, 14):
            from compression.zstd import ZstdError, ZstdFile

            reader = ZstdFile(source)
        else:
            from zstandard import ZstdDecompressor, ZstdError

            reader = ZstdDecompressor().stream_reader(source)
        try:
            with reader:
                unpacked = reader.read(MAX_METADATA_ARCHIVE + 1)
        except ZstdError as error:
            raise ValueError(
                tr("The Gentoo package could not be read: {0}").format(error)
            ) from error
    else:
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}.get(suffix)
        if opener is None:
            raise ValueError(tr("Unsupported Gentoo metadata compression: {0}.").format(suffix))
        with opener(source, "rb") as reader:
            unpacked = reader.read(MAX_METADATA_ARCHIVE + 1)
    if len(unpacked) > MAX_METADATA_ARCHIVE:
        raise ValueError(tr("Gentoo package metadata exceeds the read limit."))
    return unpacked


def _read_fields(raw: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            prefix, _, key = member.name.partition("/")
            if (
                prefix != "metadata"
                or key in ("", ".", "..")
                or "/" in key
                or member.type not in REGULAR_FILES
                or key in seen
                or member.size < 0
                or member.offset_data + member.size > len(raw)
            ):
                raise ValueError(tr("Invalid or duplicate Gentoo metadata file."))
            seen.add(key)
            if key in FIELD_NAMES:
                data = _read_member(archive, member, MAX_METADATA_FILE)
                fields[key] = " ".join(data.decode("utf-8", errors="replace").split())
    return fields


def dependency_lines(dependencies: str, alternative: str = " or ") -> list[str]:
    """One requirement per line: "|| ( a b )" joins choices; USE conditions are dropped."""
    tokens = dependencies.split()
    position = 0

    def group() -> list[str]:
        nonlocal position
        lines: list[str] = []
        while position < len(tokens):
            token = tokens[position]
            position += 1
            if token == ")":
                break
            if token == "||" and position < len(tokens) and tokens[position] == "(":
                position += 1
                choices = group()
                if choices:
                    lines.append(alternative.join(choices))
            elif token == "(" or (token.endswith("?") and len(token) > 1):
                if token != "(" and position < len(tokens) and tokens[position] == "(":
                    position += 1
                lines.extend(group())
            elif not token.startswith("!"):
                # "!atom" and "!!atom" are blockers, not requirements.
                lines.append(token)
        return lines

    result: list[str] = []
    for line in group():
        if line not in result:
            result.append(line)
    return result

"""Read Puppy Linux PET packages and convert them for Slackware or Debian/Ubuntu.

Puppy Linux is built from another distribution's packages: BookwormPup from Debian 12,
S15Pup from Slackware 15.0, JammyPup from Ubuntu 22.04. pet.specs names that
distribution, so MultiPak installs a PET in an environment of it. The PET becomes a
package of that distribution (a .txz or a .deb), which its package manager then
records and removes. Reading never extracts files or executes package scripts.
"""

import gzip
import hashlib
import io
import lzma
import re
import tarfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from multipak.localization import tr

GZIP_MAGIC = b"\x1f\x8b"
XZ_MAGIC = b"\xfd7zXZ\x00"
CHECKSUM_SIZE = 32
MAX_COMPRESSED_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_SCAN = 128 * 1024 * 1024
MAX_MEMBERS = 50_000
MAX_SPECS = 64 * 1024
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]*")
# Puppy versions often end with the architecture and build: 3.0.4-x86_64_s84.
ARCH_SUFFIX = re.compile(r"[-_](?:noarch|x86_64|amd64|i[3-6]86)(?:[-_].*)?$")
ELF_MACHINES = {3: "i686", 40: "armv7l", 62: "x86_64", 183: "aarch64"}


@dataclass(frozen=True)
class Info:
    name: str
    version: str
    release: str
    architecture: str
    summary: str
    dependencies: str
    compatible_distro: str = ""
    compatible_release: str = ""


class _PayloadReader:
    """Expose the compressed TAR bytes without the PET checksum trailer."""

    def __init__(self, source: BinaryIO, size: int):
        self.source = source
        self.remaining = size

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(self.remaining if size < 0 else min(size, self.remaining))
        self.remaining -= len(data)
        return data


class _ArchiveReader:
    """Limit decompression while reading package metadata and ELF headers."""

    def __init__(self, source: BinaryIO):
        self.source = source
        self.remaining = MAX_ARCHIVE_SCAN

    def read(self, size: int) -> bytes:
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError(tr("Puppy Linux package metadata exceeds the read limit."))
        return data


def is_candidate(path: Path, header: bytes) -> bool:
    """A gzip or XZ file named .pet, or one that ends with a PET checksum trailer."""
    if not header.startswith((GZIP_MAGIC, XZ_MAGIC)):
        return False
    if path.name.lower().endswith(".pet"):
        return True
    with path.open("rb") as stream:
        return bool(_checksum(stream))


def read_info(path: Path) -> Info:
    """Inspect a PET archive with a valid checksum and a Puppy package directory."""
    with _payload(path) as reader:
        return _read_archive(reader)


@contextmanager
def _payload(path: Path):
    """The decompressed TAR stream of a PET whose checksum trailer matches."""
    try:
        with path.open("rb") as stream:
            header = stream.read(len(XZ_MAGIC))
            if not header.startswith((GZIP_MAGIC, XZ_MAGIC)):
                raise ValueError(tr("The Puppy Linux package must use gzip or XZ compression."))
            checksum = _checksum(stream)
            if not checksum:
                raise ValueError(
                    tr("The file is missing a valid Puppy Linux PET checksum trailer.")
                )
            size = stream.tell() - CHECKSUM_SIZE
            if size > MAX_COMPRESSED_SIZE:
                raise ValueError(tr("The Puppy Linux package exceeds the read limit."))
            stream.seek(0)
            payload = _PayloadReader(stream, size)
            digest = hashlib.md5(usedforsecurity=False)
            for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                digest.update(chunk)
            if payload.remaining or digest.hexdigest().encode() != checksum.lower():
                raise ValueError(tr("The Puppy Linux PET checksum does not match."))
            stream.seek(0)
            payload = _PayloadReader(stream, size)
            reader = (
                gzip.GzipFile(fileobj=payload)
                if header.startswith(GZIP_MAGIC)
                else lzma.LZMAFile(payload)
            )
            with reader:
                yield reader
    except (
        tarfile.TarError,
        OSError,
        EOFError,
        lzma.LZMAError,
        zlib.error,
        RecursionError,
    ) as error:
        raise ValueError(
            tr("The Puppy Linux package could not be read: {0}").format(error)
        ) from error


def _checksum(stream: BinaryIO) -> bytes:
    if stream.seek(0, io.SEEK_END) <= CHECKSUM_SIZE:
        return b""
    stream.seek(-CHECKSUM_SIZE, io.SEEK_END)
    checksum = stream.read(CHECKSUM_SIZE)
    return checksum if re.fullmatch(rb"[0-9a-fA-F]{32}", checksum) else b""


def _read_archive(source: BinaryIO) -> Info:
    root = ""
    specs = None
    has_files = False
    architectures: set[str] = set()
    with tarfile.open(fileobj=_ArchiveReader(source), mode="r|") as archive:
        for index, member in enumerate(archive):
            if index >= MAX_MEMBERS:
                raise ValueError(tr("The Puppy Linux package lists too many files."))
            parts = _member_parts(member, root)
            if not parts:
                continue
            root = parts[0]
            if len(parts) == 1 or member.isdir():
                continue
            if len(parts) == 2 and (parts[1] == "pet.specs" or parts[1].endswith(".pet.specs")):
                if specs is not None or not member.isfile() or not 0 <= member.size <= MAX_SPECS:
                    raise ValueError(tr("Invalid or duplicate Puppy Linux pet.specs metadata."))
                field_file = archive.extractfile(member)
                if field_file is None:
                    raise ValueError(tr("Invalid Puppy Linux pet.specs metadata."))
                specs = field_file.read()
                continue
            has_files = True
            if member.isfile() and member.size >= 20:
                file = archive.extractfile(member)
                if file is not None:
                    header = file.read(20)
                    if (
                        header.startswith(b"\x7fELF")
                        and header[4] in (1, 2)
                        and header[5] in (1, 2)
                    ):
                        endian = "little" if header[5] == 1 else "big"
                        machine = int.from_bytes(header[18:20], endian)
                        architectures.add(ELF_MACHINES.get(machine, ""))
    if not root or (not has_files and not specs):
        raise ValueError(tr("The Puppy Linux package contains no files or metadata."))
    arch = next(iter(architectures)) if len(architectures) == 1 else ""
    if not architectures and root.endswith(("-noarch", "_noarch")):
        arch = "noarch"
    return _package_info(root, specs, arch)


def _member_parts(member: tarfile.TarInfo, root: str) -> list[str]:
    """The member's path inside the package directory, or [] for the archive root."""
    name = member.name.removeprefix("./").removesuffix("/") if member.isdir() else member.name
    name = name.removeprefix("./")
    if name in ("", ".") and member.isdir():
        return []
    parts = name.split("/")
    if (
        any(part in ("", ".", "..") for part in parts)
        or not PACKAGE_NAME.fullmatch(parts[0])
        or (root and parts[0] != root)
        or (len(parts) == 1 and not member.isdir())
    ):
        raise ValueError(tr("Invalid Puppy Linux package directory."))
    return parts


def _package_info(root: str, specs: bytes | None, architecture: str) -> Info:
    if not specs or not specs.strip():
        # Older PETs may only have a package directory to identify them.
        match = re.fullmatch(r"(.+?)-([0-9][A-Za-z0-9+._~-]*)", root)
        name, version = match.groups() if match else (root, "")
        version = ARCH_SUFFIX.sub("", version)
        return Info(name, version, "", architecture, "", "")

    fields = specs.decode("utf-8", errors="replace").splitlines()[0].split("|")
    if len(fields) < 10:
        raise ValueError(tr("Invalid Puppy Linux pet.specs metadata."))
    name, version, release = (fields[index].strip() for index in (1, 2, 3))
    version = ARCH_SUFFIX.sub("", version) or version
    if (
        not PACKAGE_NAME.fullmatch(name)
        or (version and not VERSION.fullmatch(version))
        or (release and not VERSION.fullmatch(release))
    ):
        raise ValueError(tr("Invalid Puppy Linux package name or version."))
    compatibility = [*fields[10:12], "", ""]
    return Info(
        name,
        version,
        release,
        architecture,
        fields[9].strip(),
        "\n".join(item.strip() for item in fields[8].split(",") if item.strip()),
        compatibility[0].strip(),
        compatibility[1].strip(),
    )


# Environment kinds that can receive a PET, as converted packages.
ENVIRONMENT_KINDS = ("slackware", "deb")
# Release codenames that pet.specs may use instead of numbers.
CODENAMES = {
    "debian": {
        "buster": "10",
        "bullseye": "11",
        "bookworm": "12",
        "trixie": "13",
        "forky": "14",
    },
    "ubuntu": {
        "bionic": "18.04",
        "focal": "20.04",
        "jammy": "22.04",
        "noble": "24.04",
    },
}
# Where the PET's own scripts are kept in the converted package.
SCRIPTS_DIRECTORY = "usr/share/multipak/puppy"
PET_SCRIPTS = ("pinstall.sh", "puninstall.sh")
# Puppy's scripts are written for Puppy; one that hangs must not block the installation.
SCRIPT_TIMEOUT = 300


def compatible_environment(distro: str, release: str) -> str:
    """The MultiPak environment id for pet.specs' distribution fields, or ""."""
    # Puppy's 64-bit builds may say slackware64.
    distro = re.sub(r"(?:64|32)$", "", distro.strip().lower())
    release = release.strip().lower()
    if distro == "devuan":
        return ""
    if distro == "slackware":
        match = re.fullmatch(r"(\d+\.\d+)", release)
        return f"slackware-{match[1]}" if match else ""
    if distro in CODENAMES:
        number = CODENAMES[distro].get(release, release)
        pattern = r"\d+" if distro == "debian" else r"\d{2}\.\d{2}"
        return f"{distro}-{number}" if re.fullmatch(pattern, number) else ""
    return ""


def dependency_names(text: str) -> list[str]:
    """Package names from pet.specs' dependency field: +name or +name&ge1.0."""
    names = []
    for line in text.replace(",", "\n").splitlines():
        name = re.split(r"[&|<>=:\s]", line.strip().lstrip("+"), maxsplit=1)[0]
        if PACKAGE_NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names


def deb_name(name: str) -> str:
    """A valid Debian package name: lower case, digits, + - and dots."""
    value = re.sub(r"[^a-z0-9+.-]+", "-", name.lower()).strip("-.+")
    return value if len(value) >= 2 else f"pet-{value or 'package'}"


def deb_version(version: str) -> str:
    """A valid Debian version; PET versions may start with letters."""
    value = re.sub(r"[^A-Za-z0-9.+~-]+", ".", version).strip("-.")
    upstream, separator, revision = value.rpartition("-")
    if not separator:
        upstream, revision = value, ""
    upstream = upstream.replace("-", ".")
    if not upstream[:1].isdigit():
        upstream = f"0~{upstream}" if upstream else "0"
    return f"{upstream}-{revision}" if revision else upstream


def slackware_file(name: str, version: str, architecture: str) -> str:
    """The name-version-arch-build.txz name installpkg takes the package name from."""
    value = re.sub(r"[^A-Za-z0-9+._~]+", "_", version).strip("_") or "0"
    if not value[0].isalnum():
        value = f"0{value}"
    arch = "x86_64" if architecture == "x86_64" else "noarch"
    return f"{name}-{value}-{arch}-1_pet.txz"


def deb_architecture(architecture: str) -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(architecture, "all")


def _script_runner(name: str, script: str) -> str:
    path = f"/{SCRIPTS_DIRECTORY}/{name}/{script}"
    return (
        f"if [ -f {path} ]; then\n"
        '  if command -v timeout >/dev/null 2>&1; then limit="timeout '
        f'{SCRIPT_TIMEOUT}"; else limit=; fi\n'
        f"  ( cd / && $limit sh {path} </dev/null ) || "
        f'echo "MultiPak: the Puppy Linux {script} script failed; continuing." >&2\n'
        "fi\n"
    )


def _normalized(member: tarfile.TarInfo, name: str, link: str = "") -> tarfile.TarInfo:
    """A copy owned by root, without setuid/setgid bits; directories are 755."""
    copy = tarfile.TarInfo(name)
    copy.type = member.type
    copy.size = member.size if member.isfile() else 0
    copy.mtime = member.mtime
    copy.mode = 0o755 if member.isdir() else member.mode & 0o777
    copy.uid = copy.gid = 0
    copy.uname = copy.gname = "root"
    copy.linkname = link or member.linkname
    return copy


def _directory(name: str) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    member.uname = member.gname = "root"
    return member


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = mode
    member.uname = member.gname = "root"
    archive.addfile(member, io.BytesIO(data))


def _copy_payload(
    source: Path, archive: tarfile.TarFile, prefix: str, name: str, reserved: str = ""
) -> tuple[set[str], int]:
    """Copy the PET's files into archive, relative to /, with prefix before each path.

    pet.specs is left out; pinstall.sh and puninstall.sh go to SCRIPTS_DIRECTORY.
    Returns the scripts found and the installed size in bytes.
    """
    scripts: set[str] = set()
    size = 0
    root = ""
    directories: set[str] = set()

    def add_parents(path: str) -> None:
        parts = path.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            parent = "/".join(parts[:index])
            if parent not in directories:
                directories.add(parent)
                archive.addfile(_directory(prefix + parent))

    with _payload(source) as reader, tarfile.open(fileobj=reader, mode="r|") as pet:
        for index, member in enumerate(pet):
            if index >= MAX_MEMBERS:
                raise ValueError(tr("The Puppy Linux package lists too many files."))
            parts = _member_parts(member, root)
            if not parts:
                continue
            root = parts[0]
            if len(parts) == 1:
                continue
            relative = "/".join(parts[1:])
            if len(parts) == 2 and (relative == "pet.specs" or relative.endswith(".pet.specs")):
                continue
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(
                    tr("The Puppy Linux package contains an unsupported special file.")
                )
            if reserved and (relative == reserved or relative.startswith(reserved + "/")):
                raise ValueError(
                    tr("The Puppy Linux package contains a reserved folder: {0}.").format(reserved)
                )
            if len(parts) == 2 and relative in PET_SCRIPTS and member.isfile():
                scripts.add(relative)
                relative = f"{SCRIPTS_DIRECTORY}/{name}/{relative}"
            link = ""
            if member.islnk():
                target = _member_parts(tarfile.TarInfo(member.linkname), root)
                if len(target) < 2:
                    raise ValueError(tr("Invalid Puppy Linux package directory."))
                link = prefix + "/".join(target[1:])
            if member.isdir():
                if relative in directories:
                    continue
                directories.add(relative)
            add_parents(relative)
            copy = _normalized(member, prefix + relative, link)
            if member.isfile():
                size += member.size
                archive.addfile(copy, pet.extractfile(member))
            else:
                archive.addfile(copy)
    return scripts, size


def to_slackware(source: Path, target: Path, info: Info) -> None:
    """Write a Slackware package (.txz) with the PET's files.

    installpkg runs install/doinst.sh, which runs the PET's pinstall.sh.
    """
    name = target.name.rsplit("-", 3)[0]
    with tarfile.open(target, mode="x:xz", format=tarfile.GNU_FORMAT) as archive:
        archive.addfile(_directory("./"))
        archive.addfile(_directory("install"))
        scripts, _size = _copy_payload(source, archive, "", name, reserved="install")
        summary = info.summary or "Puppy Linux package"
        description = [
            f"{name}: {name} ({summary})"[:79],
            f"{name}:",
            f"{name}: Converted by MultiPak from a Puppy Linux PET package.",
        ]
        _add_bytes(archive, "install/slack-desc", ("\n".join(description) + "\n").encode())
        if "pinstall.sh" in scripts:
            _add_bytes(archive, "install/doinst.sh", _script_runner(name, "pinstall.sh").encode())


def _ar_member(name: str, size: int) -> bytes:
    return f"{name:<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{size:<10}".encode("ascii") + b"`\n"


def to_deb(source: Path, target: Path, info: Info, name: str, version: str) -> None:
    """Write a Debian package with the PET's files and no dependencies yet.

    The postinst runs the PET's pinstall.sh and the prerm its puninstall.sh. The
    dependencies are added inside the environment, where its libraries can be checked.
    """
    data_path = target.with_name(target.name + ".data.tar.xz")
    try:
        with tarfile.open(data_path, mode="x:xz", format=tarfile.GNU_FORMAT) as archive:
            archive.addfile(_directory("./"))
            scripts, size = _copy_payload(source, archive, "./", name)
        summary = re.sub(r"\s+", " ", info.summary).strip() or "Puppy Linux package"
        control = (
            f"Package: {name}\n"
            f"Version: {version}\n"
            f"Architecture: {deb_architecture(info.architecture)}\n"
            "Maintainer: MultiPak <noreply@localhost>\n"
            f"Installed-Size: {max(1, size // 1024)}\n"
            "Section: misc\n"
            "Priority: optional\n"
            f"Description: {summary}\n"
            " Converted by MultiPak from a Puppy Linux PET package.\n"
        )
        control_stream = io.BytesIO()
        with tarfile.open(fileobj=control_stream, mode="w:xz", format=tarfile.GNU_FORMAT) as tar:
            tar.addfile(_directory("./"))
            _add_bytes(tar, "./control", control.encode())
            if "pinstall.sh" in scripts:
                postinst = (
                    '#!/bin/sh\n[ "$1" = configure ] || exit 0\n'
                    + _script_runner(name, "pinstall.sh")
                    + "exit 0\n"
                )
                _add_bytes(tar, "./postinst", postinst.encode(), 0o755)
            if "puninstall.sh" in scripts:
                prerm = (
                    '#!/bin/sh\n[ "$1" = remove ] || exit 0\n'
                    + _script_runner(name, "puninstall.sh")
                    + "exit 0\n"
                )
                _add_bytes(tar, "./prerm", prerm.encode(), 0o755)
        control_data = control_stream.getvalue()
        with target.open("xb") as output:
            output.write(b"!<arch>\n")
            for member, data in ((b"debian-binary", b"2.0\n"), (b"control.tar.xz", control_data)):
                output.write(_ar_member(member.decode(), len(data)) + data)
                if len(data) % 2:
                    output.write(b"\n")
            data_size = data_path.stat().st_size
            output.write(_ar_member("data.tar.xz", data_size))
            with data_path.open("rb") as data_file:
                while chunk := data_file.read(1024 * 1024):
                    output.write(chunk)
            if data_size % 2:
                output.write(b"\n")
    finally:
        data_path.unlink(missing_ok=True)

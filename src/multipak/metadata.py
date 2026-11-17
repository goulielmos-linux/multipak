"""Inspect package metadata without extracting payloads or executing package scripts."""

import bz2
import gzip
import io
import lzma
import platform
import re
import sys
import tarfile
from contextlib import ExitStack
from pathlib import Path

from multipak.localization import tr

from . import alpine, gentoo, host, pisi, puppy, slackware, slitaz, xbps
from .models import (
    GENTOO_ENVIRONMENT,
    SLITAZ_ENVIRONMENT,
    SLITAZ_I486_ENVIRONMENT,
    SOLUS_ENVIRONMENT,
    Package,
    PackageKind,
    void_environment,
)
from .process import Runner

MAX_CONTROL_ARCHIVE = 16 * 1024 * 1024
MAX_CONTROL_FILE = 1024 * 1024
MAX_ARCH_METADATA_SCAN = 32 * 1024 * 1024


class _MetadataReader:
    """Cap decompression while looking for metadata near the start of a package archive."""

    def __init__(self, source, limit_message):
        self.source = source
        self.limit_message = limit_message
        self.remaining = MAX_ARCH_METADATA_SCAN

    def read(self, size):
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError(self.limit_message)
        return data


def _tar_stream(stack, stream, header):
    if header.startswith(b"\x28\xb5\x2f\xfd"):
        if sys.version_info >= (3, 14):
            from compression.zstd import ZstdFile

            stream = stack.enter_context(ZstdFile(stream))
        else:
            import zstandard

            stream = stack.enter_context(zstandard.ZstdDecompressor().stream_reader(stream))
    elif header.startswith(b"\x1f\x8b"):
        stream = stack.enter_context(gzip.GzipFile(fileobj=stream))
    elif header.startswith(b"\xfd7zXZ\x00"):
        stream = stack.enter_context(lzma.LZMAFile(stream))
    elif header.startswith(b"BZh"):
        stream = stack.enter_context(bz2.BZ2File(stream))
    return stream


def _arch_fields(path: Path) -> dict[str, list[str]]:
    with ExitStack() as stack:
        stream = stack.enter_context(path.open("rb"))
        header = stream.read(16)
        stream.seek(0)
        stream = _tar_stream(stack, stream, header)
        limited = _MetadataReader(stream, tr("Arch package metadata exceeds the read limit."))
        with tarfile.open(fileobj=limited, mode="r|") as archive:
            for member in archive:
                if member.name.removeprefix("./") != ".PKGINFO":
                    continue
                if not member.isfile() or member.size > MAX_CONTROL_FILE:
                    raise ValueError(tr("Invalid .PKGINFO file."))
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(tr("Invalid .PKGINFO file."))
                fields: dict[str, list[str]] = {}
                for line in source.read(MAX_CONTROL_FILE + 1).decode("utf-8").splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    key, separator, value = line.partition(" = ")
                    if not separator or not key.strip():
                        raise ValueError(tr("Invalid .PKGINFO metadata."))
                    fields.setdefault(key.strip(), []).append(value.strip())
                for key in ("pkgname", "pkgver", "arch"):
                    if len(fields.get(key, ())) != 1 or not fields[key][0]:
                        raise ValueError(tr("Missing or duplicate Arch metadata: {0}.").format(key))
                if "pkgtype=src" in fields.get("xdata", ()):
                    raise ValueError(tr("Select a prebuilt Arch package, not a source package."))
                return fields
    raise ValueError(tr("The file does not contain Arch package .PKGINFO metadata."))


def _xbps_info(path: Path, header: bytes, *, required: bool) -> xbps.Info | None:
    if header.startswith(b"\x04\x22\x4d\x18"):
        if required:
            raise ValueError(tr("MultiPak does not support XBPS packages compressed with LZ4."))
        return None
    if sys.version_info >= (3, 14):
        from compression.zstd import ZstdError
    else:
        from zstandard import ZstdError

    try:
        with ExitStack() as stack:
            stream = stack.enter_context(path.open("rb"))
            stream = _tar_stream(stack, stream, header)
            limited = _MetadataReader(stream, tr("XBPS package metadata exceeds the read limit."))
            with tarfile.open(fileobj=limited, mode="r|") as archive:
                info = xbps.read_info(archive)
    except (tarfile.TarError, OSError, EOFError, ValueError, lzma.LZMAError, ZstdError) as exc:
        if required:
            raise ValueError(tr("The XBPS package could not be read: {0}").format(exc)) from exc
        return None
    if required and info is None:
        raise ValueError(tr("The file does not contain XBPS props.plist and files.plist metadata."))
    return info


def read_xbps_info(path: Path) -> xbps.Info:
    with path.open("rb") as stream:
        header = stream.read(16)
    info = _xbps_info(path, header, required=True)
    assert info is not None
    return info


def _package_metadata(
    path: Path,
) -> tuple[
    PackageKind,
    slackware.Info
    | pisi.Info
    | slitaz.Info
    | gentoo.Info
    | puppy.Info
    | alpine.Info
    | xbps.Info
    | None,
]:
    """Identify the package format and keep the metadata a reader found while probing."""
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"\xed\xab\xee\xdb"):
        return "rpm", None
    if header[:8] == b"!<arch>\n":
        return "deb", None
    if header.startswith(b"\x7fELF") and header[8:11] in (b"AI\x01", b"AI\x02"):
        return "appimage", None
    if header.startswith(b"hsqs"):
        return "snap", None
    if header.startswith(b"07070"):
        # A SliTaz package is a CPIO (newc) with receipt, files.list and fs.cpio.lzma.
        try:
            return "slitaz", slitaz.read_info(path)
        except ValueError as slitaz_error:
            raise ValueError(
                tr(
                    "The file contents do not match a valid RPM, DEB, Arch, AppImage, snap, "
                    "Slackware, Pisi Linux, SliTaz, Gentoo, Puppy Linux, Alpine, Void Linux or "
                    "Solus package. {0}"
                ).format(slitaz_error)
            ) from slitaz_error
    if alpine.is_v3(header):
        raise alpine.v3_error()
    if header.startswith(b"PK\x03\x04"):
        if path.suffix.lower() == ".apk":
            # Android apps share the .apk extension with Alpine packages.
            raise ValueError(
                tr(
                    "This is an Android app. MultiPak installs Linux packages; its .apk "
                    "support is for Alpine Linux packages."
                )
            )
        # PiSi and Solus packages are ZIPs with metadata.xml, files.xml and install.tar.xz;
        # the metadata names the distribution, whatever the file is called.
        try:
            info = pisi.read_info(path)
            return ("eopkg" if info.family == "solus" else "pisi"), info
        except ValueError as pisi_error:
            raise ValueError(
                tr(
                    "The file contents do not match a valid RPM, DEB, Arch, AppImage, snap, "
                    "Slackware, Pisi Linux, SliTaz, Gentoo, Puppy Linux, Alpine, Void Linux or "
                    "Solus package. {0}"
                ).format(pisi_error)
            ) from pisi_error
    if alpine.is_candidate(path, header):
        # An Alpine package is gzip TAR with a .PKGINFO like Arch's: read it before Arch.
        try:
            return "apk", alpine.read_info(path)
        except ValueError as alpine_error:
            raise ValueError(
                tr(
                    "The file contents do not match a valid RPM, DEB, Arch, AppImage, snap, "
                    "Slackware, Pisi Linux, SliTaz, Gentoo, Puppy Linux, Alpine, Void Linux or "
                    "Solus package. {0}"
                ).format(alpine_error)
            ) from alpine_error
    if info := _xbps_info(path, header, required=path.suffix.lower() == ".xbps"):
        return "xbps", info
    try:
        _arch_fields(path)
    except (tarfile.TarError, OSError, EOFError, ValueError, lzma.LZMAError) as arch_error:
        diagnosis = str(arch_error)
        # A Gentoo package is an uncompressed TAR with gpkg-1, metadata and image.
        if puppy.is_candidate(path, header):
            # A Puppy package is a gzip or XZ TAR followed by its md5 checksum in text.
            try:
                return "puppy", puppy.read_info(path)
            except ValueError as puppy_error:
                diagnosis = str(puppy_error)
        elif gentoo.is_candidate(path):
            try:
                return "gentoo", gentoo.read_info(path)
            except ValueError as gentoo_error:
                diagnosis = str(gentoo_error)
        elif slackware.is_candidate(path, header):
            try:
                return "slackware", slackware.read_info(path)
            except ValueError as slackware_error:
                diagnosis = str(slackware_error)
        raise ValueError(
            tr(
                "The file contents do not match a valid RPM, DEB, Arch, AppImage, snap, "
                "Slackware, Pisi Linux, SliTaz, Gentoo, Puppy Linux, Alpine, Void Linux or Solus "
                "package. {0}"
            ).format(diagnosis)
        ) from arch_error
    return "arch", None


def package_kind(path: Path) -> PackageKind:
    """Identify a candidate by its header or package metadata."""
    return _package_metadata(path)[0]


def decompress_control(name: str, raw: bytes) -> bytes:
    if name.endswith(".zst"):
        if sys.version_info >= (3, 14):
            from compression.zstd import ZstdFile

            reader = ZstdFile(io.BytesIO(raw), mode="rb")
        else:
            import zstandard

            reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw))
    else:
        opener = {
            ".gz": gzip.GzipFile,
            ".xz": lzma.LZMAFile,
            ".bz2": bz2.BZ2File,
        }.get(Path(name).suffix)
        if opener is None:
            if name != "control.tar":
                raise ValueError(tr("Unsupported DEB metadata compression."))
            return raw
        source = io.BytesIO(raw)
        reader = opener(fileobj=source) if opener is gzip.GzipFile else opener(source)
    with reader:
        unpacked = reader.read(MAX_CONTROL_ARCHIVE + 1)
    if len(unpacked) > MAX_CONTROL_ARCHIVE:
        raise ValueError(tr("Decompressed metadata exceeds the limit."))
    return unpacked


def _deb_members(stream):
    if stream.read(8) != b"!<arch>\n":
        raise ValueError(tr("The file is not a valid DEB package."))
    debian_format = False
    while header := stream.read(60):
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValueError(tr("Invalid DEB header."))
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58])
        if size < 0:
            raise ValueError(tr("Invalid file size."))
        if not debian_format:
            if name != "debian-binary" or size != 4 or stream.read(4) != b"2.0\n":
                raise ValueError(tr("The file is not a valid DEB version 2.0 package."))
            debian_format = True
            continue
        offset = stream.tell()
        yield name, size
        stream.seek(offset + size + size % 2)


def _deb_fields(path: Path) -> dict[str, str]:
    with path.open("rb") as stream:
        for name, size in _deb_members(stream):
            if name.startswith("control.tar"):
                if size > MAX_CONTROL_ARCHIVE:
                    raise ValueError(tr("DEB metadata exceeds the permitted limit."))
                raw = stream.read(size)
                if len(raw) != size:
                    raise ValueError(tr("The DEB file is truncated."))
                raw = decompress_control(name, raw)
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as archive:
                    for member in archive:
                        if member.name.removeprefix("./") != "control":
                            continue
                        if not member.isfile() or member.size > MAX_CONTROL_FILE:
                            raise ValueError(tr("Invalid control file."))
                        source = archive.extractfile(member)
                        if source is None:
                            raise ValueError(tr("Invalid control file."))
                        text = source.read(MAX_CONTROL_FILE + 1).decode("utf-8")
                        return parse_control(text)
                raise ValueError(tr("The package does not contain control metadata."))
    raise ValueError(tr("The file does not contain control.tar."))


def parse_control(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    key = ""
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += " " + line.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    return fields


# The Release field names the distribution that built an RPM: Fedora 1.fc44, Mageia
# 2.mga10, openSUSE Leap lp156 or bp161 (its own builds and Package Hub) or 160100.2.1
# (SUSE Linux Enterprise's base, 16.1), ALT Linux alt1. ALT names its branch only in
# backports (alt1.p11.1).
_RPM_RELEASE_HINTS = (
    (re.compile(r"\.fc(\d+)(?=[._-]|$)"), lambda match: f"fedora-{match[1]}"),
    (re.compile(r"\.mga(\d+)(?=[._-]|$)"), lambda match: f"mageia-{match[1]}"),
    (
        re.compile(r"(?:^|[.-])[lb]p(1[5-9])(\d)(?=[._-]|$)"),
        lambda match: f"opensuse-{match[1]}.{match[2]}",
    ),
    (
        re.compile(r"(?:^|-)(1[5-9])(\d\d)00(?=\.)"),
        lambda match: f"opensuse-{match[1]}.{int(match[2])}",
    ),
    (
        re.compile(r"(?:^|[.-])alt\d+(?:\.p(\d+)(?=[._-]|$))?"),
        lambda match: f"alt-p{match[1]}" if match[1] else "alt",
    ),
)


def rpm_release_hints(text: str) -> list[str]:
    """Distributions named in an RPM Release field or file name, without repeats."""
    hints: list[str] = []
    for pattern, hint in _RPM_RELEASE_HINTS:
        for match in pattern.finditer(text):
            if hint(match) not in hints:
                hints.append(hint(match))
    return hints


def rpm_metadata_hint(release: str, distribution: str) -> str:
    """The distribution and release an RPM was built for, from its header.

    The Release field comes first. Tumbleweed's names no release, and ALT's rarely names
    the branch: their Distribution field does ("openSUSE Tumbleweed", "ALT p11"). ALT
    packages copied from Sisyphus to a branch still say "ALT Sisyphus", so that says only
    that the package is ALT's.
    """
    hints = rpm_release_hints(release)
    hint = hints[0] if hints else ""
    distribution = distribution.strip()
    if not hint and distribution.startswith("openSUSE Tumbleweed"):
        return "opensuse-tumbleweed"
    if not hint and (match := re.fullmatch(r"openSUSE Leap (1[5-9]\.[0-9]+)", distribution)):
        return f"opensuse-{match[1]}"
    if hint in ("", "alt") and (match := re.fullmatch(r"ALT (p[0-9]+)", distribution)):
        return f"alt-{match[1]}"
    if not hint and distribution.startswith("ALT "):
        return "alt"
    return hint


def inspect_package(path: Path, runner: Runner) -> Package:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(tr("Select a regular file."))
    kind, info = _package_metadata(path)
    if kind == "appimage":
        from .appimage import inspect_appimage

        return inspect_appimage(path, runner)
    if kind == "snap":
        from .snap import inspect_snap

        return inspect_snap(path, runner)
    hint = ""
    hint_source = ""
    filename_hint = ""
    if kind == "deb":
        fields = _deb_fields(path)
        for key in ("Package", "Version", "Architecture"):
            if not fields.get(key):
                raise ValueError(tr("The DEB control file is missing the {0} field.").format(key))
        name, version, arch = (fields[k] for k in ("Package", "Version", "Architecture"))
        deps = ", ".join(filter(None, (fields.get("Pre-Depends"), fields.get("Depends"))))
    elif kind == "arch":
        fields = _arch_fields(path)
        name, version, arch = (fields[key][0] for key in ("pkgname", "pkgver", "arch"))
        deps = "\n".join(fields.get("depend", ()))
        hint, hint_source = "arch-rolling", "metadata"
    elif isinstance(info, pisi.Info):
        name, arch = info.name, info.architecture
        version = f"{info.version}-{info.release}"
        deps = info.dependencies
        # Pisi Linux and Solus have one release line each, updated continuously, like Arch.
        if info.family == "solus":
            hint, hint_source = SOLUS_ENVIRONMENT.id, "metadata"
        else:
            hint, hint_source = "pisilinux-rolling", "default"
    elif isinstance(info, slitaz.Info):
        name, version, arch = info.name, info.version, info.architecture
        deps = info.dependencies
        # SliTaz keeps separate repositories for 32-bit (i486) and 64-bit programs.
        hint = SLITAZ_I486_ENVIRONMENT.id if arch == "i486" else SLITAZ_ENVIRONMENT.id
        hint_source = "default"
    elif isinstance(info, gentoo.Info):
        name, version, arch = info.name, info.version, info.architecture
        deps = info.dependencies
        # Gentoo has one release line, updated continuously, like Arch.
        hint, hint_source = GENTOO_ENVIRONMENT.id, "default"
    elif isinstance(info, puppy.Info):
        name, arch = info.name, info.architecture or "noarch"
        version = "-".join(filter(None, (info.version, info.release)))
        deps = info.dependencies
        # Puppy Linux is built from another distribution; pet.specs may say which.
        hint = puppy.compatible_environment(info.compatible_distro, info.compatible_release)
        hint_source = "metadata"
        if not hint:
            # Unknown base: BookwormPup, built from Debian 12, is the most common.
            hint, hint_source = "debian-12", "default"
    elif isinstance(info, alpine.Info):
        name, version, arch = info.name, info.version, info.architecture
        deps = info.dependencies
        # The abuild that built the package tells its Alpine release. Without it, the
        # newest stable release in the catalog is suggested.
        if info.release:
            hint, hint_source = f"alpine-{info.release}", "metadata"
    elif isinstance(info, xbps.Info):
        name, version, arch = info.name, info.version, info.architecture
        deps = info.dependencies
        # glibc, musl and 32-bit packages each have their own Void environment.
        hint, hint_source = void_environment(arch).id, "metadata"
    elif isinstance(info, slackware.Info):
        slackware_info = info
        name, arch = slackware_info.name, slackware_info.arch
        version = f"{slackware_info.version}-{slackware_info.build}"
        deps = ", ".join(slackware_info.required)
        # Slackware 15.0 is the only stable release MultiPak offers for these packages.
        hint, hint_source = "slackware-15.0", "default"
    elif kind == "rpm":
        # The bundled RPM needs this option to inspect signed package headers.
        query = ["/app/bin/rpm", "-qp", "--nosignature"] if host.is_flatpak() else ["rpm", "-qp"]
        raw = runner.run(
            [
                *query,
                "--queryformat",
                "%{NAME}\x1f%{VERSION}-%{RELEASE}\x1f%{ARCH}\x1f%{DISTRIBUTION}",
                str(path),
            ],
            binary=True,
            output_limit=MAX_CONTROL_FILE,
        )
        # rpm adds its diagnostics, such as "warning: <path>: ... NOKEY" for a key the host
        # does not have, to this output: they are not requirements.
        deps = "\n".join(
            line
            for line in runner.run([*query, "--requires", str(path)]).splitlines()
            if not re.match(r"(?:warning|error): ", line)
        )
        # Binary reads keep diagnostics on stderr, separate from this record.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        fields = raw.split("\x1f")
        if len(fields) != 4 or any(
            not field or any(ord(c) < 32 or ord(c) == 127 for c in field) for field in fields
        ):
            raise ValueError(tr("Invalid RPM metadata record."))
        name, version, arch, distribution = fields
        hint = rpm_metadata_hint(version.partition("-")[2], distribution)
        filename_hints = rpm_release_hints(path.name)
        if len(filename_hints) == 1:
            filename_hint = filename_hints[0]
        if hint:
            hint_source = "metadata"
        elif filename_hint:
            hint, hint_source = filename_hint, "filename"
    else:
        raise ValueError(
            tr(
                "Choose an RPM, DEB, prebuilt Arch Linux, Slackware, Pisi Linux, SliTaz, Gentoo, "
                "Puppy Linux, Alpine, Void Linux or Solus package, or an AppImage."
            )
        )
    pattern = (
        r"[A-Za-z0-9@_+][A-Za-z0-9@+._-]*" if kind == "arch" else (r"[A-Za-z0-9][A-Za-z0-9+._-]*")
    )
    if not re.fullmatch(pattern, name):
        raise ValueError(tr("Invalid package name."))
    machine = platform.machine()
    compatible = {
        "x86_64": {"x86_64", "amd64", "all", "noarch"},
        "aarch64": {"aarch64", "arm64", "all", "noarch"},
    }.get(machine, {machine, "all", "noarch"})
    if kind == "xbps":
        # 64-bit computers also run Void's musl and 32-bit (i686) programs.
        compatible = (
            {"x86_64", "x86_64-musl", "i686", "noarch"} if machine == "x86_64" else {"noarch"}
        )
    elif kind == "arch":
        compatible = {machine, "any"}
    elif kind == "slitaz":
        # 64-bit computers also run SliTaz's 32-bit (i486) programs.
        compatible = {"x86_64", "i486", "any"} if machine == "x86_64" else set()
    elif kind == "eopkg":
        # Solus and its container image exist only for x86-64.
        compatible = {"x86_64"} if machine == "x86_64" else set()
    elif kind == "slackware":
        # "fw" marks architecture-independent firmware packages.
        compatible = {machine, "noarch", "fw"}
    if arch not in compatible:
        raise ValueError(tr("Architecture {0} is incompatible with {1}.").format(arch, machine))
    return Package(path, kind, name, version, arch, deps, hint, hint_source, filename_hint)

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

from . import host
from .models import Package
from .process import Runner

MAX_CONTROL_ARCHIVE = 16 * 1024 * 1024
MAX_CONTROL_FILE = 1024 * 1024
MAX_ARCH_METADATA_SCAN = 32 * 1024 * 1024


class _MetadataReader:
    """Cap decompression while looking for metadata near the start of an Arch archive."""

    def __init__(self, source):
        self.source = source
        self.remaining = MAX_ARCH_METADATA_SCAN

    def read(self, size):
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError(tr("Arch package metadata exceeds the read limit."))
        return data


def _arch_fields(path: Path) -> dict[str, list[str]]:
    with ExitStack() as stack:
        stream = stack.enter_context(path.open("rb"))
        header = stream.read(16)
        stream.seek(0)
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
        with tarfile.open(fileobj=_MetadataReader(stream), mode="r|") as archive:
            for member in archive:
                if member.name.removeprefix("./") != ".PKGINFO":
                    continue
                if not member.isfile() or member.size > MAX_CONTROL_FILE:
                    raise ValueError(tr("Invalid .PKGINFO file."))
                source = archive.extractfile(member)
                assert source is not None
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


def package_kind(path: Path) -> str:
    """Identify a candidate by its header; the format parser validates the rest."""
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"\xed\xab\xee\xdb"):
        return "rpm"
    if header[:8] == b"!<arch>\n":
        return "deb"
    if header.startswith(b"\x7fELF") and header[8:11] in (b"AI\x01", b"AI\x02"):
        return "appimage"
    try:
        _arch_fields(path)
    except (tarfile.TarError, OSError, EOFError, ValueError) as error:
        raise ValueError(
            tr(
                "The file contents do not match a valid RPM, DEB, Arch or AppImage package. {0}"
            ).format(error)
        ) from error
    return "arch"


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


def _deb_fields(path: Path) -> dict[str, str]:
    with path.open("rb") as stream:
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
                        assert source is not None
                        text = source.read(MAX_CONTROL_FILE + 1).decode("utf-8")
                        return parse_control(text)
                raise ValueError(tr("The package does not contain control metadata."))
            stream.seek(size + size % 2, 1)
    raise ValueError(tr("The package does not contain control.tar."))


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


def inspect_package(path: Path, runner: Runner) -> Package:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(tr("Select a regular file."))
    kind = package_kind(path)
    if kind == "appimage":
        from .appimage import inspect_appimage

        return inspect_appimage(path, runner)
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
    elif kind == "rpm":
        executable = "/app/bin/rpm" if host.is_flatpak() else "rpm"
        raw = runner.run(
            [
                executable,
                "-qp",
                "--queryformat",
                "%{NAME}\x1f%{VERSION}-%{RELEASE}\x1f%{ARCH}",
                str(path),
            ],
            binary=True,
            output_limit=MAX_CONTROL_FILE,
        )
        deps = runner.run([executable, "-qp", "--requires", str(path)])
        # Binary reads keep diagnostics on stderr, separate from this record.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        fields = raw.split("\x1f")
        if len(fields) != 3 or any(
            not field or any(ord(c) < 32 or ord(c) == 127 for c in field) for field in fields
        ):
            raise ValueError(tr("Invalid RPM metadata record."))
        name, version, arch = fields
        release = version.partition("-")[2]
        match = re.search(r"\.fc(\d+)(?:\.|$)", release)
        hint = f"fedora-{match[1]}" if match else ""
        versions = set(re.findall(r"\.fc(\d+)(?=[._-]|$)", path.name))
        if len(versions) == 1:
            filename_hint = f"fedora-{versions.pop()}"
        if hint:
            hint_source = "metadata"
        elif filename_hint:
            hint, hint_source = filename_hint, "filename"
    else:
        raise ValueError(tr("Choose an RPM, DEB, prebuilt Arch Linux package or AppImage."))
    pattern = (
        r"[A-Za-z0-9@_+][A-Za-z0-9@+._-]*" if kind == "arch" else (r"[A-Za-z0-9][A-Za-z0-9+._-]*")
    )
    if not re.fullmatch(pattern, name):
        raise ValueError(tr("Invalid package name."))
    compatible = {
        "x86_64": {"x86_64", "amd64", "all", "noarch"},
        "aarch64": {"aarch64", "arm64", "all", "noarch"},
    }.get(platform.machine(), {platform.machine(), "all", "noarch"})
    if kind == "arch":
        compatible = {platform.machine(), "any"}
    if arch not in compatible:
        raise ValueError(
            tr("Architecture {0} is incompatible with {1}.").format(arch, platform.machine())
        )
    return Package(path, kind, name, version, arch, deps, hint, hint_source, filename_hint)

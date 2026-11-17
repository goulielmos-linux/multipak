import bz2
import gzip
import io
import lzma
import tarfile

import pytest
from conftest import make_arch

from multipak import gentoo, metadata

FIELDS = (
    ("metadata/CATEGORY", b"app-editors\n"),
    ("metadata/PF", b"demo-editor-2.4_rc1-r2\n"),
    ("metadata/CHOST", b"x86_64-pc-linux-gnu\n"),
    ("metadata/SLOT", b"0/2.4\n"),
    ("metadata/BUILD_ID", b"7\n"),
    ("metadata/repository", b"gentoo\n"),
    ("metadata/DESCRIPTION", b"A test editor\n"),
    ("metadata/USE", b"gui -wayland\n"),
    ("metadata/RDEPEND", b"sys-libs/zlib\n"),
    ("metadata/PDEPEND", b"app-misc/example\n"),
    ("metadata/IDEPEND", b"dev-util/desktop-file-utils\n"),
    ("metadata/environment.bz2", b"ignored build environment"),
)


def _tar(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, contents in members:
            member = tarfile.TarInfo(name)
            if isinstance(contents, str):
                member.type = tarfile.SYMTYPE
                member.linkname = contents
                archive.addfile(member)
            else:
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
    return buffer.getvalue()


def _compress(data, compression):
    if compression == "zst":
        try:
            from compression import zstd
        except ImportError:
            import zstandard

            return zstandard.ZstdCompressor().compress(data)
        return zstd.compress(data)
    return {"gz": gzip.compress, "bz2": bz2.compress, "xz": lzma.compress}[compression](data)


def make_gpkg(path, *, fields=FIELDS, compression="", marker=True, reverse=False, image=None):
    inner = _tar(fields)
    suffix = f".{compression}" if compression else ""
    if compression:
        inner = _compress(inner, compression)
    if image is None:
        image = _tar((("image/usr/bin/demo", b"#!/bin/sh\nexit 77\n"),))
    members = [
        ("gpkg-1", b""),
        (f"metadata.tar{suffix}", inner),
        ("image.tar", image),
        ("Manifest", b"test manifest\n"),
    ]
    if not marker:
        members.pop(0)
    if reverse:
        members.reverse()
    path.write_bytes(_tar((f"demo-editor-2.4_rc1-r2-7/{name}", data) for name, data in members))
    return path


@pytest.mark.parametrize("compression", ["", "gz", "bz2", "xz", "zst"])
def test_reads_gentoo_binary_package_metadata(tmp_path, monkeypatch, compression):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    path = make_gpkg(tmp_path / "demo-editor-2.4_rc1-r2-7.gpkg.tar", compression=compression)
    assert metadata.package_kind(path) == "gentoo"
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "gentoo",
        "demo-editor",
        "2.4_rc1-r2",
        "x86_64",
    )
    assert package.dependencies.splitlines() == [
        "sys-libs/zlib",
        "app-misc/example",
        "dev-util/desktop-file-utils",
    ]
    assert (package.distro_hint, package.distro_hint_source) == ("gentoo-rolling", "default")
    assert gentoo.read_info(path) == gentoo.Info(
        category="app-editors",
        name="demo-editor",
        version="2.4_rc1-r2",
        chost="x86_64-pc-linux-gnu",
        slot="0/2.4",
        build_id="7",
        repository="gentoo",
        summary="A test editor",
        use="gui -wayland",
        dependencies=package.dependencies,
    )
    assert list(tmp_path.iterdir()) == [path]


def test_identifies_renamed_gpkg_with_out_of_order_members(tmp_path):
    path = make_gpkg(tmp_path / "renamed.bin", reverse=True)
    assert metadata.package_kind(path) == "gentoo"


def test_gpkg_suffix_does_not_override_valid_arch_metadata(tmp_path):
    path = make_arch(tmp_path / "package.gpkg.tar", compression="xz")
    assert metadata.package_kind(path) == "arch"


def test_rejects_tar_with_gpkg_suffix_but_without_marker(tmp_path):
    path = make_gpkg(tmp_path / "fake.gpkg.tar", marker=False)
    with pytest.raises(ValueError, match="required Gentoo GPKG members"):
        metadata.package_kind(path)


def test_rejects_marker_without_image_or_manifest(tmp_path):
    path = tmp_path / "fake.gpkg.tar"
    path.write_bytes(_tar((("package/gpkg-1", b""), ("package/metadata.tar", _tar(FIELDS)))))
    with pytest.raises(ValueError, match="required Gentoo GPKG members"):
        metadata.package_kind(path)


@pytest.mark.parametrize(
    "entries,message",
    [
        (FIELDS[1:], "Missing or invalid Gentoo metadata: CATEGORY"),
        (FIELDS + (("metadata/PF", b"another-1"),), "duplicate Gentoo"),
        (FIELDS + (("metadata/alias", "/etc/passwd"),), "Invalid or duplicate Gentoo"),
        (FIELDS + (("metadata/../unsafe", b"data"),), "Invalid or duplicate Gentoo"),
    ],
)
def test_rejects_missing_or_ambiguous_metadata(tmp_path, entries, message):
    path = make_gpkg(tmp_path / "bad.gpkg.tar", fields=entries)
    with pytest.raises(ValueError, match=message):
        metadata.package_kind(path)


def test_skips_the_image_when_reading_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(gentoo, "MAX_CONTAINER_READ", 4096)
    path = make_gpkg(tmp_path / "package.gpkg.tar", compression="gz", image=b"x" * 131_072)
    assert gentoo.read_info(path).name == "demo-editor"


def test_bounds_decompressed_metadata(tmp_path, monkeypatch):
    path = make_gpkg(tmp_path / "package.gpkg.tar", compression="gz")
    monkeypatch.setattr(gentoo, "MAX_METADATA_ARCHIVE", 1024)
    with pytest.raises(ValueError, match="metadata exceeds the read limit"):
        gentoo.read_info(path)


def test_rejects_invalid_zstd_metadata(tmp_path):
    path = tmp_path / "package.gpkg.tar"
    path.write_bytes(
        _tar(
            (
                ("package/gpkg-1", b""),
                ("package/metadata.tar.zst", b"invalid metadata"),
                ("package/image.tar", b"payload"),
                ("package/Manifest", b"test manifest"),
            )
        )
    )
    with pytest.raises(ValueError, match="Gentoo package could not be read"):
        metadata.inspect_package(path, None)


def test_reports_incompatible_gentoo_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    path = make_gpkg(tmp_path / "demo.gpkg.tar")
    with pytest.raises(ValueError, match="Architecture x86_64 is incompatible with aarch64"):
        metadata.inspect_package(path, None)

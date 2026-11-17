import gzip
import lzma
import stat

import pytest

from multipak import metadata, slitaz

RECEIPT = b"""\
# SliTaz package receipt v2.
PACKED_SIZE="16K"
PACKAGE='demo-editor'
VERSION=2.4
EXTRAVERSION="-2"
SHORT_DESC="A \\"test\\" editor." # summary
DEPENDS="glibc
ncursesw"

post_install()
{
    PACKAGE=wrong-name
    exit 77
}
"""


def _cpio(members, *, magic=b"070701", corrupt_checksum=False):
    archive = bytearray()
    trailer = ("TRAILER!!!", b"", 0)
    for inode, (name, data, mode) in enumerate([*members, trailer], start=1):
        name = name.encode() + b"\0"
        checksum = sum(data) if magic == b"070702" else 0
        if corrupt_checksum and name == b"./receipt\0":
            checksum += 1
        fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), checksum)
        archive.extend(magic + "".join(f"{field:08x}" for field in fields).encode() + name)
        archive.extend(b"\0" * (-len(archive) % 4))
        archive.extend(data + b"\0" * (-len(data) % 4))
    return bytes(archive)


def elf(elf_class, machine):
    """The start of an ELF program for a class (1 = 32-bit, 2 = 64-bit) and machine."""
    return b"\x7fELF" + bytes([elf_class, 1, 1]) + b"\0" * 11 + machine.to_bytes(2, "little")


def make_tazpkg(
    path,
    *,
    receipt=RECEIPT,
    compression="lzma",
    magic=b"070701",
    corrupt_checksum=False,
    program=b"#!/bin/sh\nexit 77\n",
):
    payload = _cpio(
        [
            ("fs/usr", b"", stat.S_IFDIR | 0o755),
            ("fs/usr/share/demo.txt", b"text", stat.S_IFREG | 0o644),
            ("fs/usr/bin/demo", program, stat.S_IFREG | 0o755),
        ]
    )
    if compression == "lzma":
        payload = lzma.compress(payload, format=lzma.FORMAT_ALONE)
    else:
        payload = gzip.compress(payload)
    mode = stat.S_IFREG | 0o644
    path.write_bytes(
        _cpio(
            [
                (".", b"", stat.S_IFDIR | 0o755),
                (f"fs.cpio.{compression}", payload, mode),
                ("./receipt", receipt, mode),
                ("files.list", b"/usr/bin/demo\n", mode),
            ],
            magic=magic,
            corrupt_checksum=corrupt_checksum,
        )
    )
    return path


@pytest.mark.parametrize("compression", ["lzma", "gz"])
@pytest.mark.parametrize("arch", ["x86_64", "any"])
def test_reads_a_slitaz_binary_package(tmp_path, monkeypatch, compression, arch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    path = make_tazpkg(tmp_path / f"demo-editor-2.4-2-{arch}.tazpkg", compression=compression)
    assert metadata.package_kind(path) == "slitaz"
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "slitaz",
        "demo-editor",
        "2.4-2",
        arch,
    )
    assert slitaz.read_info(path) == slitaz.Info(
        "demo-editor", "2.4-2", arch, 'A "test" editor.', "glibc\nncursesw"
    )
    assert package.dependencies == "glibc\nncursesw"
    assert package.distro_hint == "slitaz-cooking"
    assert list(tmp_path.iterdir()) == [path]


def test_determines_architecture_when_only_the_extension_was_changed(tmp_path):
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2-x86_64.bin")
    assert metadata.package_kind(path) == "slitaz"
    assert slitaz.read_info(path).architecture == "x86_64"


def test_a_package_without_programs_is_architecture_independent(tmp_path):
    for filename in ("renamed.tazpkg", "demo-editor-2.4-2.tazpkg"):
        path = make_tazpkg(tmp_path / filename)
        assert slitaz.read_info(path).architecture == "any"


@pytest.mark.parametrize(
    ("program", "architecture", "environment"),
    [(elf(1, 3), "i486", "slitaz-cooking-i486"), (elf(2, 62), "x86_64", "slitaz-cooking")],
)
@pytest.mark.parametrize("compression", ["lzma", "gz"])
def test_reads_the_architecture_from_the_first_program(
    tmp_path, monkeypatch, program, architecture, environment, compression
):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    # The file name says nothing: SliTaz names i486 packages without a suffix.
    path = make_tazpkg(tmp_path / "renamed.tazpkg", program=program, compression=compression)
    package = metadata.inspect_package(path, None)
    assert (package.architecture, package.distro_hint) == (architecture, environment)


def test_the_program_wins_over_the_file_name(tmp_path):
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2-x86_64.tazpkg", program=elf(1, 3))
    assert slitaz.read_info(path).architecture == "i486"


def test_rejects_programs_for_other_processors(tmp_path):
    path = make_tazpkg(tmp_path / "demo.tazpkg", program=elf(2, 183))
    with pytest.raises(ValueError, match="Unsupported SliTaz package architecture"):
        slitaz.read_info(path)


def test_stops_looking_for_programs_at_the_read_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(slitaz, "MAX_PAYLOAD_SCAN", 200)
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2-x86_64.tazpkg", program=elf(1, 3))
    assert slitaz.read_info(path).architecture == "x86_64"


def test_rejects_a_generic_cpio_archive(tmp_path):
    path = tmp_path / "sources.tazpkg"
    path.write_bytes(_cpio([("README", b"source package", stat.S_IFREG | 0o644)]))
    with pytest.raises(ValueError, match="required SliTaz package files"):
        metadata.package_kind(path)


@pytest.mark.parametrize(
    "receipt",
    [
        RECEIPT.replace(b"VERSION=2.4", b'VERSION="$(echo 2.4)"'),
        RECEIPT.replace(b'DEPENDS="glibc\nncursesw"', b"DEPENDS=$(echo glibc)"),
    ],
)
def test_rejects_shell_expansion_in_a_metadata_field(tmp_path, receipt):
    path = make_tazpkg(tmp_path / "demo.tazpkg", receipt=receipt)
    with pytest.raises(ValueError, match="not a literal"):
        slitaz.read_info(path)


def test_rejects_duplicate_package_values(tmp_path):
    path = make_tazpkg(
        tmp_path / "demo.tazpkg",
        receipt=RECEIPT.replace(b"VERSION=2.4", b"VERSION=2.4\nVERSION=1.0"),
    )
    with pytest.raises(ValueError, match="Duplicate SliTaz receipt field: VERSION"):
        slitaz.read_info(path)


def test_rejects_symbolic_link_as_receipt(tmp_path):
    path = tmp_path / "demo.tazpkg"
    path.write_bytes(_cpio([("receipt", b"another-file", stat.S_IFLNK | 0o777)]))
    with pytest.raises(ValueError, match="Invalid or duplicate SliTaz package member"):
        slitaz.read_info(path)


def test_checks_receipt_crc_in_cpio_crc_format(tmp_path):
    path = make_tazpkg(tmp_path / "demo.tazpkg", magic=b"070702")
    assert slitaz.read_info(path).name == "demo-editor"
    make_tazpkg(path, magic=b"070702", corrupt_checksum=True)
    with pytest.raises(ValueError, match="Invalid SliTaz receipt checksum"):
        slitaz.read_info(path)


def test_rejects_truncated_archive(tmp_path):
    path = make_tazpkg(tmp_path / "demo.tazpkg")
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(ValueError, match="archive is truncated"):
        slitaz.read_info(path)


def test_limits_receipt_size(tmp_path, monkeypatch):
    path = make_tazpkg(tmp_path / "demo.tazpkg")
    monkeypatch.setattr(slitaz, "MAX_RECEIPT", 32)
    with pytest.raises(ValueError, match="receipt exceeds the read limit"):
        slitaz.read_info(path)


def test_rejects_incompatible_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    path = make_tazpkg(tmp_path / "demo-editor-2.4-2-x86_64.tazpkg")
    with pytest.raises(ValueError, match="Architecture x86_64 is incompatible with aarch64"):
        metadata.inspect_package(path, None)

import gzip
import hashlib
import io
import lzma
import tarfile

import pytest
from conftest import make_arch

from multipak import metadata, puppy

SPECS = (
    b"demo-app-2.1|demo-app|2.1|3|Utility|24K||demo-app-2.1.pet|"
    b"+library,+tool|Demo application|debian|bookworm||\n"
)


def _tar(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in members:
            member = tarfile.TarInfo(name)
            if isinstance(data, str):
                member.type = tarfile.SYMTYPE
                member.linkname = data
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def _elf(machine, elf_class=2, byte_order="little"):
    header = bytearray(64)
    header[:7] = b"\x7fELF" + bytes((elf_class, 1 if byte_order == "little" else 2, 1))
    header[18:20] = machine.to_bytes(2, byte_order)
    return bytes(header)


def make_pet(path, *, compression="gzip", members=None, with_checksum=True):
    if members is None:
        members = [
            ("demo-app-2.1/pet.specs", SPECS),
            ("demo-app-2.1/usr/bin/demo", _elf(62)),
            ("demo-app-2.1/pinstall.sh", b"#!/bin/sh\nexit 77\n"),
        ]
    data = (gzip.compress if compression == "gzip" else lzma.compress)(_tar(members))
    if with_checksum:
        data += hashlib.md5(data, usedforsecurity=False).hexdigest().encode()
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("compression", ["gzip", "xz"])
@pytest.mark.parametrize("filename", ["demo-app-2.1.pet", "renamed.bin"])
def test_reads_pet_package(tmp_path, monkeypatch, compression, filename):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    path = make_pet(tmp_path / filename, compression=compression)
    assert metadata.package_kind(path) == "puppy"
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "puppy",
        "demo-app",
        "2.1-3",
        "x86_64",
    )
    assert puppy.read_info(path) == puppy.Info(
        "demo-app",
        "2.1",
        "3",
        "x86_64",
        "Demo application",
        "+library\n+tool",
        "debian",
        "bookworm",
    )
    assert package.dependencies == "+library\n+tool"
    assert (package.distro_hint, package.distro_hint_source) == ("debian-12", "metadata")
    assert list(tmp_path.iterdir()) == [path]


def test_recognizes_legacy_pets_without_specs(tmp_path):
    path = make_pet(
        tmp_path / "renamed.pet",
        members=[("old-tool-1.2-noarch/usr/bin/tool", b"#!/bin/sh\nexit 77\n")],
    )
    package = metadata.inspect_package(path, None)
    assert (package.name, package.version, package.architecture) == (
        "old-tool",
        "1.2",
        "noarch",
    )


def test_keeps_missing_version_and_architecture_unspecified(tmp_path):
    specs = b"puppy-config|puppy-config|||Utility|24K||puppy-config.pet||Configuration|||\n"
    path = make_pet(tmp_path / "config.pet", members=[("puppy-config/pet.specs", specs)])
    package = metadata.inspect_package(path, None)
    # Without programs, the package is architecture independent.
    assert (package.name, package.version, package.architecture) == (
        "puppy-config",
        "",
        "noarch",
    )
    assert (package.distro_hint, package.distro_hint_source) == ("debian-12", "default")


def test_reads_legacy_named_specs(tmp_path):
    path = make_pet(
        tmp_path / "demo.pet",
        members=[("demo-app-2.1/demo-app-2.1.pet.specs", SPECS)],
    )
    assert puppy.read_info(path).name == "demo-app"


def test_has_no_assumed_architecture_when_elf_headers_disagree(tmp_path):
    path = make_pet(
        tmp_path / "mixed.pet",
        members=[
            ("demo-app-2.1/pet.specs", SPECS),
            ("demo-app-2.1/usr/bin/demo", _elf(62)),
            ("demo-app-2.1/usr/bin/demo-arm", _elf(183)),
        ],
    )
    assert puppy.read_info(path).architecture == ""


def test_rejects_incompatible_elf_architecture(tmp_path, monkeypatch):
    path = make_pet(tmp_path / "demo.pet")
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="Architecture x86_64 is incompatible with aarch64"):
        metadata.inspect_package(path, None)


def test_rejects_a_normal_tarball_with_pet_extension(tmp_path):
    path = make_pet(tmp_path / "demo.pet", with_checksum=False)
    with pytest.raises(ValueError, match="PET checksum trailer"):
        metadata.package_kind(path)


def test_rejects_mismatched_checksum(tmp_path):
    path = make_pet(tmp_path / "demo.pet")
    path.write_bytes(path.read_bytes()[:-32] + b"0" * 32)
    with pytest.raises(ValueError, match="PET checksum does not match"):
        metadata.package_kind(path)


def test_preserves_arch_detection_for_a_pet_extension(tmp_path):
    path = make_arch(tmp_path / "demo.pet", compression="xz")
    assert metadata.package_kind(path) == "arch"


@pytest.mark.parametrize(
    "members,message",
    [
        ([("../pet.specs", SPECS)], "Invalid Puppy Linux package directory"),
        ([("demo-app-2.1/pet.specs", "/a/link")], "pet.specs metadata"),
        (
            [("demo-app-2.1/pet.specs", SPECS), ("demo-app-2.1/pet.specs", SPECS)],
            "duplicate Puppy Linux pet.specs",
        ),
        ([("demo-app-2.1/pet.specs", b"not a record")], "pet.specs metadata"),
    ],
)
def test_rejects_invalid_pet_metadata(tmp_path, members, message):
    path = make_pet(tmp_path / "demo.pet", members=members)
    with pytest.raises(ValueError, match=message):
        metadata.inspect_package(path, None)


def test_limits_metadata_decompression(tmp_path, monkeypatch):
    path = make_pet(
        tmp_path / "demo.pet",
        members=[
            ("demo-app-2.1/usr/share/data", b"x" * 32_768),
            ("demo-app-2.1/pet.specs", SPECS),
        ],
    )
    monkeypatch.setattr(puppy, "MAX_ARCHIVE_SCAN", 16_384)
    with pytest.raises(ValueError, match="metadata exceeds the read limit"):
        puppy.read_info(path)

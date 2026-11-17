import io
import lzma
import tarfile

import pytest
from conftest import make_arch

from multipak import metadata, slackware


def make_slackware(path, *, compression="xz", members=None):
    if members is None:
        members = (
            ("./install/slack-desc", b"# Description\ndemo-app: Demo application\n"),
            ("usr/bin/demo", b"payload"),
        )
    with tarfile.open(path, f"w:{compression}") as archive:
        for name, data in members:
            member = tarfile.TarInfo(name)
            if isinstance(data, str):
                member.type = tarfile.SYMTYPE
                member.linkname = data
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return path


@pytest.mark.parametrize(
    "suffix,compression",
    [
        (".txz", "xz"),
        (".tgz", "gz"),
        (".tar.xz", "xz"),
        (".tar.gz", "gz"),
    ],
)
def test_reads_slackware_package_metadata(tmp_path, suffix, compression):
    path = make_slackware(
        tmp_path / f"demo-app-1.2.3-noarch-2_SBo{suffix}", compression=compression
    )
    assert metadata.package_kind(path) == "slackware"
    assert slackware.read_info(path) == slackware.Info(
        "demo-app", "1.2.3", "noarch", "2_SBo", "Demo application"
    )
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "slackware",
        "demo-app",
        "1.2.3-2_SBo",
        "noarch",
    )
    assert package.dependencies == ""
    assert (package.distro_hint, package.distro_hint_source) == ("slackware-15.0", "default")
    assert list(tmp_path.iterdir()) == [path]


def test_uses_description_for_the_package_named_in_the_filename(tmp_path):
    path = make_slackware(
        tmp_path / "demo-app-1-x86_64-1.txz",
        members=(
            ("install/slack-desc", b"example: Example\ndemo-app:\ndemo-app: Demo app\n"),
            ("install/doinst.sh", b"#!/bin/sh\nexit 77\n"),
        ),
    )
    assert slackware.read_info(path).summary == "Demo app"


@pytest.mark.parametrize(
    "members,message",
    [
        ((("docs/README", b"Source files"),), "does not contain install/slack-desc"),
        ((("install/slack-desc", b"other: Example\n"),), "does not describe the named package"),
        ((("install/slack-desc", "doc/description"),), "Invalid install/slack-desc"),
        (
            (("../outside", b"x"), ("install/slack-desc", b"demo-app: Example\n")),
            "unsafe path",
        ),
    ],
)
def test_rejects_archives_without_valid_slackware_metadata(tmp_path, members, message):
    path = make_slackware(tmp_path / "demo-app-1-noarch-1.txz", members=members)
    with pytest.raises(ValueError, match=message):
        metadata.package_kind(path)


@pytest.mark.parametrize("suffix,compression", [(".tar.xz", "xz"), (".tar.gz", "gz")])
def test_long_extensions_require_slackware_metadata(tmp_path, suffix, compression):
    path = make_slackware(
        tmp_path / f"demo-app-1-noarch-1{suffix}",
        compression=compression,
        members=(("demo-app-1/README", b"Source files"),),
    )
    with pytest.raises(ValueError, match="does not contain install/slack-desc"):
        metadata.package_kind(path)


def test_rejects_a_package_without_an_identifying_filename(tmp_path):
    path = make_slackware(tmp_path / "renamed.txz")
    with pytest.raises(ValueError, match="name-version-arch-build"):
        metadata.inspect_package(path, None)


def test_preserves_arch_detection_for_a_slackware_like_filename(tmp_path):
    path = make_arch(tmp_path / "demo-app-1-noarch-1.txz", compression="xz")
    assert metadata.package_kind(path) == "arch"


def test_rejects_truncated_description(tmp_path):
    member = tarfile.TarInfo("install/slack-desc")
    member.size = 40
    path = tmp_path / "demo-app-1-noarch-1.txz"
    path.write_bytes(lzma.compress(member.tobuf() + b"demo-app:"))
    with pytest.raises(ValueError, match="Slackware package could not be read"):
        metadata.inspect_package(path, None)


@pytest.mark.parametrize("suffix", [".tbz", ".tar.bz2", ".tgz"])
def test_bzip2_slackware_is_outside_initial_support(tmp_path, suffix):
    path = make_slackware(tmp_path / f"demo-app-1-noarch-1{suffix}", compression="bz2")
    with pytest.raises(ValueError, match="contents do not match"):
        metadata.package_kind(path)


@pytest.mark.parametrize("suffix", [".tlz", ".tar.lz"])
def test_lzip_slackware_is_outside_initial_support(tmp_path, suffix):
    path = tmp_path / f"demo-app-1-noarch-1{suffix}"
    path.write_bytes(b"LZIP\x01\x0c")
    with pytest.raises(ValueError, match="contents do not match"):
        metadata.package_kind(path)


def test_bounds_slackware_description(tmp_path, monkeypatch):
    path = make_slackware(tmp_path / "demo-app-1-noarch-1.txz")
    monkeypatch.setattr(slackware, "MAX_SLACK_DESC", 8)
    with pytest.raises(ValueError, match="description exceeds the read limit"):
        slackware.read_info(path)


def test_bounds_slackware_decompression(tmp_path, monkeypatch):
    path = make_slackware(
        tmp_path / "demo-app-1-noarch-1.txz",
        members=(("usr/bin/demo", b"x" * 32_768), ("install/slack-desc", b"demo-app: Example\n")),
    )
    monkeypatch.setattr(slackware, "MAX_METADATA_SCAN", 16_384)
    with pytest.raises(ValueError, match="metadata exceeds the read limit"):
        slackware.read_info(path)


def test_rejects_an_incompatible_slackware_architecture(tmp_path, monkeypatch):
    path = make_slackware(tmp_path / "demo-app-1-x86_64-1.txz")
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="Architecture x86_64 is incompatible with aarch64"):
        metadata.inspect_package(path, None)


def test_reads_required_packages_next_to_the_description(tmp_path):
    path = make_slackware(
        tmp_path / "demo-app-1-x86_64-1.txz",
        members=(
            ("install/doinst.sh", b"( cd usr/lib64 ; ln -sf libdemo.so.1.0 libdemo.so.1 )\n"),
            ("install/slack-desc", b"demo-app: Demo app\n"),
            ("install/slack-required", b"gtk+3 >= 3.24\n# comment\nlibfoo | libbar,zlib\n"),
            ("usr/bin/demo", b"payload"),
        ),
    )
    info = slackware.read_info(path)
    assert info.required == ("gtk+3", "libfoo", "zlib")
    assert metadata.inspect_package(path, None).dependencies == "gtk+3, libfoo, zlib"


def test_split_name_rejects_invalid_fields():
    assert slackware.split_name("demo-1.0-x86_64-1_SBo.txz") == ("demo", "1.0", "x86_64", "1_SBo")
    assert slackware.split_name("demo-1.0-x86_64.txz") is None
    assert slackware.split_name("demo-1.0-x86 64-1.txz") is None
    assert slackware.split_name("demo-1.0-x86_64-1.zip") is None

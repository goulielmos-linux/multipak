import io
import json
import plistlib
import tarfile
from dataclasses import asdict
from pathlib import Path
from subprocess import CalledProcessError

import pytest

from multipak import guest
from multipak.catalog import package_environment, read_cached
from multipak.environment_choices import container_base
from multipak.metadata import inspect_package
from multipak.models import (
    GENTOO_ENVIRONMENT,
    VOID_ENVIRONMENT,
    VOID_ENVIRONMENTS,
    VOID_I686_ENVIRONMENT,
    VOID_MUSL_ENVIRONMENT,
    ExistingEnvironment,
    void_variant,
)


def write_xbps(path: Path, mode: str, **overrides) -> Path:
    props = {
        "pkgname": "demo-app",
        "pkgver": "demo-app-1.2.3_4",
        "architecture": "noarch",
        "run_depends": ["glibc>=2.36_1"],
        "shlib-requires": ["libc.so.6"],
        "short_desc": "Demo application",
    }
    with tarfile.open(path, mode) as archive:
        for name, data in (
            ("./INSTALL", b"exit 999\n"),
            ("./props.plist", plistlib.dumps(props | overrides)),
            ("./files.plist", plistlib.dumps({})),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path


@pytest.mark.parametrize("mode", ["w", "w:xz", "w:gz", "w:bz2"])
def test_inspection_reads_void_metadata(tmp_path, mode):
    path = write_xbps(tmp_path / "renamed-file", mode)
    package = inspect_package(path, None)
    assert (package.kind, package.name, package.version) == ("xbps", "demo-app", "1.2.3_4")
    assert package.dependencies == "glibc>=2.36_1"
    assert package.distro_hint == "void-rolling"


def test_inspects_zstandard(tmp_path):
    filename = write_xbps(tmp_path / "package.xbps", "w")
    payload = filename.read_bytes()
    try:
        from compression.zstd import compress
    except ImportError:
        from zstandard import compress
    filename.write_bytes(compress(payload))
    assert inspect_package(filename, None).kind == "xbps"


@pytest.mark.parametrize(
    ("architecture", "environment"),
    [
        ("x86_64", "void-rolling"),
        ("x86_64-musl", "void-musl"),
        ("i686", "void-i686"),
        ("noarch", "void-rolling"),
    ],
)
def test_each_architecture_suggests_its_environment(
    tmp_path, monkeypatch, architecture, environment
):
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    filename = write_xbps(tmp_path / "package.xbps", "w:xz", architecture=architecture)
    assert inspect_package(filename, None).distro_hint == environment


def test_rejects_invalid_properties(tmp_path):
    filename = write_xbps(tmp_path / "package.xbps", "w:xz", pkgver="wrong-1.0_1")
    with pytest.raises(ValueError, match="Invalid XBPS package properties"):
        inspect_package(filename, None)


def test_old_catalog_exposes_void(tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(GENTOO_ENVIRONMENT)]))
    assert package_environment("void-rolling", list(read_cached(cache))) == VOID_ENVIRONMENT
    assert container_base(VOID_ENVIRONMENT) == "MultiPak-Void-Linux"
    assert ExistingEnvironment("void", "id", "void", "").matches(VOID_ENVIRONMENT)


def test_catalog_from_before_the_variants_adds_them(tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(VOID_ENVIRONMENT)]))
    profiles = list(read_cached(cache))
    assert [item for item in profiles if item.kind == "xbps"] == list(VOID_ENVIRONMENTS)
    assert package_environment("void-musl", profiles) == VOID_MUSL_ENVIRONMENT
    assert package_environment("void-i686", profiles) == VOID_I686_ENVIRONMENT


def test_each_variant_has_its_own_container():
    names = [container_base(item) for item in VOID_ENVIRONMENTS]
    assert names == [
        "MultiPak-Void-Linux",
        "MultiPak-Void-Linux-musl",
        "MultiPak-Void-Linux-32-bits",
    ]
    musl = ExistingEnvironment("MultiPak-Void-Linux-musl", "id", "void", "musl")
    assert musl.matches(VOID_MUSL_ENVIRONMENT) and not musl.matches(VOID_ENVIRONMENT)
    assert musl.multipak_managed
    glibc = ExistingEnvironment("MultiPak-Void-Linux", "id", "void", "rolling")
    assert glibc.matches(VOID_ENVIRONMENT) and not glibc.matches(VOID_I686_ENVIRONMENT)


def test_the_image_tells_the_variant():
    assert void_variant(("", "ghcr.io/void-linux/void-musl-full:latest")) == "musl"
    assert void_variant(("localhost/multipak-void:i686",)) == "i686"
    assert void_variant(("ghcr.io/void-linux/void-glibc-full:latest",)) == "rolling"
    assert void_variant(()) == "rolling"


def test_removal_leaves_xbps_dependency_checks_enabled(monkeypatch):
    monkeypatch.setattr(guest, "package_present", lambda *_: True)

    def refuse(command, *, check, stdin):
        assert command == ["xbps-remove", "-y", "--", "demo-app"]
        assert check
        raise CalledProcessError(1, command)

    monkeypatch.setattr(guest.subprocess, "run", refuse)
    with pytest.raises(CalledProcessError):
        guest.remove_package("xbps", "demo-app")


def test_library_names_only_when_run_depends_is_empty(tmp_path):
    filename = write_xbps(tmp_path / "package.xbps", "w:xz", run_depends=[])
    assert inspect_package(filename, None).dependencies == "libc.so.6"


def test_rejects_32_bit_musl_package(tmp_path, monkeypatch):
    # Void publishes no repository or image for it.
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    filename = write_xbps(tmp_path / "package.xbps", "w:xz", architecture="i686-musl")
    with pytest.raises(ValueError, match="incompatible"):
        inspect_package(filename, None)


def test_rejects_lz4(tmp_path):
    filename = tmp_path / "package.xbps"
    filename.write_bytes(b"\x04\x22\x4d\x18" + bytes(64))
    with pytest.raises(ValueError, match="LZ4"):
        inspect_package(filename, None)


def test_xbps_name_without_plists_is_reported(tmp_path):
    filename = tmp_path / "package.xbps"
    with tarfile.open(filename, "w:xz") as archive:
        member = tarfile.TarInfo("./usr/bin/demo")
        archive.addfile(member, io.BytesIO(b""))
    with pytest.raises(ValueError):
        inspect_package(filename, None)

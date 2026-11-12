import io
import json
import tarfile
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from conftest import make_arch

from multipak import catalog, guest, metadata
from multipak.environments import discover
from multipak.kirigami.configuration import Configuration
from multipak.models import ENVIRONMENTS, ExistingEnvironment
from multipak.process import CommandError
from multipak.suggestions import suggestion_details

ARCH = next(item for item in ENVIRONMENTS if item.kind == "arch")


@pytest.mark.parametrize("compression", ["", "gz", "xz", "bz2", "zst"])
@pytest.mark.parametrize("filename", ["renamed.deb", "application.pkg.tar.zst"])
def test_arch_metadata_is_read_from_contents_without_extracting_or_running_scripts(
    tmp_path, compression, filename
):
    path = make_arch(tmp_path / filename, compression=compression)
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "arch",
        "demo-app",
        "1:2.0-3",
        "any",
    )
    assert package.dependencies.splitlines() == ["glibc>=2.38", "gtk3"]
    assert package.distro_hint == "arch-rolling"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "control",
    [
        "pkgname = demo\narch = any\n",  # Missing version.
        "pkgname = demo\npkgver = 1-1\npkgver = 2-1\narch = any\n",
        "pkgname = -option\npkgver = 1-1\narch = any\n",
        "pkgname = demo\npkgver = 1-1\narch = any\nxdata = pkgtype=src\n",
    ],
)
def test_arch_rejects_incomplete_ambiguous_and_source_packages(tmp_path, control):
    path = make_arch(tmp_path / "bad.pkg.tar.zst", control)
    with pytest.raises(ValueError):
        metadata.inspect_package(path, None)


def test_arch_rejects_wrong_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    path = make_arch(
        tmp_path / "arm.pkg.tar.xz", "pkgname = demo\npkgver = 1-1\narch = aarch64\n", "xz"
    )
    with pytest.raises(ValueError, match="Architecture"):
        metadata.inspect_package(path, None)


@pytest.mark.parametrize("link", [False, True])
def test_arch_rejects_generic_archives_and_symlinked_metadata(tmp_path, link):
    path = tmp_path / "fake.pkg.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(".PKGINFO" if link else "PKGBUILD")
        member.type = tarfile.SYMTYPE if link else tarfile.REGTYPE
        member.linkname = "/etc/passwd" if link else ""
        archive.addfile(member, io.BytesIO())
    with pytest.raises(ValueError):
        metadata.inspect_package(path, None)


def test_arch_bounds_metadata_and_decompression(tmp_path, monkeypatch):
    path = make_arch(tmp_path / "oversized.pkg.tar.zst")
    monkeypatch.setattr(metadata, "MAX_CONTROL_FILE", 5)
    with pytest.raises(ValueError, match="PKGINFO"):
        metadata.inspect_package(path, None)
    monkeypatch.setattr(metadata, "MAX_CONTROL_FILE", 1024)
    monkeypatch.setattr(metadata, "MAX_ARCH_METADATA_SCAN", 100)
    with pytest.raises(ValueError, match="limit"):
        metadata.inspect_package(path, None)


def test_arch_catalog_extends_an_existing_fresh_cache_without_network(tmp_path):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(item) for item in ENVIRONMENTS if item.kind != "arch"]))
    assert catalog.load_catalog(None, cache)[-1] == ARCH
    assert catalog.package_environment("arch-rolling", [ARCH]) == ARCH
    assert catalog.alternatives([ARCH], ARCH) == []


def test_arch_catalog_does_not_offer_dated_tags_as_distribution_versions(monkeypatch):
    monkeypatch.setattr(catalog.host, "which", lambda _: None)
    assert [item for item in catalog.load_catalog(None) if item.kind == "arch"] == [ARCH]


@pytest.mark.parametrize(
    "profile",
    [
        replace(ARCH, id="arch-2026", image="docker.io/library/archlinux:2026"),
        replace(ARCH, id="arch-latest"),
        replace(ARCH, resolved_version="2026"),
    ],
)
def test_arch_cache_cannot_introduce_numbered_versions(tmp_path, profile):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps([asdict(profile)]))
    assert catalog.read_cached(cache) == ()


def test_arch_identification_does_not_claim_to_distinguish_derivatives(arch):
    strength, explanation = suggestion_details(
        metadata.inspect_package(arch, None), ARCH, ARCH.title
    )
    assert strength == "Medium"
    assert "does not distinguish" in explanation
    assert "rolling release" in explanation
    assert "Ubuntu" not in explanation


def test_arch_is_discovered_without_version_id_and_reused_by_configuration(fake_engine):
    runner = fake_engine.runner
    runner.release = "ID=arch\nBUILD_ID=rolling\n"
    runner.container = {
        "Name": "MultiPak-Arch-rolling",
        "Id": "container123",
        "Image": "image123",
        "Config": {"Labels": {"manager": "distrobox"}},
    }
    found = discover(runner, (ARCH,))
    assert len(found) == 1
    assert found[0].matches(ARCH)
    assert not replace(found[0], family="manjaro").matches(ARCH)
    settings = Configuration()
    settings.reset(ENVIRONMENTS, found, ARCH)
    assert settings.profileOptions == [{"title": ARCH.title}]
    assert settings.standard_selection().existing == found[0]
    assert settings.selection().container_name(found) == "MultiPak-Arch-rolling"


def test_arch_install_updates_then_installs_exports_and_uninstalls_without_deleting_environment(
    fake_engine, arch, monkeypatch
):
    engine = fake_engine
    tx = engine.install(metadata.inspect_package(arch, None), ARCH)
    assert tx.container == "MultiPak-Arch-Linux"
    assert tx.environment_id == "arch-rolling"
    assert tx.status == "installed"
    assert len(tx.launchers) == 1
    assert engine.store.launcher(tx, tx.launchers[0]).is_file()
    calls = [call[call.index("pacman") :] for call in engine.runner.calls if "pacman" in call]
    assert calls == [
        ["pacman", "-Syu", "--noconfirm"],
        ["pacman", "-U", "--noconfirm", engine.guest_package],
    ]
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--additional-packages") + 1] == "python"
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx, preserve_environment=True)
    assert engine.runner.container is not None
    assert not engine.runner.package_present
    assert not engine.store.transactions()
    assert not engine.store.launcher(tx, tx.launchers[0]).exists()


def test_arch_reuses_existing_rolling_container_and_refuses_preexisting_package(fake_engine, arch):
    runner = fake_engine.runner
    runner.release = "ID=arch\n"
    runner.container = {
        "Name": "My-apps",
        "Id": "container123",
        "Image": "image123",
        "Config": {"Labels": {"manager": "distrobox"}},
    }
    existing = ExistingEnvironment("My-apps", "container123", "arch", "")
    runner.package_present = True
    with pytest.raises(RuntimeError, match="already installed"):
        fake_engine.install(metadata.inspect_package(arch, None), ARCH, existing)
    assert not any("pacman" in call for call in runner.calls)
    assert not fake_engine.tx.package_install_started
    assert not any(call[:2] == ["distrobox", "create"] for call in runner.calls)


def test_arch_upgrade_failure_never_proceeds_to_local_package_install(
    fake_engine, arch, monkeypatch
):
    run = fake_engine.runner.run

    def fail_upgrade(argv, **kwargs):
        if "pacman" in argv:
            fake_engine.runner.calls.append(argv)
            raise CommandError(argv, 1, "failed to synchronize all databases")
        return run(argv, **kwargs)

    monkeypatch.setattr(fake_engine.runner, "run", fail_upgrade)
    with pytest.raises(CommandError):
        fake_engine.install(metadata.inspect_package(arch, None), ARCH)
    assert fake_engine.tx.status == "failed"
    assert not any("-U" in call for call in fake_engine.runner.calls)
    assert (fake_engine.store.directory(fake_engine.tx.id) / "package.arch").exists()
    monkeypatch.setattr(fake_engine.runner, "run", run)
    assert fake_engine.retry(fake_engine.tx).status == "installed"


def test_guest_arch_queries_files_and_removes_only_requested_package(tmp_path, monkeypatch):
    desktop = tmp_path / "demo.desktop"
    desktop.write_text("[Desktop Entry]\nType=Application\nName=Demo\nExec=demo\n")
    calls, installed = [], [True]

    def run(argv, **kwargs):
        calls.append(argv)
        if "-R" in argv:
            installed[0] = False
        return SimpleNamespace(returncode=0 if installed[0] or "-R" in argv else 1)

    def output(argv, **kwargs):
        calls.append(argv)
        return str(desktop) + "\n"

    monkeypatch.setattr(guest.subprocess, "run", run)
    monkeypatch.setattr(guest.subprocess, "check_output", output)
    assert guest.collect("arch", "demo")["desktops"][0]["name"] == "demo.desktop"
    assert guest.remove_package("arch", "demo") == {"present": False}
    assert ["pacman", "-Qlq", "demo"] in calls
    assert ["pacman", "-R", "--noconfirm", "demo"] in calls
    assert not any(flag in call for call in calls for flag in ("-Rdd", "-Rs", "--nodeps"))

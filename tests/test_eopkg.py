import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_pisi import METADATA, make_pisi

from multipak import catalog, guest, metadata, pisi, solus_image
from multipak.environment_choices import container_base
from multipak.file_associations import FORMATS, FileAssociations
from multipak.models import (
    ENVIRONMENTS,
    SOLUS_BASE_IMAGE,
    SOLUS_ENVIRONMENT,
    ExistingEnvironment,
)
from multipak.process import CommandError
from multipak.suggestions import suggestion_details

SOLUS_METADATA = METADATA.replace("PisiLinux", "Solus").replace(
    "<DistributionRelease>2.0", "<DistributionRelease>1"
)
RELEASE = 'NAME="Solus"\nID=solus\nVERSION="4.9"\nVERSION_ID="4.9"\n'
SCRIPT = Path(__file__).parents[1] / "src/multipak/assets/solus-setup.sh"


@pytest.fixture
def x86_64(monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")


# Package reading


@pytest.mark.parametrize("filename", ["gpicview-0.3.1-7-1-x86_64.eopkg", "demo.EOPKG", "a.zip"])
def test_reads_a_solus_package_whatever_its_name(tmp_path, x86_64, filename):
    path = make_pisi(tmp_path / filename, xml=SOLUS_METADATA)
    assert metadata.package_kind(path) == "eopkg"
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "eopkg",
        "demo-app",
        "2.4_rc1-12",
        "x86_64",
    )
    assert package.dependencies == "libdemo (version >= 1.2, release <= 9)\nxdg-utils"
    assert (package.distro_hint, package.distro_hint_source) == ("solus-rolling", "metadata")
    assert pisi.read_info(path).family == "solus"


def test_metadata_decides_between_pisi_and_solus(tmp_path):
    # The two share the binary format; a file with the other's suffix keeps its own type.
    assert metadata.package_kind(make_pisi(tmp_path / "a.pisi", xml=SOLUS_METADATA)) == "eopkg"
    assert metadata.package_kind(make_pisi(tmp_path / "b.eopkg")) == "pisi"


def test_rejects_another_distribution(tmp_path):
    path = make_pisi(tmp_path / "demo.eopkg", xml=METADATA.replace("PisiLinux", "Pardus"))
    with pytest.raises(ValueError, match="does not identify Pisi Linux or Solus"):
        metadata.package_kind(path)


def test_messages_name_solus(tmp_path):
    path = make_pisi(tmp_path / "demo.eopkg", with_image=False)
    with pytest.raises(ValueError, match="required Solus binary package files"):
        pisi.read_info(path)
    xml = SOLUS_METADATA.replace('release="12"', 'release="x"')
    with pytest.raises(ValueError, match="Invalid Solus package name"):
        pisi.read_info(make_pisi(tmp_path / "renamed.zip", xml=xml))


@pytest.mark.parametrize("filename", ["demo-11-12-1-x86_64.delta.eopkg", "demo-1-2.delta.pisi"])
def test_refuses_delta_packages(tmp_path, filename):
    path = make_pisi(tmp_path / filename, xml=SOLUS_METADATA)
    with pytest.raises(ValueError, match="delta package"):
        metadata.package_kind(path)


def test_solus_exists_only_for_x86_64(tmp_path, monkeypatch):
    path = make_pisi(tmp_path / "demo.eopkg", xml=SOLUS_METADATA)
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="incompatible with aarch64"):
        metadata.inspect_package(path, None)
    xml = SOLUS_METADATA.replace("<Architecture>x86_64", "<Architecture>aarch64")
    with pytest.raises(ValueError, match="incompatible with aarch64"):
        metadata.inspect_package(make_pisi(tmp_path / "arm.eopkg", xml=xml), None)


def test_suggestion_is_high_and_mentions_the_unofficial_image(tmp_path, x86_64):
    package = metadata.inspect_package(make_pisi(tmp_path / "d.eopkg", xml=SOLUS_METADATA), None)
    strength, text = suggestion_details(package, SOLUS_ENVIRONMENT, "Solus")
    assert strength == "High"
    assert "unofficial image" in text


# Environment and catalog


def test_environment_is_rolling_and_reused():
    assert SOLUS_ENVIRONMENT.image == "localhost/multipak-solus:base"
    assert SOLUS_BASE_IMAGE == "docker.io/silkeh/solus:base"
    box = ExistingEnvironment(
        "MultiPak-Solus-Linux", "id", "solus", "4.9", "image", (SOLUS_ENVIRONMENT.image,)
    )
    assert box.matches(SOLUS_ENVIRONMENT)
    assert container_base(SOLUS_ENVIRONMENT) == "MultiPak-Solus-Linux"
    assert not ExistingEnvironment("x", "id", "pisilinux", "", "i").matches(SOLUS_ENVIRONMENT)


def test_catalog_offers_solus_and_rejects_other_images(tmp_path):
    class Offline:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = list(catalog.load_catalog(Offline()))
    assert [item for item in profiles if item.kind == "eopkg"] == [SOLUS_ENVIRONMENT]
    assert catalog.package_environment("solus-rolling", profiles) == SOLUS_ENVIRONMENT
    cache = tmp_path / "catalog.json"
    older = [item for item in ENVIRONMENTS if item.kind != "eopkg"]
    cache.write_text(json.dumps([item.__dict__ for item in older]))
    assert SOLUS_ENVIRONMENT in catalog.read_cached(cache)
    tampered = {**SOLUS_ENVIRONMENT.__dict__, "image": "docker.io/someone/solus:base"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_association_registers_the_solus_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.solus_xml_path.read_text()
    assert 'type="application/x-eopkg-package"' in text and 'pattern="*.eopkg"' in text
    settings.apply({key: key != "eopkg" for key in FORMATS})
    assert not settings.solus_xml_path.exists()


# The local image


class ImageRunner:
    def __init__(self, exists):
        self.exists = exists
        self.calls = []
        self.containerfile = ""

    def run(self, argv, **_options):
        self.calls.append(argv)
        if argv[:3] == ["podman", "image", "exists"] and not self.exists:
            raise CommandError(argv, 1, "")
        if argv[:2] == ["podman", "build"]:
            self.containerfile = Path(argv[argv.index("--file") + 1]).read_text()
        return ""


def test_builds_the_local_image_once(tmp_path):
    runner = ImageRunner(exists=False)
    solus_image.ensure_image(runner, tmp_path / "work", SOLUS_ENVIRONMENT.image)
    pull = next(call for call in runner.calls if call[:2] == ["podman", "pull"])
    assert pull[-1] == SOLUS_BASE_IMAGE
    build = runner.calls[-1]
    assert build[:2] == ["podman", "build"] and "--pull=never" in build
    assert build[build.index("--tag") + 1] == SOLUS_ENVIRONMENT.image
    # Podman cannot start a container as root without the user database.
    assert f"FROM {SOLUS_BASE_IMAGE}\n" in runner.containerfile
    assert "cp -p /usr/share/baselayout/$name /etc/$name" in runner.containerfile
    assert "/etc/os-release" in runner.containerfile
    assert list((tmp_path / "work").iterdir()) == []
    runner = ImageRunner(exists=True)
    solus_image.ensure_image(runner, tmp_path / "work", SOLUS_ENVIRONMENT.image)
    assert len(runner.calls) == 1


# Installation


@pytest.fixture
def solus_engine(fake_engine, x86_64):
    fake_engine.runner.release = RELEASE
    return fake_engine


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_after_preparing_the_environment(solus_engine, tmp_path):
    engine = solus_engine
    package = metadata.inspect_package(make_pisi(tmp_path / "d.eopkg", xml=SOLUS_METADATA), None)
    tx = engine.install(package, SOLUS_ENVIRONMENT)
    assert tx.status == "installed"
    assert (tx.container, tx.environment_id) == ("MultiPak-Solus-Linux", "solus-rolling")
    commands = guest_commands(engine)
    script = f"/tmp/multipak-{tx.id}/solus-setup.sh"
    prepare = commands.index(["sudo", "-n", "sh", script, "--prepare"])
    installation = commands.index(
        [
            "sudo",
            "-n",
            "eopkg",
            "install",
            "--yes-all",
            "--no-color",
            f"/tmp/multipak-{tx.id}/package.eopkg",
        ]
    )
    assert prepare < installation
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == SOLUS_ENVIRONMENT.image
    hook = shlex.split(create[create.index("--pre-init-hooks") + 1])
    assert hook[0] == "sh" and Path(hook[1]).read_bytes() == SCRIPT.read_bytes()


def test_container_update_uses_the_setup_script(store, monkeypatch):
    from multipak import updates

    calls = []
    monkeypatch.setattr(updates, "read_os_release", lambda *_: {"ID": "solus"})
    engine = SimpleNamespace(
        store=store,
        runner=SimpleNamespace(run=lambda argv, **_: calls.append(argv) or ""),
        progress=lambda *_: 0,
    )
    updates.update_container(engine, "MultiPak-Solus-Linux")
    target = updates.SCRIPTS["solus"][1]
    copy = next(call for call in calls if call[:2] == ["podman", "cp"])
    assert copy[2].endswith("solus-setup.sh") and copy[3] == f"MultiPak-Solus-Linux:{target}"
    assert calls[-1][-5:] == ["sudo", "-n", "sh", target, "--upgrade"]


# Guest: state, files and removal


@pytest.fixture
def solus_root(tmp_path, monkeypatch):
    database = tmp_path / "package"
    database.mkdir()
    monkeypatch.setattr(guest, "EOPKG_DATABASE", database)
    monkeypatch.setattr(guest, "PISI_DATABASE", tmp_path / "missing")

    def add(name, version, files=(), needs=()):
        entry = database / f"{name}-{version}"
        entry.mkdir()
        paths = "".join(f"<File><Path>{item}</Path></File>" for item in files)
        (entry / "files.xml").write_text(f"<Files>{paths}</Files>")
        dependencies = "".join(f"<Dependency>{item}</Dependency>" for item in needs)
        (entry / "metadata.xml").write_text(
            f"<PISI><Package><Name>{name}</Name><RuntimeDependencies>{dependencies}"
            "</RuntimeDependencies></Package></PISI>"
        )

    return add


def test_guest_reads_eopkg_records(solus_root):
    solus_root("gpicview", "0.3.1-7", ("usr/bin/gpicview", "usr/share/applications/g.desktop"))
    assert guest.package_present("eopkg", "gpicview")
    assert not guest.package_present("pisi", "gpicview")
    files = ["/usr/bin/gpicview", "/usr/share/applications/g.desktop"]
    assert guest.eopkg_files("gpicview") == files


def test_guest_refuses_to_remove_a_package_others_need(solus_root, monkeypatch):
    solus_root("gtk2", "2.24-1")
    solus_root("gpicview", "0.3.1-7", needs=("gtk2",))
    monkeypatch.setattr(guest.subprocess, "run", lambda *_a, **_k: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="gpicview"):
        guest.remove_package("eopkg", "gtk2")


def test_guest_removes_only_the_package(solus_root, monkeypatch):
    solus_root("gpicview", "0.3.1-7")
    commands = []
    monkeypatch.setattr(guest.subprocess, "run", lambda argv, **_: commands.append(argv))
    guest.remove_package("eopkg", "gpicview")
    assert commands == [["eopkg", "remove", "--no-color", "--yes-all", "gpicview"]]


# The setup script, with eopkg and the system folders redirected


def run_script(tmp_path, *args, dark=True, sudoers=None):
    root = tmp_path / "root"
    tools = tmp_path / "bin"
    tools.mkdir(parents=True)
    log = tmp_path / "log"
    (tools / "eopkg").write_text(f'#!/bin/sh\necho "eopkg $*" >> "{log}"\n')
    (tools / "eopkg").chmod(0o755)
    host = root / "run/host/home/user/.config/gtk-3.0"
    host.mkdir(parents=True)
    (host / "settings.ini").write_text(
        f"[Settings]\ngtk-application-prefer-dark-theme={'true' if dark else 'false'}\n"
    )
    for theme in ("Breeze", "Breeze-Dark"):
        (root / f"usr/share/themes/{theme}/gtk-2.0").mkdir(parents=True)
        (root / f"usr/share/themes/{theme}/gtk-2.0/gtkrc").write_text("")
    (root / "usr/share/os-release").mkdir(parents=True)
    (root / "usr/share/os-release/os-release").write_text(RELEASE)
    (root / "usr/lib").mkdir(parents=True)
    (root / "etc").mkdir()
    if sudoers is not None:
        (root / "etc/sudoers").write_text(sudoers)
    text = SCRIPT.read_text()
    for path in ("/run/host/", "/etc/", "/usr/share/themes/", "/usr/lib/", "/.containersetupdone"):
        text = text.replace(path, f"{root}{path}")
    script = tmp_path / "solus-setup.sh"
    script.write_text(text)
    environment = os.environ | {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "SUDO_UID": str(os.getuid()),
    }
    result = subprocess.run(
        ["sh", str(script), *args], env=environment, capture_output=True, text=True
    )
    return result, root, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("dark", [True, False])
def test_prepare_follows_the_host_theme(tmp_path, dark):
    result, root, log = run_script(tmp_path, "--prepare", dark=dark)
    assert result.returncode == 0, result.stderr
    assert log[0] == "eopkg update-repo --no-color"
    installed = {line.split()[-1] for line in log[1:]}
    assert installed == {"adwaita-icon-theme", "librsvg", "breeze-gtk-theme"}
    gtk3 = (root / "etc/xdg/gtk-3.0/settings.ini").read_text()
    assert f"gtk-application-prefer-dark-theme={'true' if dark else 'false'}" in gtk3
    theme = "Breeze-Dark" if dark else "Breeze"
    gtkrc = (root / "etc/gtk-2.0/gtkrc").read_text()
    assert f'include "{root}/usr/share/themes/{theme}/gtk-2.0/gtkrc"' in gtkrc
    assert (root / "etc/os-release").read_text() == RELEASE


def test_hook_installs_what_distrobox_needs_once(tmp_path):
    result, root, log = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    assert log[0] == "eopkg update-repo --no-color"
    assert {"python3", "sudo", "mesalib", "xauth"} <= set(log[1].split())
    assert (root / "etc/os-release").read_text() == RELEASE
    assert (root / "usr/lib/os-release").read_text() == RELEASE
    assert (root / ".containersetupdone").exists()
    # Solus's sudo reads its own defaults, which include /etc/sudoers.d, when
    # /etc/sudoers does not exist: creating one would replace them.
    assert not (root / "etc/sudoers").exists()
    again = tmp_path / "again"
    assert run_script(again, sudoers="root ALL=(ALL) ALL\n")[0].returncode == 0
    assert "@includedir " in (again / "root/etc/sudoers").read_text()


def test_hook_does_nothing_after_the_setup(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "root/.containersetupdone").write_text("")
    result, _root, log = run_script(tmp_path)
    assert result.returncode == 0 and log == []


def test_upgrade_updates_everything(tmp_path):
    result, _root, log = run_script(tmp_path, "--upgrade")
    assert result.returncode == 0, result.stderr
    assert log == ["eopkg update-repo --no-color", "eopkg upgrade --yes-all --no-color"]

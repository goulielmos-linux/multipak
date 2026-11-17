import json
import shlex
from pathlib import Path

import pytest
from test_pisi import make_pisi

from multipak import catalog, guest
from multipak.container_progress import PackageProgress
from multipak.environment_choices import container_base
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS, PISI_ENVIRONMENT, ExistingEnvironment
from multipak.process import CommandError

RELEASE = 'NAME="PisiLinux"\nID=pisilinux\nVERSION="2.4.4"\nVERSION_ID="2.4.4"\n'


@pytest.fixture
def pisi_engine(fake_engine, monkeypatch):
    fake_engine.runner.release = RELEASE
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    return fake_engine


def install(engine, path):
    return engine.install(inspect_package(path, engine.runner), PISI_ENVIRONMENT)


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_with_pisi_after_starting_the_system_bus(pisi_engine, tmp_path):
    engine = pisi_engine
    tx = install(engine, make_pisi(tmp_path / "demo-app-2.4_rc1-12-p2-x86_64.pisi"))
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Pisi-Linux"
    assert tx.environment_id == "pisilinux-rolling"
    assert tx.package_file == ""
    commands = guest_commands(engine)
    bus = next(index for index, item in enumerate(commands) if "--pisi-bus" in item)
    update = commands.index(["sudo", "-n", "pisi", "update-repo", "--no-color"])
    installation = commands.index(
        [
            "sudo",
            "-n",
            "pisi",
            "install",
            "--no-color",
            "--yes-all",
            "--ignore-safety",
            f"/tmp/multipak-{tx.id}/package.pisi",
        ]
    )
    assert bus < update < installation


def test_new_container_runs_the_bundled_setup_script(pisi_engine, tmp_path):
    engine = pisi_engine
    install(engine, make_pisi(tmp_path / "demo-app.pisi"))
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == "docker.io/pisilinux/chroot:base"
    hook = shlex.split(create[create.index("--pre-init-hooks") + 1])
    assert hook[0] == "sh"
    script = Path(hook[1])
    assert script.parent == engine.environment_home
    text = script.read_text()
    assert "touch /.containersetupdone" in text
    assert "https://beta.pisilinux.org/pisi-index.xml.xz" in text
    assert "@includedir /etc/sudoers.d" in text


def test_pisi_environment_is_rolling_and_reused():
    box = ExistingEnvironment(
        "MultiPak-Pisi-Linux", "id", "pisilinux", "2.4.4", "image", (PISI_ENVIRONMENT.image,)
    )
    assert box.matches(PISI_ENVIRONMENT)
    assert box.multipak_managed
    assert container_base(PISI_ENVIRONMENT) == "MultiPak-Pisi-Linux"
    assert not ExistingEnvironment("x", "id", "arch", "", "image").matches(PISI_ENVIRONMENT)


def test_catalog_offers_the_pisi_environment(tmp_path):
    class Runner:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = catalog.load_catalog(Runner())
    assert [item for item in profiles if item.kind == "pisi"] == [PISI_ENVIRONMENT]
    assert catalog.package_environment("pisilinux-rolling", list(profiles)) == PISI_ENVIRONMENT
    cache = tmp_path / "catalog.json"
    older = [item for item in ENVIRONMENTS if item.kind != "pisi"]
    cache.write_text(json.dumps([item.__dict__ for item in older]))
    assert [item for item in catalog.read_cached(cache) if item.kind == "pisi"] == [
        PISI_ENVIRONMENT
    ]
    tampered = {**PISI_ENVIRONMENT.__dict__, "image": "docker.io/someone/chroot:base"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_association_registers_the_pisi_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.pisi_xml_path.read_text()
    assert 'type="application/x-pisi-package"' in text and 'pattern="*.pisi"' in text
    settings.apply({key: key != "pisi" for key in FORMATS})
    assert not settings.pisi_xml_path.exists()


def test_progress_follows_pisi_package_counts():
    reported = []
    progress = PackageProgress(lambda *item: reported.append(item))
    progress.feed("Downloading 1 / 4\nInstalling 3 / 4\nInstalling 9 / 4\n")
    assert [percent for percent, _ in reported] == [25, 75]


@pytest.fixture
def pisi_root(tmp_path, monkeypatch):
    database = tmp_path / "package"
    database.mkdir()
    monkeypatch.setattr(guest, "PISI_DATABASE", database)

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


def test_guest_reads_the_installed_file_list(pisi_root):
    pisi_root("demo-app", "2.4-12", ("usr/bin/demo", "usr/share/applications/demo.desktop"))
    pisi_root("demo-app-extra", "1-1")
    assert guest.package_present("pisi", "demo-app")
    assert not guest.package_present("pisi", "demo")
    assert guest.pisi_files("demo-app") == ["/usr/bin/demo", "/usr/share/applications/demo.desktop"]


def test_guest_refuses_to_remove_a_package_others_need(pisi_root, monkeypatch):
    pisi_root("libdemo", "1-1")
    pisi_root("demo-app", "2-1", needs=("libdemo",))
    monkeypatch.setattr(guest, "pisi_bus", lambda: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="demo-app"):
        guest.remove_package("pisi", "libdemo")


def test_guest_replaces_a_stale_bus_socket(tmp_path, monkeypatch):
    bus = tmp_path / "dbus"
    bus.mkdir()
    (bus / "system_bus_socket").write_text("")
    (bus / "pid").write_text("999999999")
    monkeypatch.setattr(guest, "PISI_BUS", bus)
    started = []
    monkeypatch.setattr(guest.subprocess, "run", lambda argv, **_: started.append(argv))
    assert guest.pisi_bus() == {"running": True}
    assert started == [["dbus-daemon", "--system", "--fork"]]
    assert not (bus / "pid").exists() and not (bus / "system_bus_socket").exists()


def test_container_update_uses_the_setup_script_for_pisi_linux(store, monkeypatch):
    from types import SimpleNamespace

    from multipak import updates

    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return ""

    monkeypatch.setattr(updates, "read_os_release", lambda *_: {"ID": "pisilinux"})
    engine = SimpleNamespace(store=store, runner=SimpleNamespace(run=run), progress=lambda *_: 0)
    updates.update_container(engine, "MultiPak-Pisi-Linux")
    copy = next(call for call in calls if call[:2] == ["podman", "cp"])
    assert (
        copy[2].endswith("pisi-setup.sh")
        and copy[3] == f"MultiPak-Pisi-Linux:{updates.PISI_SCRIPT}"
    )
    assert calls[-1][-5:] == ["sudo", "-n", "sh", updates.PISI_SCRIPT, "--upgrade"]
    assert ["distrobox", "upgrade", "MultiPak-Pisi-Linux"] not in calls

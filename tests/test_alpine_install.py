import json
import subprocess
from pathlib import Path

import pytest
from test_alpine import make_apk

from multipak import catalog, environments, guest
from multipak.container_progress import PackageProgress
from multipak.environment_choices import container_base
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import (
    ALPINE_ENVIRONMENT,
    ALPINE_ENVIRONMENTS,
    ENVIRONMENTS,
    ExistingEnvironment,
)
from multipak.process import CommandError
from multipak.suggestions import suggestion_details

RELEASE = 'NAME="Alpine Linux"\nID=alpine\nVERSION_ID=3.24.3\nPRETTY_NAME="Alpine Linux v3.24"\n'


@pytest.fixture
def alpine_engine(fake_engine, monkeypatch):
    fake_engine.runner.release = RELEASE
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    return fake_engine


def install(engine, path, environment=ALPINE_ENVIRONMENT):
    return engine.install(inspect_package(path, engine.runner), environment)


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_with_apk_after_updating_the_lists(alpine_engine, tmp_path):
    engine = alpine_engine
    tx = install(engine, make_apk(tmp_path / "demo-viewer-0.2.6-r0.apk"))
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Alpine-3.24"
    assert tx.environment_id == "alpine-3.24"
    package = f"/tmp/multipak-{tx.id}/package.apk"
    commands = guest_commands(engine)
    update = commands.index(["sudo", "-n", "apk", "update"])
    prepare = commands.index(
        ["sudo", "-n", "sh", f"/tmp/multipak-{tx.id}/alpine-setup.sh", "--prepare"]
    )
    assert update < prepare
    verify = commands.index(["apk", "verify", "--quiet", package])
    installation = commands.index(["sudo", "-n", "apk", "add", package])
    assert update < verify < installation
    assert not tx.warnings
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == "docker.io/library/alpine:3.24"
    assert create[create.index("--additional-packages") + 1] == "python3"
    assert "--pre-init-hooks" not in create


def test_untrusted_package_is_installed_with_a_warning(alpine_engine, tmp_path):
    engine = alpine_engine
    engine.runner.apk_untrusted = True
    tx = install(engine, make_apk(tmp_path / "local.apk", signed=False))
    assert tx.status == "installed"
    package = f"/tmp/multipak-{tx.id}/package.apk"
    assert ["sudo", "-n", "apk", "add", "--allow-untrusted", package] in guest_commands(engine)
    assert ["sudo", "-n", "apk", "add", package] not in guest_commands(engine)
    assert len(tx.warnings) == 1 and "not signed with a key Alpine trusts" in tx.warnings[0]


def test_apk_failure_is_reported(alpine_engine, tmp_path, monkeypatch):
    engine = alpine_engine
    original = engine.runner.run

    def run(argv, **kwargs):
        if "apk" in argv and "add" in argv:
            raise CommandError(argv, 1, "ERROR: unable to select packages: so:libold.so.1")
        return original(argv, **kwargs)

    monkeypatch.setattr(engine.runner, "run", run)
    with pytest.raises(CommandError, match="libold"):
        install(engine, make_apk(tmp_path / "old.apk"))


def test_existing_container_with_a_patch_release_is_accepted(alpine_engine, tmp_path):
    engine = alpine_engine
    first = install(engine, make_apk(tmp_path / "a.apk"))
    engine.runner.package_present = False
    existing = ExistingEnvironment(
        first.container, "container123", "alpine", "3.24", "image123", (ALPINE_ENVIRONMENT.image,)
    )
    package = inspect_package(make_apk(tmp_path / "b.apk"), engine.runner)
    tx = engine.install(package, ALPINE_ENVIRONMENT, existing=existing)
    assert tx.status == "installed"


def test_alpine_environment_naming_and_reuse():
    box = ExistingEnvironment(
        "MultiPak-Alpine-3.24", "id", "alpine", "3.24", "image", (ALPINE_ENVIRONMENT.image,)
    )
    assert box.matches(ALPINE_ENVIRONMENT)
    assert box.multipak_managed
    assert container_base(ALPINE_ENVIRONMENT) == "MultiPak-Alpine-3.24"
    assert not ExistingEnvironment("x", "id", "alpine", "3.23", "image").matches(ALPINE_ENVIRONMENT)


def test_discovery_keeps_only_the_release_of_version_id(monkeypatch):
    class Runner:
        def run(self, argv, **_kwargs):
            if argv[:2] == ["podman", "ps"]:
                return json.dumps([{"Names": ["MultiPak-Alpine-3.24"]}])
            if argv[:2] == ["podman", "images"]:
                return json.dumps([{"Id": "img", "Names": [ALPINE_ENVIRONMENT.image]}])
            if argv[:2] == ["podman", "inspect"]:
                return json.dumps(
                    [{"Id": "c1", "Image": "img", "Config": {"Labels": {"manager": "distrobox"}}}]
                )
            raise AssertionError(argv)

    monkeypatch.setattr(environments.host, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(
        environments, "read_os_release", lambda *_: {"ID": "alpine", "VERSION_ID": "3.24.3"}
    )
    (box,) = environments.discover(Runner())
    assert (box.family, box.version) == ("alpine", "3.24")
    assert box.matches(ALPINE_ENVIRONMENT)


def test_offline_catalog_offers_the_fallback_alpine_releases(tmp_path):
    class Runner:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = catalog.load_catalog(Runner())
    assert [item for item in profiles if item.kind == "apk"] == list(ALPINE_ENVIRONMENTS)
    assert catalog.package_environment("alpine-3.24", list(profiles)) == ALPINE_ENVIRONMENT
    cache = tmp_path / "catalog.json"
    older = [item for item in ENVIRONMENTS if item.kind != "apk"]
    cache.write_text(json.dumps([item.__dict__ for item in older]))
    assert [item for item in catalog.read_cached(cache) if item.kind == "apk"] == list(
        ALPINE_ENVIRONMENTS
    )
    tampered = {**ALPINE_ENVIRONMENT.__dict__, "image": "docker.io/someone/alpine:latest"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_suggestion_mentions_musl(tmp_path, monkeypatch):
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("multipak.suggestions.alpine.supported", lambda *_: True)
    package = inspect_package(make_apk(tmp_path / "a.apk"), runner=None)
    strength, text = suggestion_details(package, ALPINE_ENVIRONMENT, "Alpine Linux 3.24")
    # abuild 3.17 built it: Alpine 3.24.
    assert strength == "High"
    assert "musl" in text and "Alpine Linux 3.24" in text


def test_association_registers_the_alpine_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.alpine_xml_path.read_text()
    assert 'type="application/x-alpine-package"' in text
    assert 'pattern="*.apk"' in text and "\\x1f\\x8b" in text
    settings.apply({key: key != "apk" for key in FORMATS})
    assert not settings.alpine_xml_path.exists()


def test_progress_reads_apk_counters():
    reported = []
    progress = PackageProgress(lambda *item: reported.append(item), install_label="Installing")
    progress.feed("( 1/78) Installing libgcc (15.2.0-r5)\n")
    assert reported and reported[-1][1] == "Installing"


DATABASE = """\
C:Q1aaa=
P:gtk+3.0
V:3.24.52-r0
A:x86_64
D:so:libc.musl-x86_64.so.1 so:libglib-2.0.so.0
p:so:libgtk-3.so.0=0.2409.32 so:libgdk-3.so.0=0.2409.32
F:usr/lib
R:libgtk-3.so.0

C:Q1bbb=
P:demo-viewer
V:0.2.6-r0
A:x86_64
D:so:libX11.so.6 so:libgtk-3.so.0 !demo-viewer-legacy
p:cmd:demo-viewer=0.2.6-r0
F:usr
F:usr/bin
R:demo-viewer
F:usr/share/applications
R:demo viewer.desktop
a:0:0:755

C:Q1ccc=
P:demo-viewer-plugins
V:1-r0
D:demo-viewer>=0.2
"""


@pytest.fixture
def alpine_root(tmp_path, monkeypatch):
    database = tmp_path / "installed"
    database.write_text(DATABASE)
    monkeypatch.setattr(guest, "ALPINE_DATABASE", database)
    return database


def test_guest_reads_presence_and_files(alpine_root):
    assert guest.package_present("apk", "demo-viewer")
    assert not guest.package_present("apk", "demo")
    assert guest.alpine_files("demo-viewer") == [
        "/usr/bin/demo-viewer",
        "/usr/share/applications/demo viewer.desktop",
    ]
    with pytest.raises(RuntimeError):
        guest.alpine_files("demo")


def test_guest_finds_dependents_by_name_and_by_provided_library(alpine_root):
    assert guest.alpine_dependents("gtk+3.0") == ["demo-viewer"]
    assert guest.alpine_dependents("demo-viewer") == ["demo-viewer-plugins"]
    assert guest.alpine_dependents("demo-viewer-plugins") == []


def test_guest_refuses_to_remove_a_package_others_need(alpine_root, monkeypatch):
    monkeypatch.setattr(guest.subprocess, "run", lambda *_a, **_k: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="demo-viewer-plugins"):
        guest.remove_package("apk", "demo-viewer")


def test_guest_removes_with_apk_del(alpine_root, monkeypatch):
    removed = []

    def run(argv, **_kwargs):
        removed.append(argv)
        alpine_root.write_text(DATABASE.split("\n\n")[0] + "\n")

    monkeypatch.setattr(guest.subprocess, "run", run)
    assert guest.remove_package("apk", "demo-viewer-plugins") == {"present": False}
    assert removed == [["apk", "del", "--no-progress", "demo-viewer-plugins"]]


SCRIPT = Path(guest.__file__).with_name("assets") / "alpine-setup.sh"


def test_setup_script_follows_the_host_dark_preference(tmp_path):
    text = SCRIPT.read_text()
    assert "gtk-application-prefer-dark-theme" in text and "/etc/xdg/gtk-3.0" in text
    assert "apk add --no-progress lang musl-locales" in text
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    result = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2 and "usage" in result.stderr

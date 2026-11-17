import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from test_gentoo import make_gpkg

from multipak import catalog, gentoo, guest
from multipak.container_progress import PackageProgress
from multipak.environment_choices import container_base
from multipak.file_associations import FORMATS, FileAssociations
from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS, GENTOO_ENVIRONMENT, ExistingEnvironment
from multipak.process import CommandError

RELEASE = "NAME='Gentoo'\nID='gentoo'\nVERSION_ID='2.18'\n"
SCRIPT = Path(gentoo.__file__).with_name("assets") / "gentoo-setup.sh"


@pytest.fixture
def gentoo_engine(fake_engine, monkeypatch):
    fake_engine.runner.release = RELEASE
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    return fake_engine


def install(engine, path):
    return engine.install(inspect_package(path, engine.runner), GENTOO_ENVIRONMENT)


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_with_the_setup_script_after_syncing_the_tree(gentoo_engine, tmp_path):
    engine = gentoo_engine
    tx = install(engine, make_gpkg(tmp_path / "demo-editor-2.4_rc1-r2-7.gpkg.tar"))
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Gentoo-Linux"
    assert tx.environment_id == "gentoo-rolling"
    assert tx.package_file == ""
    script = f"/tmp/multipak-{tx.id}/gentoo-setup.sh"
    commands = guest_commands(engine)
    sync = commands.index(["sudo", "-n", "sh", script, "--sync"])
    installation = commands.index(
        [
            "sudo",
            "-n",
            "sh",
            script,
            "--install",
            f"/tmp/multipak-{tx.id}/package.gentoo",
            "app-editors",
            "demo-editor-2.4_rc1-r2",
        ]
    )
    assert sync < installation


def test_new_container_runs_the_bundled_setup_script(gentoo_engine, tmp_path):
    engine = gentoo_engine
    install(engine, make_gpkg(tmp_path / "demo.gpkg.tar"))
    create = next(call for call in engine.runner.calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == "docker.io/gentoo/stage3:amd64-openrc"
    hook = shlex.split(create[create.index("--pre-init-hooks") + 1])
    assert hook[0] == "sh"
    script = Path(hook[1])
    assert script.parent == engine.environment_home
    text = script.read_text()
    assert "touch /.containersetupdone" in text
    assert "default/linux/amd64/23.0/desktop" in text
    assert "getbinpkg" in text and "getuto" in text


def test_gentoo_environment_is_rolling_and_reused():
    box = ExistingEnvironment(
        "MultiPak-Gentoo-Linux", "id", "gentoo", "2.18", "image", (GENTOO_ENVIRONMENT.image,)
    )
    assert box.matches(GENTOO_ENVIRONMENT)
    assert box.multipak_managed
    assert container_base(GENTOO_ENVIRONMENT) == "MultiPak-Gentoo-Linux"
    assert not ExistingEnvironment("x", "id", "arch", "", "image").matches(GENTOO_ENVIRONMENT)


def test_catalog_offers_the_gentoo_environment(tmp_path):
    class Runner:
        def run(self, *_args, **_kwargs):
            raise CommandError(["distrobox"], 1, "offline")

    profiles = catalog.load_catalog(Runner())
    assert [item for item in profiles if item.kind == "gentoo"] == [GENTOO_ENVIRONMENT]
    assert catalog.package_environment("gentoo-rolling", list(profiles)) == GENTOO_ENVIRONMENT
    cache = tmp_path / "catalog.json"
    older = [item for item in ENVIRONMENTS if item.kind != "gentoo"]
    cache.write_text(json.dumps([item.__dict__ for item in older]))
    assert [item for item in catalog.read_cached(cache) if item.kind == "gentoo"] == [
        GENTOO_ENVIRONMENT
    ]
    tampered = {**GENTOO_ENVIRONMENT.__dict__, "image": "docker.io/someone/stage3:latest"}
    cache.write_text(json.dumps([tampered]))
    assert catalog.read_cached(cache) == ()


def test_association_registers_the_gentoo_type(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings = FileAssociations(
        config_home=tmp_path / "config",
        data_home=tmp_path / "data",
        refresher=lambda: None,
        reader=lambda _mime: "other.desktop",
    )
    settings.apply({key: True for key in FORMATS})
    text = settings.gentoo_xml_path.read_text()
    assert 'type="application/x-gentoo-binary-package"' in text
    assert 'pattern="*.gpkg.tar"' in text
    settings.apply({key: key != "gentoo" for key in FORMATS})
    assert not settings.gentoo_xml_path.exists()


def test_progress_follows_emerge_package_counts():
    reported = []
    progress = PackageProgress(lambda *item: reported.append(item))
    progress.feed(
        ">>> Emerging binary (1 of 4) x11-libs/cairo-1.18.4::gentoo\n"
        ">>> Emerging (3 of 4) x11-misc/xdg-utils-1.2.1::gentoo\n"
        ">>> Emerging binary (9 of 4) x11-misc/other-1::gentoo\n"
    )
    assert [percent for percent, _ in reported] == [0, 50]


def test_dependency_lines_join_choices_and_drop_blockers():
    assert gentoo.dependency_lines(
        "|| ( a/b c/d ) gui? ( x/y ) a/b >=e/f-1:2= !g/h !!i/j", " ou "
    ) == ["a/b ou c/d", "x/y", "a/b", ">=e/f-1:2="]


@pytest.fixture
def gentoo_root(tmp_path, monkeypatch):
    database = tmp_path / "pkg"
    database.mkdir()
    monkeypatch.setattr(guest, "GENTOO_DATABASE", database)

    def add(category, pf, files=(), needs=""):
        entry = database / category / pf
        entry.mkdir(parents=True)
        lines = ["dir /usr"] + [f"obj {item} 0123456789abcdef 1782920722" for item in files]
        lines.append("sym /usr/bin/link -> demo 1782920722")
        (entry / "CONTENTS").write_text("\n".join(lines) + "\n")
        (entry / "RDEPEND").write_text(needs + "\n")

    return add


def test_guest_reads_the_installed_file_list(gentoo_root):
    gentoo_root(
        "app-editors",
        "demo-editor-2.4_rc1-r2",
        ("/usr/bin/demo", "/usr/share/applications/demo editor.desktop"),
    )
    gentoo_root("app-editors", "demo-editor-extra-1.0")
    assert guest.package_present("gentoo", "demo-editor")
    assert not guest.package_present("gentoo", "demo")
    assert not guest.package_present("gentoo", "../pkg")
    assert guest.gentoo_files("demo-editor") == [
        "/usr/bin/demo",
        "/usr/share/applications/demo editor.desktop",
    ]


def test_guest_refuses_to_remove_a_package_others_need(gentoo_root, monkeypatch):
    gentoo_root("dev-libs", "libdemo-1.0")
    gentoo_root("dev-libs", "libdemo-extra-1.0", needs="dev-libs/libdemo-plugins")
    gentoo_root("app-misc", "demo-2", needs="dev-libs/glib:2 >=dev-libs/libdemo-1:0=[gtk]")
    monkeypatch.setattr(guest.subprocess, "run", lambda *_a, **_k: pytest.fail("must not run"))
    with pytest.raises(RuntimeError, match="app-misc/demo-2"):
        guest.remove_package("gentoo", "libdemo")
    assert guest.gentoo_dependents("libdemo-extra") == []


def test_guest_unmerges_each_installed_slot(gentoo_root, monkeypatch):
    gentoo_root("dev-libs", "libdemo-1.0")
    gentoo_root("dev-libs", "libdemo-2.0")
    removed = []

    def run(argv, **_kwargs):
        removed.append(argv)
        for atom in argv[4:]:
            category, pf = atom.removeprefix("=").split("/")
            for item in (guest.GENTOO_DATABASE / category / pf).iterdir():
                item.unlink()
            (guest.GENTOO_DATABASE / category / pf).rmdir()

    monkeypatch.setattr(guest.subprocess, "run", run)
    assert guest.remove_package("gentoo", "libdemo") == {"present": False}
    assert removed == [
        [
            "emerge",
            "--ask=n",
            "--quiet",
            "--unmerge",
            "=dev-libs/libdemo-1.0",
            "=dev-libs/libdemo-2.0",
        ]
    ]


def test_container_update_uses_the_setup_script_for_gentoo(store, monkeypatch):
    from types import SimpleNamespace

    from multipak import updates

    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return ""

    monkeypatch.setattr(updates, "read_os_release", lambda *_: {"ID": "gentoo"})
    engine = SimpleNamespace(store=store, runner=SimpleNamespace(run=run), progress=lambda *_: 0)
    updates.update_container(engine, "MultiPak-Gentoo-Linux")
    target = updates.SCRIPTS["gentoo"][1]
    copy = next(call for call in calls if call[:2] == ["podman", "cp"])
    assert copy[2].endswith("gentoo-setup.sh") and copy[3] == f"MultiPak-Gentoo-Linux:{target}"
    assert calls[-1][-5:] == ["sudo", "-n", "sh", target, "--upgrade"]


def fake_tools(tmp_path):
    """emerge and the other Portage tools record their arguments instead of running."""
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "log"
    for name in ("emerge", "emaint", "emerge-webrsync", "getuto", "eselect", "locale-gen"):
        tool = tools / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\n')
        tool.chmod(0o755)
    return tools, log


def run_script(tmp_path, *arguments):
    tools, log = fake_tools(tmp_path)
    environment = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["sh", str(SCRIPT), *arguments], env=environment, capture_output=True, text=True
    )
    return result, log.read_text() if log.exists() else ""


@pytest.mark.parametrize(
    "category,pf",
    [("../etc", "demo-1"), ("app-misc", "../demo-1"), ("app misc", "demo-1"), (".a", "b-1")],
)
def test_setup_script_rejects_unsafe_package_names(tmp_path, category, pf):
    package = tmp_path / "package.gentoo"
    package.write_bytes(b"x")
    result, log = run_script(tmp_path, "--install", str(package), category, pf)
    assert result.returncode == 2
    assert "invalid Gentoo package name" in result.stderr
    assert "emerge" not in log


def test_setup_script_syntax():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0

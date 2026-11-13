"""AppImage dependency analysis, container installation and isolated check."""

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import appimage, appimage_container, appimage_dependencies, containers
from multipak.appimage_dependencies import (
    Dependency,
    DependencyReport,
    analyze,
    elf_dependencies,
    fedora_python_package,
    python_imports,
    startup_failures,
)
from multipak.containers import ManagedContainer, _save_record
from multipak.engine import Engine
from multipak.models import Package, Transaction
from multipak.process import Cancelled, CommandError, Runner

SYSTEM_BINARY = shutil.which("ls")


# Reading without running anything


@pytest.mark.skipif(SYSTEM_BINARY is None, reason="A dynamically linked system binary is needed")
def test_reads_needed_libraries_and_glibc_version_from_an_elf_file():
    info = elf_dependencies(Path(SYSTEM_BINARY).resolve().read_bytes())
    assert info is not None and info.is_64bit
    assert "libc.so.6" in info.needed
    assert info.glibc is not None and info.glibc >= (2, 2)


def test_non_elf_data_is_ignored():
    assert elf_dependencies(b"#!/bin/sh\necho hello\n") is None
    assert elf_dependencies(b"\x7fELF" + b"\0" * 10) is None


def test_python_imports_separate_required_and_optional_modules():
    required, optional = python_imports(
        "import os, tkinter as tk\n"
        "from gi.repository import Gtk\n"
        "from . import local\n"
        "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        "def later():\n    import numpy\n"
    )
    assert required == {"os", "tkinter", "gi"}
    assert optional == {"yaml", "numpy"}
    assert python_imports("def broken(:\n") is None


def test_fedora_package_names_for_python_modules():
    assert fedora_python_package("yaml") == "python3-pyyaml"
    assert fedora_python_package("_tkinter") == "python3-tkinter"
    assert fedora_python_package("Some_Module") == "python3dist(some-module)"


class FakeArchive:
    """Behaves like appimage.Archive for a fixed set of members."""

    def __init__(self, files):
        self.files = files
        self.extracted = []

    def names(self):
        return list(self.files)

    def read(self, name, limit=1024 * 1024):
        return self.files[name]

    def extract_paths(self, destination, names):
        self.extracted = list(names)
        destination.mkdir()
        for name in names:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.files[name])


@pytest.mark.skipif(SYSTEM_BINARY is None, reason="A dynamically linked system binary is needed")
def test_analysis_reports_only_what_the_system_lacks(tmp_path):
    binary = Path(SYSTEM_BINARY).resolve().read_bytes()
    needed = elf_dependencies(binary).needed
    external = [name for name in needed if name != "libc.so.6"]
    archive = FakeArchive(
        {
            "AppRun": b'#!/bin/bash\nexec /usr/bin/env python3 "$HERE/usr/bin/app.py"\n',
            "usr/bin/app.py": b"import json, tkinter, helper\ntry:\n    import yaml\nexcept "
            b"ImportError:\n    pass\n",
            "usr/bin/helper.py": b"import gi\n",
            "usr/bin/tool": binary,
            "usr/share/doc/README": b"not extracted",
        }
    )
    requests = []

    def probe(runner, modules, libraries):
        requests.append((modules, libraries))
        return {
            "modules": {name: name == "gi" for name in modules},
            "libraries": {name: False for name in libraries},
            "glibc": "glibc 2.1",
        }

    report = analyze(archive, None, scratch=tmp_path, probe=probe)
    assert "usr/share/doc/README" not in archive.extracted
    modules, libraries = requests[0]
    # Standard library and bundled modules are never reported; tkinter needs _tkinter.
    assert modules == ["_tkinter", "gi", "yaml"]
    assert libraries == sorted(external)
    found = {(item.kind, item.name): item for item in report.missing}
    assert found[("python", "_tkinter")].package == "python3-tkinter"
    assert not found[("python", "_tkinter")].optional
    assert found[("python", "yaml")].optional
    assert ("python", "gi") not in found
    assert ("glibc", ".".join(map(str, elf_dependencies(binary).glibc))) in found
    for name in external:
        assert found[("library", name)].package == f"{name}()(64bit)"
    assert "python3-tkinter" in report.packages
    assert list(tmp_path.iterdir()) == []  # The temporary extraction is removed.


def test_analysis_without_the_host_probe_is_incomplete(tmp_path):
    archive = FakeArchive({"AppRun": b"#!/bin/sh\nexec python3 app.py\n", "app.py": b"import x\n"})
    report = analyze(archive, None, scratch=tmp_path, probe=lambda *_: None)
    assert report.incomplete
    assert [item.name for item in report.missing] == ["python3"]


def test_symbolic_links_in_the_extraction_are_not_followed(tmp_path):
    class LinkArchive(FakeArchive):
        def extract_paths(self, destination, names):
            destination.mkdir()
            (destination / "usr/lib").mkdir(parents=True)
            (destination / "usr/lib/libx.so.1").symlink_to("/etc/passwd")

    seen = []
    archive = LinkArchive({"AppRun": b"\0", "usr/lib/libx.so.1": b""})
    analyze(archive, None, scratch=tmp_path, probe=lambda r, m, libs: seen.append(libs) or {})
    assert seen == [[]]


def test_report_text_is_plain_and_marks_optional_items():
    report = DependencyReport(
        (
            Dependency("library", "<b>libx.so.1</b>", "libx.so.1()(64bit)"),
            Dependency("python", "yaml", "python3-pyyaml", optional=True),
        )
    )
    assert report.text() == "• Library <b>libx.so.1</b>\n• Python module PyYAML (optional)"
    assert [item.name for item in report.required] == ["<b>libx.so.1</b>"]


def test_only_known_startup_errors_are_recognized():
    output = (
        "/opt/app/bin/app: error while loading shared libraries: libxcb-cursor.so.0: "
        "cannot open shared object file: No such file or directory\n"
        "ModuleNotFoundError: No module named 'tkinter'\n"
        "ModuleNotFoundError: No module named 'yaml.loader'\n"
        "Segmentation fault\n"
    )
    found = {(item.kind, item.name, item.package) for item in startup_failures(output)}
    assert found == {
        ("library", "libxcb-cursor.so.0", "libxcb-cursor.so.0()(64bit)"),
        ("python", "_tkinter", "python3-tkinter"),
        ("python", "yaml", "python3-pyyaml"),
    }


# Container


class Recorder:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or (lambda argv: "")

    def run(self, argv, **options):
        self.calls.append(argv)
        return self.responses(argv)


def engine_for(store, runner):
    engine = Engine(store, threading.Event())
    engine.runner = runner
    engine.preflight = lambda: None
    return engine


def test_packages_are_installed_without_options_from_names(store):
    def responses(argv):
        if argv[:3] == ["podman", "container", "inspect"]:
            return "c" * 64 + "\n"
        if "rpm" in argv:
            return "python3-tkinter-3.14\nno package provides python3dist(guess)\n"
        return ""

    runner = Recorder(responses)
    engine = engine_for(store, runner)
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    missing = appimage_container.install_packages(
        engine, container, ["python3-tkinter", "python3dist(guess)", "--evil", "python3-tkinter"]
    )
    assert missing == ["python3dist(guess)"]
    # The name is checked against the recorded ID before anything runs inside it.
    assert runner.calls[0][:3] == ["podman", "container", "inspect"]
    install = next(argv for argv in runner.calls if "dnf" in argv)
    assert install[:7] == [
        "distrobox",
        "enter",
        "--name",
        container.name,
        "--no-tty",
        "--no-workdir",
        "--",
    ]
    assert install[-3:] == ["--", "python3-tkinter", "python3dist(guess)"]
    assert "--evil" not in install and "--skip-unavailable" in install


def test_container_is_created_once_and_then_reused(store):
    state = {"created": False}
    container_id = "d" * 64

    def responses(argv):
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([])
        if argv[:2] == ["distrobox", "create"]:
            state["created"] = True
            flags = argv[argv.index("--additional-flags") + 1]
            state["owner"] = flags.split("--label=io.github.multipak.transaction=")[1].split()[0]
            assert "--home" not in argv  # AppImages keep using the user's own folders.
        if argv[:3] == ["podman", "container", "inspect"]:
            return container_id
        if argv[:2] == ["podman", "inspect"]:
            labels = {
                "io.github.multipak.transaction": state.get("owner", ""),
                appimage_container.APPIMAGE_LABEL: "1",
                "manager": "distrobox",
            }
            return json.dumps(
                [
                    {
                        "Id": container_id,
                        "Name": appimage_container.CONTAINER_NAME,
                        "Config": {"Labels": labels},
                    }
                ]
            )
        return ""

    runner = Recorder(responses)
    engine = engine_for(store, runner)
    first = appimage_container.ensure_container(engine)
    assert state["created"] and first.container_id == container_id
    creates = sum(argv[:2] == ["distrobox", "create"] for argv in runner.calls)
    second = appimage_container.ensure_container(engine)
    assert second == first
    assert sum(argv[:2] == ["distrobox", "create"] for argv in runner.calls) == creates


def test_a_taken_default_name_moves_the_appimage_container_to_the_lowest_free_one(store):
    taken = [appimage_container.CONTAINER_NAME, "C2-MultiPak-AppImages", "C4-MultiPak-AppImages"]
    state = {}

    def responses(argv):
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([{"Names": [name]} for name in taken])
        if argv[:2] == ["distrobox", "create"]:
            flags = argv[argv.index("--additional-flags") + 1]
            state["owner"] = flags.split("--label=io.github.multipak.transaction=")[1].split()[0]
            state["name"] = argv[argv.index("--name") + 1]
        if argv[:2] == ["podman", "inspect"]:
            labels = {"io.github.multipak.transaction": state.get("owner", "")}
            return json.dumps([{"Id": "d" * 64, "Config": {"Labels": labels}}])
        return ""

    engine = engine_for(store, Recorder(responses))
    record = appimage_container.ensure_container(engine)
    # No question asked: the lowest free number is used (C3 here, since C4 is taken).
    assert state["name"] == record.name == "C3-MultiPak-AppImages"
    # Later installations find it again under that name.
    assert appimage_container.find_record(store) == record
    assert appimage_container.update_entry(store).name == "C3-MultiPak-AppImages"


def test_isolated_check_has_no_network_files_or_privileges_and_always_cleans_up(store, tmp_path):
    def responses(argv):
        if argv[:3] == ["podman", "container", "inspect"]:
            return "c" * 64
        if argv[:2] == ["podman", "run"]:
            raise TimeoutError("still running")
        return ""

    runner = Recorder(responses)
    engine = engine_for(store, runner)
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    with pytest.raises(TimeoutError):
        appimage_container.isolated_check(engine, container, tmp_path / "AppDir", "e" * 32)
    run = next(argv for argv in runner.calls if argv[:2] == ["podman", "run"])
    for option in ("--network=none", "--cap-drop=all", "--read-only", "--rm"):
        assert option in run
    volumes = [run[i + 1] for i, value in enumerate(run) if value == "--volume"]
    assert volumes == [f"{tmp_path / 'AppDir'}:/opt/multipak-appimage:ro"]
    assert not any(str(Path.home()) in value for value in volumes)
    # timeout(1) inside the container tells a running application from a failed one.
    entry = run.index("--entrypoint")
    assert run[entry + 1] == "/usr/bin/timeout" and run[-1] == "/opt/multipak-appimage/AppRun"
    assert any(argv[:3] == ["podman", "rm", "--force"] for argv in runner.calls)
    assert any(argv[:3] == ["podman", "image", "rm"] for argv in runner.calls)


def test_check_repeats_while_it_finds_installable_dependencies(store, monkeypatch, tmp_path):
    rounds = [
        (Dependency("python", "_tkinter", "python3-tkinter"),),
        (Dependency("library", "libfoo.so.1", "libfoo.so.1()(64bit)"),),
        (Dependency("glibc", "9.99"),),
    ]
    monkeypatch.setattr(appimage_container, "isolated_check", lambda *_: (rounds.pop(0), "unknown"))
    installed = []
    monkeypatch.setattr(
        appimage_container,
        "install_packages",
        lambda engine, container, specs: installed.extend(specs) or [],
    )
    engine = engine_for(store, Recorder())
    warnings = appimage_container.verify_and_complete(engine, None, tmp_path, "f" * 32)
    assert installed == ["python3-tkinter", "libfoo.so.1()(64bit)"]
    assert "python3-tkinter" in warnings[0] and "9.99" in warnings[1]


# Installation, launch and removal


def appimage_package(path):
    return Package(
        path,
        "appimage",
        "Demo",
        "1.0",
        "x86_64",
        desktop_entry="[Desktop Entry]\nType=Application\nName=Demo\nExec=AppRun %U\n",
    )


def test_container_installation_records_the_container_and_installs_packages(store, monkeypatch):
    class Extractor:
        def __init__(self, path, runner):
            pass

        def extract(self, destination):
            destination.mkdir()
            (destination / "AppRun").write_text("#!/bin/sh\n")

    record = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    calls = []
    monkeypatch.setattr(appimage, "Archive", Extractor)
    monkeypatch.setattr(appimage_container, "ensure_container", lambda engine: record)
    monkeypatch.setattr(
        appimage_container,
        "install_packages",
        lambda engine, container, specs: calls.append(("install", specs)) or ["python3dist(x)"],
    )
    monkeypatch.setattr(
        appimage_container,
        "verify_and_complete",
        lambda engine, container, appdir, tx_id, packages: (
            calls.append(("verify", appdir)) or ["checked"]
        ),
    )
    engine = Engine(store, threading.Event())
    tx = Transaction(
        "1" * 32,
        "",
        "",
        "Demo",
        "1.0",
        "appimage",
        "",
        method="appimage",
        container_created=False,
        appimage_container=True,
        appimage_packages=["python3-tkinter", "python3dist(x)"],
        appimage_verify=True,
    )
    engine.tx = tx
    store.save(tx)
    directory = store.appimage_directory(tx.id)
    directory.mkdir(parents=True)
    (directory / "application.AppImage").write_bytes(b"AppImage")
    result = appimage._complete(engine, appimage_package(directory / "application.AppImage"))
    assert result.status == "installed" and result.appimage_mode == "container"
    assert (result.container, result.container_id) == (record.name, record.container_id)
    assert calls[0] == ("install", ["python3-tkinter", "python3dist(x)"])
    assert calls[1] == ("verify", directory / "AppDir")
    assert any("python3dist(x)" in warning for warning in result.warnings)
    assert "checked" in result.warnings
    assert len(result.launchers) == 1


def test_a_failed_check_does_not_undo_the_installation(store, monkeypatch):
    class Extractor:
        def __init__(self, path, runner):
            pass

        def extract(self, destination):
            destination.mkdir()
            (destination / "AppRun").write_text("#!/bin/sh\n")

    record = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    monkeypatch.setattr(appimage, "Archive", Extractor)
    monkeypatch.setattr(appimage_container, "ensure_container", lambda engine: record)
    monkeypatch.setattr(appimage_container, "install_packages", lambda *_: [])

    def failing(*_):
        raise CommandError(["podman", "commit"], 125, "no space left on device")

    monkeypatch.setattr(appimage_container, "verify_and_complete", failing)
    engine = Engine(store, threading.Event())
    tx = Transaction(
        "2" * 32,
        "",
        "",
        "Demo",
        "1.0",
        "appimage",
        "",
        method="appimage",
        container_created=False,
        appimage_container=True,
        appimage_verify=True,
    )
    engine.tx = tx
    store.save(tx)
    directory = store.appimage_directory(tx.id)
    directory.mkdir(parents=True)
    (directory / "application.AppImage").write_bytes(b"AppImage")
    result = appimage._complete(engine, appimage_package(directory / "application.AppImage"))
    assert result.status == "installed"
    assert any("isolated check" in warning for warning in result.warnings)


def installed_container_appimage(store, token="3"):
    tx = Transaction(
        token * 32,
        appimage_container.CONTAINER_NAME,
        "",
        "Demo",
        "1.0",
        "appimage",
        "",
        method="appimage",
        status="installed",
        container_created=False,
        appimage_mode="container",
        container_id="c" * 64,
    )
    store.save(tx)
    appdir = store.appimage_directory(tx.id) / "AppDir"
    appdir.mkdir(parents=True)
    (appdir / "AppRun").write_text("#!/bin/sh\n")
    (appdir.parent / "application.AppImage").write_bytes(b"AppImage")
    return tx


def test_launch_runs_apprun_inside_the_recorded_container(store, monkeypatch):
    tx = installed_container_appimage(store)
    monkeypatch.setattr("multipak.store.Store", lambda: store)
    monkeypatch.setattr(appimage.host, "is_flatpak", lambda: False)
    monkeypatch.setattr(
        appimage.host,
        "run",
        lambda argv, **options: SimpleNamespace(returncode=0, stdout="c" * 64 + "\n"),
    )
    monkeypatch.setattr(appimage.shutil, "which", lambda name: f"/usr/bin/{name}")
    launched = []

    def execv(path, argv):
        launched.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(appimage.os, "execv", execv)
    with pytest.raises(SystemExit):
        appimage.launch(tx.id, ["--file", "x y"])
    path, argv = launched[0]
    assert path == "/usr/bin/distrobox"
    assert argv[1:4] == ["enter", "--name", appimage_container.CONTAINER_NAME]
    assert argv[-3:] == [str(store.appimage_directory(tx.id) / "AppDir/AppRun"), "--file", "x y"]
    assert any(value.startswith("APPDIR=") for value in argv)


def test_launch_refuses_a_missing_container(store, monkeypatch):
    tx = installed_container_appimage(store, "4")
    monkeypatch.setattr("multipak.store.Store", lambda: store)
    monkeypatch.setattr(
        appimage.host, "run", lambda argv, **options: SimpleNamespace(returncode=1, stdout="")
    )
    monkeypatch.setattr(appimage.os, "execv", lambda *_: pytest.fail("Nothing may start"))
    with pytest.raises(appimage.RepairNeeded, match="Application Manager") as raised:
        appimage.launch(tx.id, [])
    assert raised.value.name == "Demo"


def test_launch_refuses_another_container_that_took_the_name(store, monkeypatch):
    tx = installed_container_appimage(store, "7")
    monkeypatch.setattr("multipak.store.Store", lambda: store)
    calls = []

    def run(argv, **options):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="9" * 64 + "\n")

    monkeypatch.setattr(appimage.host, "run", run)
    monkeypatch.setattr(appimage.os, "execv", lambda *_: pytest.fail("Nothing may start"))
    with pytest.raises(appimage.RepairNeeded):
        appimage.launch(tx.id, [])
    # The name is resolved, not only the recorded ID.
    assert calls[0][-1] == appimage_container.CONTAINER_NAME


def test_last_container_appimage_offers_container_removal(store):
    record = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    _save_record(store, record)
    first = installed_container_appimage(store, "5")
    second = installed_container_appimage(store, "6")
    assert containers._candidate(store, first) == record
    assert containers._referenced(store, record)
    store.remove_directory(second)
    store.remove_directory(first)
    assert not containers._referenced(store, record)


def test_direct_appimages_never_claim_the_container(store):
    tx = Transaction("7" * 32, "", "", "Demo", "1", "appimage", "", method="appimage")
    assert containers._candidate(store, tx) is None


def test_installer_choices_for_missing_dependencies():
    pytest.importorskip("PySide6")
    from multipak.kirigami.main import Welcome

    welcome = Welcome("Demo", "1.0", "APPIMAGE")
    assert not welcome.appImageNeedsDependencies
    assert welcome.appimage_options() == {}
    report = DependencyReport(
        (
            Dependency("python", "_tkinter", "python3-tkinter"),
            Dependency("python", "yaml", "python3-pyyaml", optional=True),
        )
    )
    welcome.set_dependency_report(report)
    assert welcome.appImageNeedsDependencies and welcome.appImageContainerSelected
    assert welcome.appimage_options() == {
        "container": True,
        "packages": ("python3-tkinter", "python3-pyyaml"),
        "verify": True,
    }
    welcome.setAppImageVerify(False)
    welcome.chooseAppImageContainer(False)
    assert welcome.appimage_options() == {}
    welcome.chooseAppImageContainer(True)
    # The verification choice survives switching between the options.
    assert welcome.appimage_options()["verify"] is False
    welcome.set_dependency_report(DependencyReport((report.missing[1],)))
    assert not welcome.appImageNeedsDependencies  # Optional components alone do not ask.


@pytest.mark.parametrize("tool", ["unsquashfs", "bsdtar", "7z"])
def test_partial_extraction_passes_member_names_after_the_options(tmp_path, tool):
    archive = object.__new__(appimage.Archive)
    archive.path, archive.kind, archive.offset = tmp_path / "app.AppImage", 2, 1234
    if tool != "unsquashfs":
        archive.kind = 1
    archive.tool = f"/usr/bin/{tool}"
    archive.runner = Recorder()
    archive.extract_paths(tmp_path / "out", ["usr/bin/app", "usr/lib/libx.so.1"])
    command = archive.runner.calls[0]
    assert command[-2:] == ["usr/bin/app", "usr/lib/libx.so.1"]
    assert (tmp_path / "out").is_dir()
    with pytest.raises(ValueError):
        archive.extract_paths(tmp_path / "other", ["../escape"])


# Corrections after review


def check_engine(store, code, output=""):
    def responses(argv):
        if argv[:3] == ["podman", "container", "inspect"]:
            return "c" * 64
        if argv[:2] == ["podman", "run"] and code:
            raise CommandError(argv, code, output)
        return output if argv[:2] == ["podman", "run"] else ""

    runner = Recorder(responses)
    return engine_for(store, runner), runner


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (124, "", "running"),
        (0, "", "exited"),
        (1, "_tkinter.TclError: no display name and no $DISPLAY environment variable", "unknown"),
    ],
)
def test_isolated_check_tells_running_from_unrecognized_failures(
    store, tmp_path, code, output, expected
):
    engine, _runner = check_engine(store, code, output)
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    missing, outcome = appimage_container.isolated_check(engine, container, tmp_path, "e" * 32)
    assert missing == () and outcome == expected


def test_isolated_check_reports_recognized_missing_components(store, tmp_path):
    engine, _runner = check_engine(store, 1, "ModuleNotFoundError: No module named 'yaml'")
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    missing, _outcome = appimage_container.isolated_check(engine, container, tmp_path, "e" * 32)
    assert [item.name for item in missing] == ["yaml"]


def test_isolated_check_cleans_up_after_cancellation(store, tmp_path, monkeypatch):
    def responses(argv):
        if argv[:3] == ["podman", "container", "inspect"]:
            return "c" * 64
        if argv[:2] == ["podman", "commit"]:
            raise Cancelled
        return ""

    cleanup = Recorder()
    monkeypatch.setattr(appimage_container, "_cleanup_runner", lambda runner: cleanup)
    engine = engine_for(store, Recorder(responses))
    container = ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32)
    with pytest.raises(Cancelled):
        appimage_container.isolated_check(engine, container, tmp_path, "e" * 32)
    assert [argv[:3] for argv in cleanup.calls] == [
        ["podman", "rm", "--force"],
        ["podman", "image", "rm"],
    ]


def test_cleanup_runner_ignores_the_cancelled_installation():
    cancelled = threading.Event()
    cancelled.set()
    fresh = appimage_container._cleanup_runner(Runner(cancelled, lambda text: None))
    assert isinstance(fresh, Runner) and not fresh.cancelled.is_set()


def test_inconclusive_check_is_reported(store, monkeypatch, tmp_path):
    monkeypatch.setattr(appimage_container, "isolated_check", lambda *_: ((), "unknown"))
    warnings = appimage_container.verify_and_complete(
        engine_for(store, Recorder()), None, tmp_path, "f" * 32
    )
    assert len(warnings) == 1 and "could not confirm" in warnings[0]
    monkeypatch.setattr(appimage_container, "isolated_check", lambda *_: ((), "running"))
    assert (
        appimage_container.verify_and_complete(
            engine_for(store, Recorder()), None, tmp_path, "f" * 32
        )
        == []
    )


def test_container_packages_cover_everything_the_appimage_uses(tmp_path):
    archive = FakeArchive(
        {"AppRun": b"#!/bin/sh\nexec python3 app.py\n", "app.py": b"import gi, tkinter\n"}
    )

    def probe(runner, modules, libraries):
        # The host already has gi; the container may not.
        return {"modules": {name: name == "gi" for name in modules}, "libraries": {}}

    report = analyze(archive, None, scratch=tmp_path, probe=probe)
    assert [item.name for item in report.missing] == ["_tkinter"]
    assert report.packages == ("python3-tkinter",)
    assert report.container_packages == ("python3", "python3-tkinter", "python3-gobject")
    assert "PyGObject" in report.requirements_text()


def test_sizes_from_the_listing_are_checked_before_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(appimage_dependencies, "MAX_TOTAL_BYTES", 100)

    class Sized(FakeArchive):
        def listing(self):
            return {
                "usr/lib/liba.so.1": ("-", 60),
                "usr/lib/libb.so.1": ("-", 60),
                "usr/lib/libc.so.9": ("l", 9),
            }

    archive = Sized(
        {
            "AppRun": b"\0",
            "usr/lib/liba.so.1": b"\0" * 60,
            "usr/lib/libb.so.1": b"\0" * 60,
            "usr/lib/libc.so.9": b"",
        }
    )
    report = analyze(archive, None, scratch=tmp_path, probe=lambda *_: {})
    # Only what fits the total is extracted; links are never extracted.
    assert archive.extracted == ["usr/lib/liba.so.1"]
    assert report.incomplete


def test_a_linked_directory_cannot_lead_outside_the_extraction(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "libx.so.1").write_bytes(b"\x7fELF" + b"\0" * 60)

    class LinkedParent(FakeArchive):
        def extract_paths(self, destination, names):
            self.extracted = list(names)
            destination.mkdir()
            (destination / "usr").mkdir()
            (destination / "usr/lib").symlink_to(outside)

    seen = []
    archive = LinkedParent({"AppRun": b"\0", "usr/lib/libx.so.1": b""})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    analyze(archive, None, scratch=scratch, probe=lambda r, m, libs: seen.append(libs) or {})
    assert seen == [[]]
    assert (outside / "libx.so.1").exists()  # Never touched.


def test_update_refuses_a_container_that_took_a_recorded_name(store):
    from multipak import updates

    record = ManagedContainer("MultiPak-Fedora-45", "c" * 64, "a" * 32)
    _save_record(store, record)

    def responses(argv):
        if argv[:3] == ["podman", "container", "inspect"]:
            return "9" * 64
        return ""

    runner = Recorder(responses)
    engine = engine_for(store, runner)
    engine.progress = lambda *_: None
    with pytest.raises(RuntimeError, match="Another container"):
        updates.update_container(engine, "MultiPak-Fedora-45")
    assert not any(argv[:2] == ["distrobox", "upgrade"] for argv in runner.calls)


def test_squashfs_listing_reports_types_and_sizes(tmp_path):
    output = (
        "Parallel unsquashfs: Using 4 processors\n"
        "3 inodes (2 blocks) to write\n\n"
        "drwxr-xr-x root/root                45 2026-01-10 13:43 squashfs-root\n"
        "-rwxr-xr-x root/root               305 2026-01-10 13:43 squashfs-root/AppRun\n"
        "-rw-r--r-- root/root           1048576 2026-01-10 13:43 squashfs-root/usr/lib/liba.so.1\n"
        "lrwxrwxrwx root/root                 9 2026-01-10 13:43 squashfs-root/lib -> usr/lib\n"
        "-rw-r--r-- root/root                10 2026-01-10 13:43 squashfs-root/../escape\n"
    )
    archive = object.__new__(appimage.Archive)
    archive.path, archive.kind, archive.offset, archive.tool = tmp_path / "a", 2, 0, "unsquashfs"
    archive.runner = Recorder(lambda argv: output)
    entries = archive.listing()
    assert archive.runner.calls[0][-2] == "-lls"
    assert entries == {
        "AppRun": ("-", 305),
        "usr/lib/liba.so.1": ("-", 1048576),
        "lib": ("l", 9),
    }


def renamed_responses(state, current="Meus AppImages", others=()):
    """Podman with MultiPak's AppImage container c...c renamed to current."""

    def responses(argv):
        if argv[:3] == ["podman", "container", "exists"]:
            if argv[3] not in state.get("existing", {"c" * 64}):
                raise CommandError(argv, 1, "")
            return ""
        if argv[:2] == ["podman", "ps"]:
            rows = [{"Id": "c" * 64, "Names": [state.get("name", current)]}]
            return json.dumps(rows + [{"Id": "e" * 64, "Names": [name]} for name in others])
        if argv[:2] == ["podman", "rename"]:
            state["renamed"] = argv[2:]
            state["name"] = argv[3]
        if argv[:2] == ["distrobox", "create"]:
            state["created"] = True
        if argv[:2] == ["podman", "inspect"]:
            labels = {
                "io.github.multipak.transaction": "a" * 32,
                appimage_container.APPIMAGE_LABEL: "1",
            }
            return json.dumps(
                [{"Id": "c" * 64, "Name": state.get("name", current), "Config": {"Labels": labels}}]
            )
        return ""

    return responses


def test_a_renamed_appimage_container_is_taken_back(store):
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "8")
    state = {}
    engine = engine_for(store, Recorder(renamed_responses(state)))
    record = appimage_container.ensure_container(engine)
    # No second container: the renamed one gets its name back.
    assert "created" not in state
    assert state["renamed"] == ["c" * 64, appimage_container.CONTAINER_NAME]
    assert record.container_id == "c" * 64 and record.name == appimage_container.CONTAINER_NAME
    assert appimage_container.find_record(store) == record
    assert next(item for item in store.transactions() if item.id == tx.id).container == record.name


def test_a_taken_back_container_avoids_a_name_another_container_took(store):
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "9")
    state = {}
    engine = engine_for(
        store, Recorder(renamed_responses(state, others=[appimage_container.CONTAINER_NAME]))
    )
    record = appimage_container.ensure_container(engine)
    assert record.name == "C2-MultiPak-AppImages"
    assert appimage_container.find_record(store) == record
    # The old record is gone and the AppImage follows the new name.
    assert appimage_container._record_names(store) == ["C2-MultiPak-AppImages"]
    assert next(item for item in store.transactions() if item.id == tx.id).container == record.name


def test_appimages_of_a_renamed_or_deleted_container_need_repair(store):
    renamed = installed_container_appimage(store, "a")
    kept = installed_container_appimage(store, "b")
    deleted = installed_container_appimage(store, "d")
    deleted.container_id = "d" * 64
    store.save(deleted)
    kept.container_id = "e" * 64
    store.save(kept)

    def responses(argv):
        return json.dumps(
            [
                {"Id": "c" * 64, "Names": ["Meus AppImages"]},
                {"Id": "e" * 64, "Names": [appimage_container.CONTAINER_NAME]},
            ]
        )

    found = appimage_container.needing_repair(engine_for(store, Recorder(responses)))
    assert {tx.id for tx in found} == {renamed.id, deleted.id}


def test_nothing_needs_repair_when_podman_cannot_be_queried(store):
    installed_container_appimage(store, "a")

    def responses(argv):
        raise CommandError(argv, 125, "podman failed")

    assert appimage_container.needing_repair(engine_for(store, Recorder(responses))) == []


def no_system_dependencies(monkeypatch, everything=False):
    monkeypatch.setattr(appimage_container, "_system_has_everything", lambda engine, tx: everything)


def test_repair_plan_for_a_renamed_container(store, monkeypatch):
    no_system_dependencies(monkeypatch)
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "a")
    engine = engine_for(store, Recorder(renamed_responses({})))
    plan = appimage_container.repair_plan(engine, tx.id)
    assert plan["kind"] == "renamed" and not plan["deleted"]
    assert (plan["old"], plan["current"], plan["target"]) == (
        appimage_container.CONTAINER_NAME,
        "Meus AppImages",
        appimage_container.CONTAINER_NAME,
    )
    # Nothing changes before the user confirms.
    assert not any(argv[:2] == ["podman", "rename"] for argv in engine.runner.calls)


def test_repair_plan_for_a_deleted_container(store, monkeypatch):
    no_system_dependencies(monkeypatch)
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "a")
    engine = engine_for(store, Recorder(renamed_responses({"existing": set()})))
    plan = appimage_container.repair_plan(engine, tx.id)
    assert plan["kind"] == "recreate" and plan["deleted"]


def test_repair_plan_prefers_the_system_when_it_has_every_dependency(store, monkeypatch):
    no_system_dependencies(monkeypatch, everything=True)
    tx = installed_container_appimage(store, "a")
    engine = engine_for(store, Recorder(renamed_responses({"existing": set()})))
    assert appimage_container.repair_plan(engine, tx.id)["kind"] == "system"


def test_repair_on_the_system_leaves_containers_alone(store):
    tx = installed_container_appimage(store, "a")
    runner = Recorder(lambda argv: pytest.fail("No container command may run"))
    repaired = appimage_container.repair(engine_for(store, runner), tx.id, on_system=True)
    assert repaired.appimage_mode == "extracted" and not repaired.appimage_container
    assert (repaired.container, repaired.container_id) == ("", "")
    assert store.appimage_directory(tx.id).joinpath("AppDir/AppRun").is_file()


def test_repair_takes_a_renamed_container_back_without_reinstalling(store):
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "a")
    tx.container = "Meus AppImages-antigo"
    tx.appimage_packages = ["python3-tkinter"]
    store.save(tx)
    state = {}
    runner = Recorder(renamed_responses(state))
    repaired = appimage_container.repair(engine_for(store, runner), tx.id)
    assert repaired.container == appimage_container.CONTAINER_NAME
    assert repaired.container_id == "c" * 64
    assert not any("dnf" in argv for argv in runner.calls)


def test_repair_installs_the_dependencies_in_a_new_container(store, monkeypatch):
    tx = installed_container_appimage(store, "a")
    tx.appimage_packages = ["python3-tkinter", "libfoo.so.1()(64bit)"]
    store.save(tx)
    new = ManagedContainer(appimage_container.CONTAINER_NAME, "d" * 64, "b" * 32)
    monkeypatch.setattr(appimage_container, "ensure_container", lambda engine: new)
    installed = []
    monkeypatch.setattr(
        appimage_container,
        "install_packages",
        lambda engine, container, specs: installed.append((container, list(specs))) or [],
    )
    repaired = appimage_container.repair(engine_for(store, Recorder()), tx.id)
    assert installed == [(new, ["python3-tkinter", "libfoo.so.1()(64bit)"])]
    assert (repaired.container, repaired.container_id) == (new.name, new.container_id)


def test_the_check_records_what_it_installed_for_later_repairs(store, monkeypatch):
    rounds = iter(
        [
            ((Dependency("library", "libfoo.so.1", "libfoo.so.1()(64bit)"),), "unknown"),
            ((), "running"),
        ]
    )
    monkeypatch.setattr(appimage_container, "isolated_check", lambda *args: next(rounds))
    monkeypatch.setattr(appimage_container, "install_packages", lambda *args: [])
    packages = ["python3-tkinter"]
    appimage_container.verify_and_complete(
        engine_for(store, Recorder()), None, Path("/x"), "f" * 32, packages
    )
    assert packages == ["python3-tkinter", "libfoo.so.1()(64bit)"]


def test_launcher_notifies_when_the_appimage_needs_repair(monkeypatch):
    from multipak import appimage_launcher

    def broken(*args):
        raise appimage.RepairNeeded("Demo")

    sent = []
    monkeypatch.setattr(appimage_launcher, "launch", broken)
    monkeypatch.setattr(appimage_launcher, "notify", lambda *args: sent.append(args))
    monkeypatch.setattr("sys.argv", ["multipak-appimage", "a" * 32])
    assert appimage_launcher.main() == 1
    assert sent == [("Could not open Demo.", "Open the MultiPak Application Manager to repair it.")]


def test_notification_text_is_a_safe_gvariant_string():
    from multipak.appimage_launcher import _text

    assert _text("it's \\ a\nname") == "'it\\'s \\\\ a name'"


def test_a_renamed_package_container_is_no_longer_multipak_s():
    from multipak.environment_choices import default_environment
    from multipak.models import Environment, ExistingEnvironment

    profile = Environment("fedora-45", "Fedora 45", "rpm", "img:45")
    renamed = ExistingEnvironment("Meu Fedora", "a" * 64, "fedora", "45", managed_home=True)
    kept_prefix = ExistingEnvironment("MultiPak-Fedora-45-velho", "b" * 64, "fedora", "45")
    wrong_version = ExistingEnvironment("MultiPak-Fedora-45", "c" * 64, "fedora", "44")
    alpha_arch = ExistingEnvironment("MultiPak-Arch-rolling", "d" * 64, "arch", "")
    assert renamed.multipak_number is None and kept_prefix.multipak_number is None
    assert wrong_version.multipak_number is None
    assert alpha_arch.multipak_number == 1  # Name given by 1.0.0 Alpha 1.
    assert default_environment([renamed, kept_prefix, wrong_version], profile) is None


def forget_setup(store, rows):
    def installed(identifier, container, container_id):
        tx = Transaction(
            identifier * 32,
            container,
            "image",
            "app-" + identifier,
            "1.0",
            "rpm",
            "",
            status="installed",
            container_id=container_id,
            launchers=[f"multipak-{identifier * 32}-app.desktop"],
        )
        store.save(tx)
        store.launcher(tx, tx.launchers[0]).write_text("[Desktop Entry]\n")
        return tx

    kept = installed("1", "MultiPak-Fedora-45", "a" * 64)
    renamed = installed("2", "MultiPak-Ubuntu-24.04", "b" * 64)
    deleted = installed("3", "meu-fedora", "c" * 64)
    engine = engine_for(store, Recorder(lambda argv: json.dumps(rows)))
    return engine, kept, renamed, deleted


def test_applications_of_renamed_or_deleted_containers_are_forgotten(store):
    rows = [
        {"Id": "a" * 64, "Names": ["MultiPak-Fedora-45"]},
        {"Id": "b" * 64, "Names": ["Meu Ubuntu"]},  # Renamed; "c" was deleted.
    ]
    engine, kept, renamed, deleted = forget_setup(store, rows)
    forgotten = containers.forget_abandoned(engine)
    assert {tx.id for tx in forgotten} == {renamed.id, deleted.id}
    assert [tx.id for tx in store.transactions()] == [kept.id]
    assert store.launcher(kept, kept.launchers[0]).exists()
    assert not list(store.applications.glob(f"multipak-{renamed.id}-*"))
    # Nothing ran inside any container.
    assert all(argv[:2] == ["podman", "ps"] for argv in engine.runner.calls)


def test_nothing_is_forgotten_when_podman_cannot_be_queried(store):
    engine, *_ = forget_setup(store, [])

    def failing(argv, **options):
        raise CommandError(argv, 125, "podman unavailable")

    engine.runner.run = failing
    engine._log = lambda text: None
    assert containers.forget_abandoned(engine) == []
    assert len(store.transactions()) == 3


def test_repair_plan_only_relinks_when_the_container_already_has_its_name(store, monkeypatch):
    no_system_dependencies(monkeypatch, everything=True)
    _save_record(store, ManagedContainer(appimage_container.CONTAINER_NAME, "c" * 64, "a" * 32))
    tx = installed_container_appimage(store, "a")
    tx.container = "Meus AppImages"
    store.save(tx)
    state = {"name": appimage_container.CONTAINER_NAME}
    engine = engine_for(store, Recorder(renamed_responses(state)))
    assert appimage_container.repair_plan(engine, tx.id)["kind"] == "relink"

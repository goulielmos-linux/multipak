import importlib.util
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

PROJECT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "version, external_zstd", [("3.12", True), ("3.14", False), ("3.15", False)]
)
def test_recent_python_does_not_request_the_external_zstandard_extension(version, external_zstd):
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    environment = default_environment() | {
        "python_version": version,
        "python_full_version": version + ".0",
    }
    required = []
    for value in config["project"]["dependencies"]:
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate(environment):
            required.append(requirement.name)
    assert ("zstandard" in required) is external_zstd


def test_failed_dependency_install_does_not_register_desktop_or_mime_associations(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "install_local", PROJECT / "scripts/install_local.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "pip" in argv:
            raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(SystemExit, match="were not changed"):
        installer.main()
    assert len(calls) == 2
    assert "--only-binary=:all:" in calls[-1]
    assert not (tmp_path / "data/applications").exists()


def test_multipak_launcher_does_not_import_the_private_qt_runtime(monkeypatch):
    from multipak import launcher

    package = "/tmp/package with spaces; $(never execute).rpm"
    monkeypatch.setattr(launcher.sys, "argv", ["multipak-installer", package])
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        monkeypatch.setenv(key, "/private/runtime")
    calls = []

    def execute(binary, arguments, environment):
        calls.append((binary, arguments, environment))
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execve", execute)
    with pytest.raises(SystemExit):
        launcher.main()
    binary, arguments, environment = calls[0]
    assert binary == "/usr/bin/python3"
    assert Path(arguments[1]).is_file()
    assert arguments[2:] == [package]
    assert not ({"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"} & environment.keys())


def test_native_manager_launcher_uses_system_qt_and_the_manager_bootstrap(monkeypatch):
    from multipak import launcher

    monkeypatch.setattr(launcher.sys, "argv", ["multipak-manager"])
    monkeypatch.setenv("PYTHONPATH", "/private/runtime")
    calls = []

    def execute(binary, arguments, environment):
        calls.append((binary, arguments, environment))
        raise SystemExit(0)

    monkeypatch.setattr(launcher.os, "execve", execute)
    with pytest.raises(SystemExit):
        launcher.manager_main()
    binary, arguments, environment = calls[0]
    assert binary == "/usr/bin/python3"
    assert arguments[2:] == ["--manager"]
    assert "PYTHONPATH" not in environment
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text())
    assert config["project"]["scripts"]["multipak-manager"] == ("multipak.launcher:manager_main")


def test_desktop_registration_installs_arch_mime_without_claiming_other_archives(
    tmp_path, monkeypatch
):
    import xml.etree.ElementTree as ET

    spec = importlib.util.spec_from_file_location(
        "register_desktop", PROJECT / "scripts/register_desktop.py"
    )
    registration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registration)
    data = tmp_path / "share"
    monkeypatch.setattr(
        registration.sys,
        "argv",
        ["register_desktop.py", str(tmp_path / "bin/multipak"), str(data), str(PROJECT)],
    )
    registration.main()
    mime = data / "mime/packages/io.github.multipak.packages.xml"
    root = ET.parse(mime).getroot()
    namespace = {"mime": "http://www.freedesktop.org/standards/shared-mime-info"}
    patterns = {item.attrib["pattern"] for item in root.findall(".//mime:glob", namespace)}
    assert patterns == {
        "*.pkg.tar",
        "*.pkg.tar.zst",
        "*.pkg.tar.xz",
        "*.pkg.tar.gz",
        "*.pkg.tar.bz2",
    }
    assert (
        "application/x-archlinux-package;"
        in (data / "applications/io.github.multipak.installer.desktop").read_text()
    )

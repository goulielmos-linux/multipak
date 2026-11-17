import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_xbps import write_xbps

from multipak import guest, updates, void_image
from multipak.metadata import inspect_package
from multipak.models import VOID_ENVIRONMENT, VOID_I686_ENVIRONMENT, VOID_MUSL_ENVIRONMENT
from multipak.process import CommandError

RELEASE = 'NAME="Void"\nID="void"\nPRETTY_NAME="Void Linux"\n'
SCRIPT = Path(updates.__file__).with_name("assets") / "void-setup.sh"


@pytest.fixture
def void_engine(fake_engine, monkeypatch):
    fake_engine.runner.release = RELEASE
    monkeypatch.setattr("multipak.metadata.platform.machine", lambda: "x86_64")
    return fake_engine


def guest_commands(engine):
    return [
        call[call.index("--") + 1 :]
        for call in engine.runner.calls
        if call[:2] == ["distrobox", "enter"]
    ]


def test_installs_through_the_setup_script(void_engine, tmp_path):
    engine = void_engine
    path = write_xbps(tmp_path / "demo-app-1.2.3_4.noarch.xbps", "w:xz")
    tx = engine.install(inspect_package(path, engine.runner), VOID_ENVIRONMENT)
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Void-Linux"
    script = f"/tmp/multipak-{tx.id}/void-setup.sh"
    package = f"/tmp/multipak-{tx.id}/package.xbps"
    commands = guest_commands(engine)
    assert ["xbps-uhelper", "arch"] in commands
    assert [
        "sudo",
        "-n",
        "sh",
        script,
        "--install",
        package,
        "demo-app-1.2.3_4",
        "noarch",
    ] in commands
    assert any("signature was not verified" in warning for warning in tx.warnings)


def environment_architecture(engine, monkeypatch, architecture):
    run = engine.runner.run

    def answer(argv, **kwargs):
        if argv[-2:] == ["xbps-uhelper", "arch"]:
            return architecture + "\n"
        return run(argv, **kwargs)

    monkeypatch.setattr(engine.runner, "run", answer)


def test_refuses_an_environment_of_another_architecture(void_engine, tmp_path, monkeypatch):
    engine = void_engine
    environment_architecture(engine, monkeypatch, "x86_64-musl")
    path = write_xbps(tmp_path / "demo.xbps", "w:xz", architecture="x86_64")
    with pytest.raises(ValueError, match="matching Void Linux environment"):
        engine.install(inspect_package(path, engine.runner), VOID_ENVIRONMENT)
    assert not any("--install" in call for call in guest_commands(engine))


def test_noarch_installs_in_any_variant(void_engine, tmp_path, monkeypatch):
    engine = void_engine
    environment_architecture(engine, monkeypatch, "x86_64-musl")
    path = write_xbps(tmp_path / "demo.xbps", "w:xz")
    tx = engine.install(inspect_package(path, engine.runner), VOID_MUSL_ENVIRONMENT)
    assert tx.status == "installed"


def test_installs_a_musl_package_in_its_own_container(void_engine, tmp_path, monkeypatch):
    engine = void_engine
    environment_architecture(engine, monkeypatch, "x86_64-musl")
    path = write_xbps(tmp_path / "demo.xbps", "w:xz", architecture="x86_64-musl")
    tx = engine.install(inspect_package(path, engine.runner), VOID_MUSL_ENVIRONMENT)
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Void-Linux-musl"
    assert tx.environment_id == "void-musl"
    assert [
        "podman",
        "pull",
        "--policy=missing",
        VOID_MUSL_ENVIRONMENT.image,
    ] in engine.runner.calls


MANIFEST = {
    "manifests": [
        {"digest": "sha256:" + "a" * 64, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "b" * 64, "platform": {"os": "linux", "architecture": "386"}},
    ]
}


def test_32_bit_image_is_pulled_by_digest_and_named_locally(void_engine, tmp_path, monkeypatch):
    engine = void_engine
    environment_architecture(engine, monkeypatch, "i686")
    run = engine.runner.run

    def registry(argv, **kwargs):
        if argv[:3] == ["podman", "image", "exists"]:
            raise CommandError(argv, 1, "")
        if argv[:3] == ["podman", "manifest", "inspect"]:
            return json.dumps(MANIFEST)
        return run(argv, **kwargs)

    monkeypatch.setattr(engine.runner, "run", registry)
    path = write_xbps(tmp_path / "demo.xbps", "w:xz", architecture="i686")
    tx = engine.install(inspect_package(path, engine.runner), VOID_I686_ENVIRONMENT)
    assert tx.status == "installed"
    assert tx.container == "MultiPak-Void-Linux-32-bits"
    reference = "ghcr.io/void-linux/void-glibc-full@sha256:" + "b" * 64
    calls = engine.runner.calls
    assert ["podman", "pull", "--policy=missing", reference] in calls
    assert ["podman", "tag", reference, "localhost/multipak-void:i686"] in calls
    # The 64-bit image's tag is never pulled for it.
    assert ["podman", "pull", "--policy=missing", VOID_ENVIRONMENT.image] not in calls
    create = next(call for call in calls if call[:2] == ["distrobox", "create"])
    assert create[create.index("--image") + 1] == "localhost/multipak-void:i686"


def test_no_32_bit_image_is_reported():
    with pytest.raises(ValueError, match="32-bit"):
        void_image.platform_digest({"manifests": MANIFEST["manifests"][:1]}, "386")


def test_container_update_uses_the_setup_script(store, monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return ""

    monkeypatch.setattr(updates, "read_os_release", lambda *_: {"ID": "void"})
    engine = SimpleNamespace(store=store, runner=SimpleNamespace(run=run), progress=lambda *_: 0)
    updates.update_container(engine, "MultiPak-Void-Linux")
    target = updates.SCRIPTS["void"][1]
    copy = next(call for call in calls if call[:2] == ["podman", "cp"])
    assert copy[2].endswith("void-setup.sh") and copy[3] == f"MultiPak-Void-Linux:{target}"
    assert calls[-1][-5:] == ["sudo", "-n", "sh", target, "--upgrade"]


def fake_tools(tmp_path):
    """xbps-install and xbps-rindex record their arguments instead of running."""
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "log"
    for name in ("xbps-install", "xbps-rindex"):
        tool = tools / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        tool.chmod(0o755)
    return tools, log


def run_script(tmp_path, *args):
    tools, log = fake_tools(tmp_path)
    environment = os.environ | {"PATH": f"{tools}:{os.environ['PATH']}", "SUDO_UID": ""}
    result = subprocess.run(
        ["sh", str(SCRIPT), *args], env=environment, capture_output=True, text=True
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_script_updates_xbps_before_upgrading(tmp_path):
    result, log = run_script(tmp_path, "--upgrade")
    assert result.returncode == 0, result.stderr
    # -y: without a terminal, xbps-install would abort at the confirmation and exit 0.
    assert log == ["xbps-install -Suy xbps", "xbps-install -uy"]


def test_script_installs_from_a_temporary_local_repository(tmp_path):
    package = tmp_path / "work" / "package.xbps"
    package.parent.mkdir()
    package.write_bytes(b"xbps")
    result, log = run_script(tmp_path, "--install", str(package), "demo-app-1.2.3_4", "noarch")
    assert result.returncode == 0, result.stderr
    assert log[0] == "xbps-install -Suy xbps"
    index, installation = log[1].split(), log[2].split()
    assert index[:2] == ["xbps-rindex", "-a"]
    assert index[2].endswith("/demo-app-1.2.3_4.noarch.xbps")
    repository = str(Path(index[2]).parent)
    assert repository.startswith(str(package.parent) + "/repo.")
    assert installation == [
        "xbps-install",
        "-Sy",
        "-R",
        repository,
        "-c",
        f"{repository}/cache",
        "--",
        "demo-app-1.2.3_4",
    ]
    # The temporary repository is removed afterwards.
    assert list(package.parent.iterdir()) == [package]


def test_script_rejects_missing_arguments(tmp_path):
    result, log = run_script(tmp_path, "--install", "only-one")
    assert result.returncode == 2 and not log


def test_collect_ignores_symbolic_link_targets(monkeypatch, tmp_path):
    desktop = tmp_path / "demo.desktop"
    desktop.write_text("[Desktop Entry]\nName=Demo\n")

    def check_output(command, text):
        if command[:3] == ["xbps-query", "-p", "state"]:
            return "installed\n"
        return f"{desktop}\n{tmp_path}/other.desktop -> {desktop}\n"

    monkeypatch.setattr(guest.subprocess, "check_output", check_output)
    result = guest.collect("xbps", "demo-app")
    assert [item["name"] for item in result["desktops"]] == ["demo.desktop"]

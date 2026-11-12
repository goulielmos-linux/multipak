import shutil
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from multipak import host


def test_host_command_preserves_literal_arguments_and_only_explicit_environment(monkeypatch):
    monkeypatch.setattr(host, "is_flatpak", lambda: True)
    argv = ["podman", "cp", "/a file; $(never execute)", "container:/tmp/package"]
    command = host.command(argv, environment={"LC_ALL": "C"})
    assert command[command.index("--") + 1 :] == argv
    assert "--watch-bus" in command
    assert "--env=LC_ALL=C" in command
    assert not any("PATH=" in item for item in command)
    assert "--watch-bus" not in host.command(argv, watch_bus=False)


def test_flatpak_reads_host_xdg_directories_instead_of_private_storage(monkeypatch):
    monkeypatch.setattr(host, "is_flatpak", lambda: True)
    monkeypatch.setenv("XDG_DATA_HOME", "/private-flatpak-data")
    monkeypatch.setattr(
        host,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "/host-data\n/host-config\n", ""),
    )
    host._flatpak_directories.cache_clear()
    try:
        assert host.directories() == (Path("/host-data"), Path("/host-config"))
    finally:
        host._flatpak_directories.cache_clear()


def test_native_directories_follow_the_current_environment(monkeypatch):
    monkeypatch.setattr(host, "is_flatpak", lambda: False)
    for directory in ("/first-data", "/second-data"):
        monkeypatch.setenv("XDG_DATA_HOME", directory)
        assert host.directories()[0] == Path(directory)


@pytest.mark.parametrize("failure", [False, True])
def test_sandbox_resources_are_staged_and_removed_even_on_failure(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(host, "is_flatpak", lambda: True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    resource = tmp_path / "guest.py"
    resource.write_text("print('fixture')")
    with pytest.raises(RuntimeError) if failure else nullcontext():
        with host.accessible_file(resource) as visible:
            assert visible != resource
            assert visible.read_text() == resource.read_text()
            assert visible.is_relative_to(tmp_path / "cache")
            if failure:
                raise RuntimeError("fixture failure")
    assert not visible.exists()
    assert resource.is_file()


@pytest.mark.skipif(not shutil.which("setsid"), reason="Linux util-linux is required")
def test_host_supervisor_preserves_command_failure():
    result = subprocess.run(
        [
            "setsid",
            "--fork",
            "--wait",
            "sh",
            "-c",
            host._SUPERVISOR,
            "multipak-host",
            "sh",
            "-c",
            "printf 'fixture failure'; exit 7",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 7
    assert result.stdout == "fixture failure"


@pytest.mark.skipif(not shutil.which("setsid"), reason="Linux util-linux is required")
def test_host_supervisor_removes_children_when_launcher_dies(tmp_path):
    marker = tmp_path / "child.pid"
    fixture = (
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    with subprocess.Popen(
        [
            "setsid",
            "--fork",
            "--wait",
            "sh",
            "-c",
            host._SUPERVISOR,
            "multipak-host",
            sys.executable,
            "-c",
            fixture,
            str(marker),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        pid = int(marker.read_text())
        process.terminate()
        process.communicate(timeout=3)
    path = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 3
    while True:
        try:
            state = path.read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            return
        if state == "Z":
            return
        assert time.monotonic() < deadline, f"Child process {pid} did not exit (state {state})"
        time.sleep(0.02)

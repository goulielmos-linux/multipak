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


def _shells():
    return [name for name in ("sh", "dash", "bash") if shutil.which(name)]


class _Spawned:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def wait(self, timeout=5):
        deadline = time.monotonic() + timeout
        while self.returncode is None:
            import os

            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
                break
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.02)
        return self.returncode

    def kill(self):
        import os
        import signal

        if self.returncode is None:
            os.kill(self.pid, signal.SIGKILL)


def _supervised(shell, argv, lifeline=None, value=None):
    """The supervisor as flatpak-spawn starts it on the computer: the lifeline's
    read end as 4, its number in MULTIPAK_LIFELINE."""
    import os

    environment = dict(os.environ)
    environment.pop(host.LIFELINE_VARIABLE, None)
    actions = []
    if lifeline is not None:
        actions.append((os.POSIX_SPAWN_DUP2, lifeline, 4))
        environment[host.LIFELINE_VARIABLE] = "4"
    if value is not None:
        environment[host.LIFELINE_VARIABLE] = value
    command = ["setsid", "--fork", "--wait", shell, "-c", host._SUPERVISOR, "multipak-host"]
    command += argv
    return _Spawned(os.posix_spawnp(command[0], command, environment, file_actions=actions))


def _ended(pid, seconds=3):
    path = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if path.read_text().split()[2] == "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(not shutil.which("setsid"), reason="Linux util-linux is required")
@pytest.mark.parametrize("shell", _shells())
def test_the_lifeline_ends_the_command_when_the_launcher_is_killed(tmp_path, shell):
    # From a Flatpak, $PPID is setsid and flatpak-spawn outlives a launcher killed
    # with SIGKILL: only the lifeline tells that the launcher is gone.
    import os

    marker = tmp_path / "child.pid"
    fixture = (
        "import os,signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    read, write = os.pipe()
    try:
        process = _supervised(shell, [sys.executable, "-c", fixture, str(marker)], read)
    finally:
        os.close(read)
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        pid = int(marker.read_text())
        time.sleep(0.3)
        assert not _ended(pid, 0.1), "the command ended while the launcher lived"
        os.close(write)  # What the launcher's death does to its end.
        write = None
        assert _ended(pid), f"child {pid} outlived the launcher"
        process.wait(3)
    finally:
        if write is not None:
            os.close(write)
        process.kill()
        process.wait()


@pytest.mark.skipif(not shutil.which("setsid"), reason="Linux util-linux is required")
@pytest.mark.parametrize("shell", _shells())
def test_a_lifeline_kept_open_or_missing_does_not_end_the_command(shell):
    import os

    read, write = os.pipe()
    try:
        # The command does not inherit the lifeline: only the supervisor watches it.
        check = "sleep 0.4; [ -e /proc/self/fd/4 ] && exit 9; exit 7"
        process = _supervised(shell, ["sh", "-c", check], read)
        os.close(read)
        assert process.wait() == 7
    finally:
        os.close(write)
    # Missing, not a number, or one dash could not redirect: no lifeline, no harm.
    for value in ("9", "12", "not-a-number"):
        process = _supervised(shell, ["sh", "-c", "sleep 0.3; exit 7"], value=value)
        assert process.wait() == 7


def test_supervised_commands_forward_the_lifeline(monkeypatch):
    monkeypatch.setattr(host, "is_flatpak", lambda: True)
    command = host.command(["bwrap"], supervise=True, forward_fds=(5,), lifeline=6)
    prefix = command[: command.index("--")]
    assert {"--forward-fd=5", "--forward-fd=6", f"--env={host.LIFELINE_VARIABLE}=6"} <= set(prefix)
    for unsupervised in (
        host.command(["bwrap"], lifeline=6),
        host.command(["bwrap"], supervise=True),
    ):
        assert not any(item.startswith(f"--env={host.LIFELINE_VARIABLE}") for item in unsupervised)
        assert "--forward-fd=6" not in unsupervised
    with pytest.raises(ValueError, match="3 to 9"):
        host.command(["bwrap"], supervise=True, lifeline=10)
    monkeypatch.setattr(host, "is_flatpak", lambda: False)
    assert host.command(["bwrap"], supervise=True, lifeline=6) == ["bwrap"]


def test_the_lifeline_write_end_stays_in_this_process(monkeypatch):
    import os

    monkeypatch.setattr(host, "_lifeline", None)
    first = host.lifeline()
    assert host.lifeline() == first
    _read, write, owner = host._lifeline
    assert owner == os.getpid()
    assert not os.get_inheritable(first) and not os.get_inheritable(write)
    # A forked copy makes its own: the parent's end would not tell its death.
    monkeypatch.setattr(os, "getpid", lambda: owner + 1)
    assert host.lifeline() != first
    for item in (first, write, *host._lifeline[:2]):
        os.close(item)
    monkeypatch.setattr(host, "_lifeline", None)


@pytest.mark.skipif(not shutil.which("setsid"), reason="Linux util-linux is required")
@pytest.mark.parametrize("shell", _shells())
def test_a_supervised_command_reads_the_launchers_input(shell):
    # A snap's command-line app under Flatpak, such as echo '{}' | jq .
    result = subprocess.run(
        ["setsid", "--fork", "--wait", shell, "-c", host._SUPERVISOR, "multipak-host", "cat"],
        input="from the pipe\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout == "from the pipe\n"

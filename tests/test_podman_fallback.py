import pytest

from multipak.environments import copy_into, read_os_release
from multipak.process import CommandError

RELEASE = 'ID=ubuntu\nVERSION_ID="26.04"\n'
LOST_USER = 'determining run uid: user: unknown user error looking up user "root"'


class Runner:
    """podman cp fails as on a long-running container whose users podman lost."""

    def __init__(self, exec_works=True):
        self.calls, self.exec_works = [], exec_works

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[1] == "cp":
            raise CommandError(argv, 125, LOST_USER)
        if not self.exec_works:
            raise CommandError(argv, 125, "container is not running")
        return RELEASE if argv[-2:] == ["--", "/usr/lib/os-release"] else ""


def test_os_release_is_read_as_uid_0_when_podman_cp_fails():
    runner = Runner()
    assert read_os_release(runner, "box") == {"ID": "ubuntu", "VERSION_ID": "26.04"}
    assert runner.calls[-1] == [
        "podman",
        "exec",
        "--user",
        "0:0",
        "box",
        "cat",
        "--",
        "/usr/lib/os-release",
    ]


def test_copy_into_falls_back_to_cp_from_run_host(tmp_path):
    source = tmp_path / "script.sh"
    source.write_text("true")
    runner = Runner()
    copy_into(runner, "box", source, "/usr/libexec/script.sh")
    assert runner.calls[-1] == [
        "podman",
        "exec",
        "--user",
        "0:0",
        "box",
        "cp",
        "--",
        f"/run/host{source.resolve()}",
        "/usr/libexec/script.sh",
    ]


def test_copy_into_keeps_podman_cps_error_when_the_fallback_fails(tmp_path):
    with pytest.raises(CommandError) as raised:
        copy_into(Runner(exec_works=False), "box", tmp_path / "x", "/tmp/x")
    assert LOST_USER in str(raised.value.output)

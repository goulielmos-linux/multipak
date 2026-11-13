"""Exercise the Android-side removal contract with disposable command fixtures."""

import os
import subprocess

import pytest

from multipak.android import REMOVE_ANDROID


@pytest.mark.parametrize("mode", ["installed", "absent", "query_failure", "remove_failure"])
def test_removal_confirms_actual_packages_and_preserves_data(tmp_path, mode):
    commands = tmp_path / "bin"
    commands.mkdir()
    state, log, data = (tmp_path / name for name in ("installed", "calls", "data"))
    if mode != "absent":
        state.touch()
    data.write_text("personal data")
    fixtures = {
        "cmd": (
            "#!/bin/sh\n"
            '[ "$PM_MODE" != query_failure ] || exit 1\n'
            "echo package:android\n"
            'if [ -f "$PM_STATE" ]; then echo package:org.example.demo; fi\n'
        ),
        "pm": (
            "#!/bin/sh\n"
            '[ "$1" = uninstall ] && [ "$2" = -k ] || exit 1\n'
            'echo "$*" >> "$PM_LOG"\n'
            '[ "$PM_MODE" != remove_failure ] || exit 1\n'
            'rm "$PM_STATE"\n'
        ),
    }
    for name, text in fixtures.items():
        command = commands / name
        command.write_text(text)
        command.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", REMOVE_ANDROID, "multipak-remove", "org.example.demo"],
        env=os.environ
        | {
            "PATH": str(commands) + ":/usr/bin:/bin",
            "PM_STATE": str(state),
            "PM_LOG": str(log),
            "PM_MODE": mode,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    success = mode in ("installed", "absent")
    assert (result.returncode == 0) == success
    assert ("MULTIPAK_ANDROID_PACKAGE_REMOVED" in result.stdout.splitlines()) == success
    assert data.read_text() == "personal data"
    if mode == "absent":
        assert not log.exists()

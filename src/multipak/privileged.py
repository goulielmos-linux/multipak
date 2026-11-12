"""Invoke only the installed privileged entry point after explicit bootstrap."""

import json
from pathlib import Path

from . import host

HELPER = "/usr/local/libexec/multipak-system/manage"
MANIFEST = "/usr/local/libexec/multipak-system/manifest.json"


def ensure_installed(runner):
    expected = Path(__file__).with_name("assets").joinpath("privileged.sha256").read_text().strip()
    try:
        current = json.loads(runner.run(["cat", MANIFEST], timeout=10))
    except (OSError, RuntimeError, ValueError, TimeoutError):
        current = {}
    if current.get("bundle_sha256") == expected:
        return
    # Installing/updating trusted application code is the only bootstrap step.
    # Subsequent operational requests never run a Python file from the user's home.
    # The installer's code is passed on the command line, not as a path, so it is fixed
    # when pkexec starts and cannot be replaced while the password prompt is open.
    # The installer itself verifies the bundle against its embedded hash.
    source = Path(__file__).with_name("bootstrap_helpers.py")
    code = source.read_text(encoding="utf-8")
    archive = source.with_name("assets") / "privileged.tar.gz"
    with host.accessible_file(archive) as payload:
        output = runner.run(
            ["pkexec", "/usr/bin/python3", "-I", "-u", "-c", code, str(payload)], timeout=180
        )
    if "MULTIPAK_PRIVILEGED_HELPERS_READY" not in output.splitlines():
        raise RuntimeError("The privileged component installation was not confirmed")


def call(runner, subsystem, *arguments, **options):
    ensure_installed(runner)
    return runner.run(["pkexec", HELPER, subsystem, *map(str, arguments)], **options)

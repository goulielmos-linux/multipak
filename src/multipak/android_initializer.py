"""Standalone privileged supervisor for Waydroid initialization (stdlib only)."""

import configparser
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

SYSTEM_CHANNEL = "https://ota.waydro.id/system"
VENDOR_CHANNEL = "https://ota.waydro.id/vendor"


def initialization_arguments(
    arguments, channels_file=Path("/usr/share/waydroid-extra/channels.cfg")
):
    """Resolve configured channels before the first attempt; fill empty defaults."""
    config = configparser.ConfigParser(interpolation=None)
    if channels_file.is_file():
        config.read(channels_file, encoding="utf-8")
    result = list(arguments)
    for flag, key, fallback in (
        ("-c", "system_channel", SYSTEM_CHANNEL),
        ("-v", "vendor_channel", VENDOR_CHANNEL),
        ("-r", "rom_type", "lineage"),
    ):
        if flag not in result:
            value = config.get("channels", key, fallback="").strip() or fallback
            result.extend((flag, value))
    return result


if __package__:
    from .privileged_files import CLEAN_ENV, cancellation_watch
else:
    from privileged_files import CLEAN_ENV, cancellation_watch


def supervise(command, watch_file):
    """Stop the privileged download when its caller cancels or disappears."""
    parent = os.getppid()
    import pwd

    owner = pwd.getpwuid(int(os.environ["PKEXEC_UID"]))
    with (
        cancellation_watch(owner, watch_file) as cancelled,
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, start_new_session=True, cwd="/", env=CLEAN_ENV
        ) as child,
    ):
        try:
            while child.poll() is None:
                if cancelled() or os.getppid() != parent:
                    return 130
                time.sleep(0.1)
            return child.returncode
        except KeyboardInterrupt:
            return 130
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                if child.poll() is not None:
                    break
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    continue


def main():
    if os.geteuid() != 0 or not os.environ.get("PKEXEC_UID", "").isdigit():
        raise PermissionError("Administrative authorization is required")
    watch_file, *arguments = sys.argv[1:]
    if arguments != ["init", "-s", "VANILLA"]:
        raise ValueError("Only the standard Android initialization is supported")
    if not arguments or arguments[0] != "init" or len(arguments) % 2 != 1:
        raise SystemExit("Only Waydroid initialization is supported.")
    for index in range(1, len(arguments), 2):
        if arguments[index] not in ("-s", "-c", "-v", "-r") or arguments[index + 1].startswith("-"):
            raise SystemExit("Invalid Waydroid initialization option.")
    # -u flushes every progress update, including carriage-return-only output.
    # -E/-s ignore user Python configuration while retaining Waydroid's script directory.
    result = supervise(
        [
            "/usr/bin/python3",
            "-E",
            "-s",
            "-u",
            "/usr/bin/waydroid",
            *arguments,
            "-c",
            SYSTEM_CHANNEL,
            "-v",
            VENDOR_CHANNEL,
            "-r",
            "lineage",
        ],
        watch_file,
    )
    if result == 0 and (restorecon := shutil.which("restorecon", path=CLEAN_ENV["PATH"])):
        subprocess.run(
            [restorecon, "-R", "/var/lib/waydroid"],
            env=CLEAN_ENV,
            cwd="/",
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=120,
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())

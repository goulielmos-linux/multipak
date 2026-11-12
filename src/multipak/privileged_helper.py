#!/usr/bin/python3 -I
"""Installed entry point. Its complete directory is writable only by root."""

import importlib
import os
import pwd
import sys
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def caller():
    value = os.environ.get("PKEXEC_UID", "")
    if os.geteuid() != 0 or not value.isdigit() or int(value) == 0:
        raise PermissionError("An authenticated regular user must request this action")
    return pwd.getpwuid(int(value))


def main():
    # -I removes user Python paths; add only our root-owned installation.
    if DIRECTORY.stat().st_uid != 0 or DIRECTORY.stat().st_mode & 0o022:
        raise PermissionError("The privileged code directory is not protected")
    sys.path.insert(0, str(DIRECTORY))
    caller()

    os.chdir("/")
    os.umask(0o022)
    subsystem, *arguments = sys.argv[1:]
    if subsystem == "system-install" and len(arguments) == 2:
        sys.argv = [str(DIRECTORY / "system_install_helper.py"), *arguments]
        return importlib.import_module("system_install_helper").main()
    raise ValueError("Unsupported privileged operation")


if __name__ == "__main__":
    raise SystemExit(main())

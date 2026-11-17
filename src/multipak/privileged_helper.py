#!/usr/bin/python3 -I
"""Installed entry point. Its complete directory is writable only by root."""

import importlib
import os
import pwd
import subprocess
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
    owner = caller()
    from privileged_files import CLEAN_ENV

    os.chdir("/")
    os.umask(0o022)
    subsystem, *arguments = sys.argv[1:]
    if subsystem == "system-install" and len(arguments) == 2:
        sys.argv = [str(DIRECTORY / "system_install_helper.py"), *arguments]
        return importlib.import_module("system_install_helper").main()
    modules = {
        "android": "android_instances_helper",
        "android-upgrade": "android_upgrade_helper",
        "arm": "android_translation_helper",
        "initialize": "android_initializer",
    }
    if subsystem in modules:
        if subsystem == "android" and arguments == ["deploy"]:
            arguments.append(str(DIRECTORY / "assets/waydroid-runtime.tar.gz"))
        elif subsystem == "android" and arguments and arguments[0] == "deploy":
            raise ValueError("Only the installed Android runtime can be deployed")
        sys.argv = [str(DIRECTORY / (modules[subsystem] + ".py")), *arguments]
        return importlib.import_module(modules[subsystem]).main()
    if subsystem in ("service", "waydroid") and len(arguments) >= 2:
        identifier, *command = arguments
        if identifier == "personal":
            executable = "/usr/bin/waydroid"
            prefix = []
        else:
            from android_instances_helper import metadata

            metadata(identifier, owner)
            executable = "/usr/local/bin/multipak-waydroid"
            prefix = ["--instance", identifier]
        if subsystem == "service":
            if len(command) != 1 or command[0] not in ("start", "stop"):
                raise ValueError("Unsupported Android service action")
            argv = [
                "/usr/bin/systemctl",
                command[0],
                (
                    "waydroid-container.service"
                    if identifier == "personal"
                    else "multipak-waydroid@" + identifier + ".service"
                ),
            ]
        else:
            if command[:2] != ["shell", "--"]:
                raise ValueError("Only an Android shell operation is supported")
            argv = [executable, *prefix, *command]
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, env=CLEAN_ENV, cwd="/", check=False)
        return result.returncode
    raise ValueError("Unsupported privileged operation")


if __name__ == "__main__":
    raise SystemExit(main())

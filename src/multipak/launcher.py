"""Launch the MultiPak installer and manager with system Qt."""

import os
import sys
from pathlib import Path

from . import host


def _launch(arguments):
    bootstrap = Path(__file__).with_name("kirigami") / "bootstrap.py"
    environment = dict(os.environ)
    keys = (
        ("PYTHONHOME", "VIRTUAL_ENV")
        if host.is_flatpak()
        else ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")
    )
    for key in keys:
        environment.pop(key, None)
    python = sys.executable if host.is_flatpak() else "/usr/bin/python3"
    os.execve(python, [python, str(bootstrap), *arguments], environment)


def main():
    _launch(sys.argv[1:])


def manager_main():
    _launch(["--manager", *sys.argv[1:]])


def android_settings_main():
    _launch(["--android-settings", *sys.argv[1:]])


if __name__ == "__main__":
    main()

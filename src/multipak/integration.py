"""Register file activation explicitly; preserve unrelated desktop associations."""

import argparse
import configparser
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from multipak.localization import tr

from . import host

DESKTOP_ID = host.installer_desktop_id() + ".desktop"
MIME_TYPES = (
    "application/x-rpm",
    "application/vnd.debian.binary-package",
    "application/x-deb",
    "application/x-archlinux-package",
    "application/vnd.appimage",
    "application/x-iso9660-appimage",
    "application/x-appimage",
    "application/x-multipak-appimage",
)


def register_user_defaults() -> None:
    executable = shutil.which("xdg-mime")
    if not executable:
        raise RuntimeError(
            tr("Install xdg-utils to associate RPM, DEB, Arch and AppImage packages.")
        )
    # Some xdg-utils releases still call the Qt 5 name on Plasma 6. Supply the
    # installed Qt helper under that name only for these subprocesses.
    qt_helper = None
    if not shutil.which("qtpaths"):
        qt_helper = shutil.which("qtpaths6") or shutil.which("qtpaths-qt6")
    compatibility = (
        TemporaryDirectory(prefix="multipak-qtpaths-") if qt_helper else nullcontext(None)
    )
    with compatibility as directory:
        environment = None
        if directory is not None:
            (Path(directory) / "qtpaths").symlink_to(Path(qt_helper).resolve())
            environment = os.environ | {
                "PATH": directory + os.pathsep + os.environ.get("PATH", os.defpath)
            }
        for mime_type in MIME_TYPES:
            subprocess.run(
                [executable, "default", DESKTOP_ID, mime_type], check=True, env=environment
            )


def merge_system_defaults(path: Path) -> None:
    """For image builds only: update package mappings without dropping existing ones."""
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if path.exists():
        config.read(path, encoding="utf-8")
    if not config.has_section("Default Applications"):
        config.add_section("Default Applications")
    for mime_type in MIME_TYPES:
        config["Default Applications"][mime_type] = DESKTOP_ID + ";"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        config.write(stream, space_around_delimiters=False)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-file", type=Path)
    args = parser.parse_args()
    if args.system_file:
        merge_system_defaults(args.system_file)
    else:
        from .file_associations import FORMATS, FileAssociations

        settings = FileAssociations()
        selected = (
            settings.load() if settings.state_path.exists() else {key: True for key in FORMATS}
        )
        settings.apply(selected)


if __name__ == "__main__":
    main()

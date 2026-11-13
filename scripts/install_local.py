#!/usr/bin/env python3
"""Install in a private venv, without layering packages or invoking a shell."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if os.geteuid() == 0:
        raise SystemExit("Run as a regular user, without sudo.")
    project = Path(__file__).resolve().parents[1]
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    runtime = data / "multipak-runtime"
    command = runtime / "bin/multipak-installer"
    multipak_command = runtime / "bin/multipak-installer"
    multipak_launcher = Path.home() / ".local/bin/multipak-installer"
    manager_launcher = Path.home() / ".local/bin/multipak-manager"
    manager_command = runtime / "bin/multipak-manager"
    launchers = (
        (multipak_launcher, multipak_command),
        (manager_launcher, manager_command),
    )
    for path, target in launchers:
        if path.exists() or path.is_symlink():
            if not path.is_symlink() or path.readlink() != target:
                raise SystemExit(f"{path} already exists and does not belong to this installation.")
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(runtime)], check=True
    )
    try:
        subprocess.run(
            [
                str(runtime / "bin/python"),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--only-binary=:all:",
                str(project),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "Application installation did not complete. See the error above. "
            "File associations were not changed during this attempt."
        ) from exc
    multipak_launcher.parent.mkdir(parents=True, exist_ok=True)
    for path, target in launchers:
        if not path.is_symlink():
            path.symlink_to(target)
    # Import only after dependencies have been installed into the private runtime.
    subprocess.run(
        [
            str(runtime / "bin/python"),
            str(project / "scripts/register_desktop.py"),
            str(command),
            str(data),
            str(project),
        ],
        check=True,
    )
    if updater := shutil.which("update-desktop-database"):
        subprocess.run([updater, str(data / "applications")], check=True)
    if mime_updater := shutil.which("update-mime-database"):
        subprocess.run([mime_updater, str(data / "mime")], check=True)
    if kde_cache := shutil.which("kbuildsycoca6"):
        subprocess.run([kde_cache, "--noincremental"], check=True)
    subprocess.run(
        [str(runtime / "bin/python"), "-m", "multipak.integration"],
        check=True,
    )
    subprocess.run([str(runtime / "bin/python"), "-m", "multipak.android_menu"], check=True)
    if kde_cache := shutil.which("kbuildsycoca6"):
        subprocess.run([kde_cache, "--noincremental"], check=True)
    print("Installation completed. Open an RPM, DEB, Arch, AppImage or Android APK to begin.")


if __name__ == "__main__":
    main()

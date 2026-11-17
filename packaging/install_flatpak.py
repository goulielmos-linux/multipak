#!/usr/bin/env python3
"""Install the application and desktop exports into the Flatpak build tree."""

import json
import re
import shutil
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = Path("/app")
APP_ID = "io.github.goulielmos_linux.multipak"
INSTALLER_ID = APP_ID + ".installer"


def install(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def bundled_versions():
    """Versions of the libraries the manifest builds, shown in the About window."""
    manifest = json.loads((ROOT / "packaging" / f"{APP_ID}.json").read_text(encoding="utf-8"))
    found = {}

    def walk(modules):
        for module in modules:
            if not isinstance(module, dict):
                continue
            for source in module.get("sources", []):
                url = source.get("url", "") if isinstance(source, dict) else ""
                version = re.search(r"[v-](\d+(?:\.\d+)+)\.tar\.", url)
                if version:
                    found.setdefault(module["name"], version[1])
            walk(module.get("modules", []))

    walk(manifest.get("modules", []))
    return found


def main():
    packages = Path(sysconfig.get_path("purelib", vars={"base": str(PREFIX)}))
    shutil.copytree(
        ROOT / "src/multipak",
        packages / "multipak",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for name, module, function in (
        ("multipak-manager", "launcher", "manager_main"),
        ("multipak-installer", "launcher", "main"),
        ("multipak-appimage", "appimage_launcher", "main"),
        ("multipak-snap", "snap_launcher", "main"),
    ):
        target = PREFIX / "bin" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"#!/usr/bin/python3\nfrom multipak.{module} import {function}\n"
            f"raise SystemExit({function}())\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
    for role, desktop_id in (("manager", APP_ID), ("installer", INSTALLER_ID)):
        text = (ROOT / "data" / f"io.github.multipak.{role}.desktop").read_text()
        text = text.replace(f"io.github.multipak.{role}", desktop_id)
        icon_id = f"{APP_ID}.{role}"
        text = text.replace(f"Icon={desktop_id}\n", f"Icon={icon_id}\n")
        target = PREFIX / "share/applications" / f"{desktop_id}.desktop"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        for extension, size in (("svg", "scalable"), ("png", "256x256")):
            install(
                ROOT / "src/multipak/kirigami/assets" / f"multipak-{role}.{extension}",
                PREFIX / "share/icons/hicolor" / size / "apps" / f"{icon_id}.{extension}",
            )
    for extension, size in (("svg", "scalable"), ("png", "256x256")):
        asset = "multipak.svg" if extension == "svg" else "multipak-installer.png"
        install(
            ROOT / "src/multipak/kirigami/assets" / asset,
            PREFIX / "share/icons/hicolor" / size / "apps" / f"{APP_ID}.{extension}",
        )
    install(
        ROOT / "src/multipak/assets/multipak-packages.xml",
        PREFIX / "share/mime/packages" / f"{APP_ID}.packages.xml",
    )
    install(
        ROOT / "src/multipak/assets/multipak-android.xml",
        PREFIX / "share/mime/packages" / f"{APP_ID}.android.xml",
    )
    install(
        ROOT / "src/multipak/assets/multipak-snap.xml",
        PREFIX / "share/mime/packages" / f"{APP_ID}.snap.xml",
    )
    install(
        ROOT / "packaging" / f"{APP_ID}.metainfo.xml",
        PREFIX / "share/metainfo" / f"{APP_ID}.metainfo.xml",
    )
    versions = PREFIX / "share/multipak/bundled-versions.json"
    versions.parent.mkdir(parents=True, exist_ok=True)
    versions.write_text(json.dumps(bundled_versions(), indent=2) + "\n", encoding="utf-8")
    for name in ("LICENSE", "NOTICE"):
        install(ROOT / name, PREFIX / "share/licenses" / APP_ID / name)


if __name__ == "__main__":
    main()

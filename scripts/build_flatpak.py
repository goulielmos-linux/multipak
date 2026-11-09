#!/usr/bin/env python3
"""Build a local Flatpak repository and an installable bundle."""

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.multipak.manager"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/flatpak")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if executable := shutil.which("flatpak-builder"):
        builder = [executable]
    else:
        builder = ["flatpak", "run", "org.flatpak.Builder"]
    subprocess.run(
        [
            *builder,
            "--user",
            "--force-clean",
            "--disable-rofiles-fuse",
            f"--state-dir={output / 'state'}",
            f"--repo={output / 'repo'}",
            str(output / "build"),
            str(ROOT / "packaging" / f"{APP_ID}.json"),
        ],
        cwd=ROOT,
        check=True,
    )
    architecture = subprocess.check_output(["flatpak", "--default-arch"], text=True).strip()
    version = re.search(
        r'^__version__ = "([^"]+)"', (ROOT / "src/multipak/__init__.py").read_text(), re.M
    ).group(1)
    bundle = output / f"multipak-{version}-{architecture}.flatpak"
    subprocess.run(
        [
            "flatpak",
            "build-bundle",
            str(output / "repo"),
            str(bundle),
            APP_ID,
            "stable",
            "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo",
        ],
        check=True,
    )
    print(f"Created {bundle}")


if __name__ == "__main__":
    main()

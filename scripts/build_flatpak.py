#!/usr/bin/env python3
"""Build a local Flatpak repository and an installable bundle."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.goulielmos_linux.multipak"


def version():
    return re.search(
        r'^__version__ = "([^\"]+)"', (ROOT / "src/multipak/__init__.py").read_text(), re.M
    ).group(1)


def signing_options(key, home):
    if not re.fullmatch(r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})", key):
        raise ValueError("Use the complete GPG fingerprint")
    return [f"--gpg-sign={key}", f"--gpg-homedir={home}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/flatpak")
    parser.add_argument(
        "--local-source", action="store_true", help="Build the current working tree"
    )
    parser.add_argument("--gpg-key", help="Full fingerprint of the release signing key")
    parser.add_argument(
        "--gpg-homedir",
        type=Path,
        default=Path(os.environ.get("GNUPGHOME", Path.home() / ".gnupg")),
    )
    parser.add_argument("--repo-url", help="HTTPS URL of the published Flatpak repository")
    parser.add_argument(
        "--expected-tag", help="Require this release tag to match the source version"
    )
    args = parser.parse_args()
    release_version = version()
    if args.expected_tag and args.expected_tag != "v" + release_version:
        parser.error("Release tag does not match the source version")
    if bool(args.gpg_key) != bool(args.repo_url):
        parser.error("Publishing requires both --gpg-key and --repo-url")
    signing = signing_options(args.gpg_key, args.gpg_homedir) if args.gpg_key else []
    if args.repo_url:
        from publish_flatpak import validate_url

        validate_url(args.repo_url)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = ROOT / "packaging" / f"{APP_ID}.json"
    if signing:
        public_key = output / "multipak.gpg"
        public_key.write_bytes(
            subprocess.check_output(
                ["gpg", "--homedir", str(args.gpg_homedir), "--batch", "--export", args.gpg_key]
            )
        )
        if not public_key.stat().st_size:
            raise ValueError("The signing public key could not be exported")
        (ROOT / "src/multipak/assets/update-signing.gpg").write_bytes(public_key.read_bytes())
    if args.local_source:
        content = json.loads(manifest.read_text())
        module = next(item for item in content["modules"] if item["name"] == "multipak")
        module["sources"] = [
            {
                "type": "dir",
                "path": str(ROOT),
                "skip": [
                    ".git",
                    ".venv",
                    ".pytest_cache",
                    ".ruff_cache",
                    "build",
                    "dist",
                    ".flatpak-builder",
                ],
            }
        ]
        manifest = output / f"{APP_ID}.local.json"
        manifest.write_text(json.dumps(content, indent=2) + "\n")
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
            *signing,
            f"--state-dir={output / 'state'}",
            f"--repo={output / 'repo'}",
            str(output / "build"),
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
    )
    architecture = subprocess.check_output(["flatpak", "--default-arch"], text=True).strip()
    bundle = output / f"multipak-{release_version}-{architecture}.flatpak"
    publication = []
    if signing:
        subprocess.run(
            [
                "flatpak",
                "build-update-repo",
                *signing,
                "--generate-static-deltas",
                "--title=MultiPak",
                str(output / "repo"),
            ],
            check=True,
        )
        publication = [f"--repo-url={args.repo_url}", f"--gpg-keys={public_key}"]
    subprocess.run(
        [
            "flatpak",
            "build-bundle",
            *publication,
            str(output / "repo"),
            str(bundle),
            APP_ID,
            "stable",
            "--runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo",
        ],
        check=True,
    )
    bundle.with_suffix(".flatpak.sha256").write_text(
        hashlib.sha256(bundle.read_bytes()).hexdigest() + "  " + bundle.name + "\n"
    )
    print(f"Created {bundle}")


if __name__ == "__main__":
    main()

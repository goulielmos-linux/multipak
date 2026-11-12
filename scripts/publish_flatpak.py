"""Prepare signed Flatpak repository files for GitHub Pages, without uploading them."""

import argparse
import base64
import configparser
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.goulielmos_linux.multipak"
RUNTIME_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def validate_url(value):
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or any(c.isspace() for c in value)
        or ".." in url.path.split("/")
    ):
        raise ValueError("A fixed HTTPS repository URL is required")
    return value.rstrip("/") + "/"


def descriptors(repo_url, public_key):
    repo_url = validate_url(repo_url)
    if not public_key or len(public_key) > 1024 * 1024:
        raise ValueError("A public signing key is required")
    with tempfile.TemporaryDirectory(prefix="multipak-public-key-") as home:
        checked = subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--list-packets"],
            input=public_key,
            capture_output=True,
            check=False,
            timeout=15,
        )
    packets = checked.stdout.decode("utf-8", errors="replace")
    if checked.returncode or ":public key packet:" not in packets or ":secret" in packets:
        raise ValueError("Only a valid public signing key may be published")
    key = base64.b64encode(public_key).decode("ascii")
    common = f"Title=MultiPak\nUrl={repo_url}\nGPGKey={key}\n"
    return {
        "multipak.flatpakref": "[Flatpak Ref]\n"
        + common
        + f"Name={APP_ID}\nBranch=stable\nSuggestRemoteName=multipak\n"
        f"IsRuntime=false\nRuntimeRepo={RUNTIME_REPO}\n",
        "multipak.flatpakrepo": "[Flatpak Repo]\n"
        + common
        + "Homepage=https://github.com/goulielmos-linux/multipak\n",
    }


def prepare(repository, output, repo_url, public_key, version):
    repo_url = validate_url(repo_url)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Only stable releases can be published to the stable repository")
    repository, output = repository.resolve(), output.resolve()
    if (
        output == repository
        or output.is_relative_to(repository)
        or repository.is_relative_to(output)
    ):
        raise ValueError("The publication directory must be separate from the repository")
    config = configparser.ConfigParser()
    config.read(repository / "config")
    if config.get("core", "mode", fallback="") != "archive-z2":
        raise ValueError("An archive-z2 Flatpak repository is required")
    for name in ("summary", "summary.sig"):
        if not (repository / name).is_file() or not (repository / name).stat().st_size:
            raise ValueError("The repository summary must be signed before publication")
    if any(path.is_symlink() for path in repository.rglob("*")):
        raise ValueError("The Pages repository must not contain symbolic links")
    files = descriptors(repo_url, public_key)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(repository, output / "repo", ignore=shutil.ignore_patterns(".lock", "tmp"))
    for name, content in files.items():
        (output / name).write_text(content)
    (output / "multipak.gpg").write_bytes(public_key)
    (output / ".nojekyll").touch()
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>MultiPak</title><h1>MultiPak " + html.escape(version) + "</h1>"
        '<p><a href="multipak.flatpakref">Install MultiPak</a></p>'
        "<p>Open this file with your software store. Future updates follow your "
        'system update preferences.</p><p><a href="multipak.flatpakrepo">'
        "Add the update repository</a></p>"
        '<p><a href="https://github.com/goulielmos-linux/multipak/releases">'
        "Release downloads</a></p></html>\n"
    )


def write_policy(output, version, minimum, key, home):
    if not re.fullmatch(r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})", key):
        raise ValueError("Use the complete GPG fingerprint")
    versions = (version, minimum)
    if any(not re.fullmatch(r"\d{1,8}\.\d{1,8}\.\d{1,8}", item) for item in versions):
        raise ValueError("Invalid release policy version")
    if tuple(map(int, minimum.split("."))) > tuple(map(int, version.split("."))):
        raise ValueError("Minimum supported version exceeds the published release")
    policy = output / "update-policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema": 1,
                "app_id": APP_ID,
                "latest_version": version,
                "minimum_supported_version": minimum,
            },
            sort_keys=True,
        )
        + "\n"
    )
    subprocess.run(
        [
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--local-user",
            key,
            "--armor",
            "--detach-sign",
            "--output",
            str(policy) + ".asc",
            str(policy),
        ],
        check=True,
        timeout=15,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--gpg-key", required=True)
    parser.add_argument(
        "--gpg-homedir",
        type=Path,
        default=Path(os.environ.get("GNUPGHOME", Path.home() / ".gnupg")),
    )
    parser.add_argument("--policy-file", type=Path, default=ROOT / "packaging/update-policy.json")
    args = parser.parse_args()
    minimum = json.loads(args.policy_file.read_text())["minimum_supported_version"]
    prepare(args.repository, args.output, args.repo_url, args.public_key.read_bytes(), args.version)
    write_policy(args.output, args.version, minimum, args.gpg_key, args.gpg_homedir)


if __name__ == "__main__":
    main()

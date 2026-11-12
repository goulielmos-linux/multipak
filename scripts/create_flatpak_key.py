"""Create a release-signing key outside the source tree; never print its private data."""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path.home() / ".config/multipak-release-signing"
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("Store the signing key outside the source directory")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    home = output / "gnupg"
    home.mkdir(mode=0o700)
    subprocess.run(
        [
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "MultiPak Flatpak Updates",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
    )
    listing = subprocess.check_output(
        ["gpg", "--homedir", str(home), "--batch", "--with-colons", "--list-secret-keys"], text=True
    )
    fingerprint = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )
    private = subprocess.check_output(
        ["gpg", "--homedir", str(home), "--batch", "--armor", "--export-secret-keys", fingerprint]
    )
    target = output / "multipak.private.asc"
    target.write_bytes(private)
    target.chmod(0o600)
    (output / "fingerprint.txt").write_text(fingerprint + "\n")
    print(f"FLATPAK_GPG_KEY_ID: {fingerprint}")
    print(f"Private key file for FLATPAK_GPG_PRIVATE_KEY: {target}")
    print("Keep this directory private and backed up. Never commit the private key.")


if __name__ == "__main__":
    main()

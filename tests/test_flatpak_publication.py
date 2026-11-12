import base64
import configparser
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("flatpak_publication", SCRIPTS / "publish_flatpak.py")
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    if not shutil.which("gpg"):
        pytest.skip("GnuPG is required")
    home = tmp_path_factory.mktemp("signing-fixture")
    home.chmod(0o700)
    command = ["gpg", "--homedir", str(home), "--batch"]
    subprocess.run(
        command
        + [
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "MultiPak test fixture",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield (
            subprocess.check_output(command + ["--export"]),
            subprocess.check_output(command + ["--export-secret-keys"]),
        )
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"], check=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/repo/",
        "https://user@example.org/repo/",
        "https://example.org/repo/?token=secret",
        "https://example.org/#x",
        "https://example.org/../repo",
        "https://example.org/\nGPGKey=bad",
    ],
)
def test_update_addresses_reject_insecure_or_injected_values(url):
    with pytest.raises(ValueError):
        publication.validate_url(url)


def test_installer_and_remote_embed_the_same_public_key_and_address(keys):
    public, _ = keys
    descriptors = publication.descriptors("https://example.org/multipak/repo", public)
    for name, text in descriptors.items():
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read_string(text)
        values = config["Flatpak Ref" if name.endswith("ref") else "Flatpak Repo"]
        assert values["Url"] == "https://example.org/multipak/repo/"
        assert base64.b64decode(values["GPGKey"]) == public
        assert "gpg-verify=false" not in text
    assert "Branch=stable" in descriptors["multipak.flatpakref"]


def test_private_signing_material_cannot_be_published(keys):
    _, private = keys
    with pytest.raises(ValueError, match="public signing key"):
        publication.descriptors("https://example.org/repo/", private)
    with pytest.raises(ValueError, match="public signing key"):
        publication.descriptors("https://example.org/repo/", b"invalid public key")


def test_unsigned_repository_is_rejected_without_creating_publication(tmp_path, keys):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "config").write_text("[core]\nmode=archive-z2\n")
    (repository / "summary").write_bytes(b"unsigned fixture")
    output = tmp_path / "pages"
    with pytest.raises(ValueError, match="signed"):
        publication.prepare(repository, output, "https://example.org/repo/", keys[0], "1.0.1")
    assert not output.exists()


def test_prerelease_never_replaces_the_stable_repository(tmp_path, keys):
    with pytest.raises(ValueError, match="stable"):
        publication.prepare(
            tmp_path / "repo", tmp_path / "pages", "https://example.org/repo/", keys[0], "1.1.0a1"
        )


def test_release_tag_is_checked_before_a_build_can_start():
    result = subprocess.run(
        [shutil.which("python3"), str(SCRIPTS / "build_flatpak.py"), "--expected-tag", "v99.0.0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "tag does not match" in result.stderr


def test_signed_security_policy_is_verified_and_tampering_is_rejected(tmp_path, keys):
    from multipak.update_notice import verify_policy

    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    command = ["gpg", "--homedir", str(home), "--batch"]
    subprocess.run(command + ["--import"], input=keys[1], capture_output=True, check=True)
    listing = subprocess.check_output(command + ["--with-colons", "--list-secret-keys"], text=True)
    fingerprint = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )
    output = tmp_path / "pages"
    output.mkdir()
    public_key = tmp_path / "public.gpg"
    public_key.write_bytes(keys[0])
    try:
        publication.write_policy(output, "1.0.2", "1.0.2", fingerprint, home)
        payload = (output / "update-policy.json").read_bytes()
        signature = (output / "update-policy.json.asc").read_bytes()
        policy = verify_policy(payload, signature, public_key)
        assert policy.required("1.0.1")
        assert not policy.required("1.0.2")
        with pytest.raises(subprocess.CalledProcessError):
            verify_policy(payload.replace(b"1.0.2", b"9.0.0"), signature, public_key)
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"], check=False)

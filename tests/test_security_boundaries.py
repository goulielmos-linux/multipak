"""Regression tests for untrusted packages and privileged system installation."""

import configparser
import hashlib
import json
import os
import pwd
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import privileged, privileged_files, system_install_helper
from multipak.desktop import parse_desktop, render_desktop
from multipak.metadata import inspect_package
from multipak.process import Runner


@pytest.mark.parametrize("key", ["Exec", "Name", "Icon", "Comment", "Actions"])
def test_desktop_continuations_are_rejected_in_every_value(key):
    content = f"[Desktop Entry]\nType=Application\nExec=demo\n{key}=text\n\tExec=/host/bypass\n"
    with pytest.raises((ValueError, configparser.Error)):
        render_desktop(content, "guest", "/usr/bin/distrobox")


def test_desktop_duplicate_exec_and_defaults_are_rejected():
    for content in (
        "[Desktop Entry]\nExec=demo\nExec=/host/bypass\n",
        "[DEFAULT]\nExec=/host/bypass\n[Desktop Entry]\nType=Application\n",
    ):
        with pytest.raises((ValueError, configparser.Error)):
            parse_desktop(content)


@pytest.mark.parametrize(
    "record",
    [
        b"foo\nbash\x1f1-1\x1fnoarch",
        b"foo\x1f1-1\x1fnoarch\n",
        b"foo\x1f1-1\x1fnoarch\x1fextra",
        b"foo\x1f\x1fnoarch",
        b"foo\x00bar\x1f1-1\x1fnoarch",
        b"foo\x1f1-1\x1fnoarch\r",
    ],
)
def test_rpm_identity_requires_exactly_one_complete_record(tmp_path, record):
    path = tmp_path / "malformed.rpm"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\0\0\0")

    class Query:
        def run(self, argv, **options):
            return record if options.get("binary") else ""

    with pytest.raises(ValueError, match="metadata"):
        inspect_package(path, Query())


def test_binary_record_retains_stdout_without_signature_warnings():
    runner = Runner(threading.Event(), lambda _: None)
    result = runner.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('warning: unsigned package\\n'); "
            "sys.stdout.buffer.write(b'foo\\x1f1-1\\x1fnoarch')",
        ],
        binary=True,
        output_limit=1024,
    )
    assert result == b"foo\x1f1-1\x1fnoarch"


def test_installed_helper_is_the_only_operational_entry_point(monkeypatch):
    calls = []
    digest = (
        Path(privileged.__file__)
        .with_name("assets")
        .joinpath("privileged.sha256")
        .read_text()
        .strip()
    )

    def run(argv, **kwargs):
        calls.append(argv)
        if argv == ["cat", privileged.MANIFEST]:
            return json.dumps({"bundle_sha256": digest})
        return "done"

    assert (
        privileged.call(SimpleNamespace(run=run), "system-install", "/tmp/reviewed.rpm", "a" * 64)
        == "done"
    )
    assert calls[-1] == [
        "pkexec",
        privileged.HELPER,
        "system-install",
        "/tmp/reviewed.rpm",
        "a" * 64,
    ]
    assert not any("python3" in arg for command in calls for arg in command)


def test_user_input_rejects_symlinks_and_nonregular_files(tmp_path):
    owner = pwd.getpwuid(os.getuid())
    source = tmp_path / "symlink"
    source.symlink_to("/etc/passwd")
    with pytest.raises(OSError):
        privileged_files.open_user_file(owner, source)
    with pytest.raises(ValueError):
        privileged_files.open_user_file(owner, tmp_path)


def test_system_snapshot_has_verified_bytes_and_a_private_parent(tmp_path, monkeypatch):
    from tempfile import TemporaryDirectory

    monkeypatch.setattr(
        system_install_helper.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: TemporaryDirectory(prefix="snapshot-", dir=tmp_path),
    )
    owner = pwd.getpwuid(os.getuid())
    source = tmp_path / "source.rpm"
    source.write_bytes(b"reviewed bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    calls = []

    def execute(argv, **options):
        target = Path(argv[-1])
        source.write_bytes(b"changed while authorization was open")
        assert target.read_bytes() == b"reviewed bytes"
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o400
        assert options["env"] == privileged_files.CLEAN_ENV
        calls.append(argv)

    system_install_helper.install(owner, source, digest, execute=execute)
    assert len(calls) == 1 and not Path(calls[0][-1]).exists()
    with pytest.raises(ValueError, match="changed"):
        system_install_helper.install(owner, source, digest, execute=execute)
    assert len(calls) == 1


def test_privileged_bundle_contains_exact_reviewed_sources():
    import io
    import tarfile

    from multipak import bootstrap_helpers

    root = Path(privileged.__file__).parent
    payload = (root / "assets/privileged.tar.gz").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == bootstrap_helpers.BUNDLE_SHA256
    assert (
        bootstrap_helpers.BUNDLE_SHA256 == (root / "assets/privileged.sha256").read_text().strip()
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        files = {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers()}
    for name, content in files.items():
        if name == "multipak.policy":
            continue
        assert content == (root / name).read_bytes(), name

import json

import pytest

from multipak.models import Package
from multipak.system import install_system


def test_deb_cannot_be_layered(fake_engine, deb):
    package = Package(deb, "deb", "demo", "1", "all")
    with pytest.raises(ValueError, match="only supports RPMs"):
        install_system(fake_engine, package)
    assert not fake_engine.runner.calls


def test_pending_deployment_blocks_a_new_install(fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr("multipak.system.os.geteuid", lambda: 1000)
    monkeypatch.setattr("multipak.system.system_install_available", lambda: True)
    calls = []

    class Pending:
        def run(self, argv, **_kwargs):
            calls.append(argv)
            return json.dumps({"deployments": [{"booted": False}, {"booted": True}]})

    fake_engine.runner = Pending()
    package = Package(tmp_path / "demo.rpm", "rpm", "demo", "1", "noarch")
    with pytest.raises(RuntimeError, match="pending"):
        install_system(fake_engine, package)
    assert calls == [["rpm-ostree", "status", "--json"]]


def test_successful_system_install_checks_pending_deployment(fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr("multipak.system.os.geteuid", lambda: 1000)
    monkeypatch.setattr("multipak.system.system_install_available", lambda: True)
    path = tmp_path / "demo.rpm"
    path.write_bytes(b"fixture")
    package = Package(path, "rpm", "demo", "1", "noarch")
    monkeypatch.setattr("multipak.system.inspect_package", lambda *_: package)
    calls = []

    class Host:
        installed = False

        def run(self, argv, **_kwargs):
            calls.append(argv)
            if argv[:2] == ["rpm-ostree", "install"]:
                self.installed = True
                return ""
            return json.dumps({"deployments": [{"booted": not self.installed}, {"booted": True}]})

    fake_engine.runner = Host()

    def install(runner, subsystem, source, digest, **kwargs):
        import hashlib
        from pathlib import Path

        assert subsystem == "system-install"
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == digest
        runner.run(["rpm-ostree", "install", source])
        return "MULTIPAK_SYSTEM_RPM_INSTALLED\n"

    monkeypatch.setattr("multipak.privileged.call", install)
    tx = install_system(fake_engine, package)
    assert tx.status == "system_pending"
    assert tx.method == "system"
    assert [argv[1] for argv in calls] == ["status", "install", "status"]
    assert not any("reset" in argv or "rollback" in argv or "cancel" in argv for argv in calls)


def test_container_cleanup_never_uninstalls_a_system_package(fake_engine, tmp_path, monkeypatch):
    from multipak.models import Transaction

    tx = Transaction("a" * 32, "", "", "demo", "1", "rpm", "", method="system")
    with pytest.raises(ValueError, match="cannot undo"):
        fake_engine.cleanup(tx)
    assert not fake_engine.runner.calls


def test_system_install_rejects_a_file_changed_during_metadata_inspection(
    fake_engine, tmp_path, monkeypatch
):
    import os
    import pwd

    from multipak.privileged_files import copy_verified_file

    monkeypatch.setattr("multipak.system.os.geteuid", lambda: 1000)
    monkeypatch.setattr("multipak.system.system_install_available", lambda: True)
    source = tmp_path / "selected.rpm"
    source.write_bytes(b"reviewed fixture")
    package = Package(source, "rpm", "demo", "1", "noarch")

    def inspect(staged, runner):
        staged.write_bytes(b"changed after copying, during metadata inspection")
        return package

    monkeypatch.setattr("multipak.system.inspect_package", inspect)
    fake_engine.runner.run = lambda *_a, **_kw: json.dumps({"deployments": [{"booted": True}]})

    def snapshot(runner, subsystem, path, digest, **options):
        copy_verified_file(pwd.getpwuid(os.getuid()), path, tmp_path / "snapshot.rpm", digest)
        pytest.fail("changed package reached the privileged installer")

    monkeypatch.setattr("multipak.privileged.call", snapshot)
    with pytest.raises(ValueError, match="changed"):
        install_system(fake_engine, package)
    assert fake_engine.tx.status == "system_failed"
    assert not (tmp_path / "snapshot.rpm").exists()

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

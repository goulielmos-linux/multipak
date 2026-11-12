import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak.metadata import inspect_package
from multipak.models import Transaction
from multipak.process import CommandError, Runner


def test_file_association_failure_does_not_block_package_inspection(deb, monkeypatch):
    pytest.importorskip("PySide6")
    from multipak.kirigami import live

    class Broken:
        def initialize_defaults(self):
            raise ValueError("damaged association state")

    monkeypatch.setattr(live, "FileAssociations", Broken)
    monkeypatch.setattr(live, "load_catalog", lambda *_: ())
    monkeypatch.setattr(live, "discover", lambda *_: [])
    logs = []
    engine = SimpleNamespace(runner=None, store=SimpleNamespace(root=Path("/nonexistent")))
    engine._log = logs.append
    inspection = live.inspect_installation(engine, deb)
    assert inspection.package.name == "demo-app"
    assert any("damaged association state" in line for line in logs)


def test_records_with_unknown_fields_remain_visible(store):
    tx = Transaction("b" * 32, "box", "image", "demo", "1", "deb", "", status="installed")
    store.save(tx)
    path = store.directory(tx.id) / "manifest.json"
    data = json.loads(path.read_text())
    data["field_from_a_future_version"] = True
    path.write_text(json.dumps(data))
    assert [item.id for item in store.transactions()] == [tx.id]


def test_process_that_keeps_running_after_closing_output_times_out():
    runner = Runner(threading.Event(), lambda _: None)
    script = "import os, time; os.close(1); os.close(2); time.sleep(30)"
    with pytest.raises(TimeoutError):
        runner.run([sys.executable, "-c", script], timeout=60)


def test_container_update_waits_for_no_other_operation(store):
    from multipak.updates import update_container

    engine = SimpleNamespace(
        store=store, runner=SimpleNamespace(run=lambda *a, **k: ""), progress=lambda *_: None
    )
    with store.lock(), pytest.raises(RuntimeError, match="already in progress"):
        update_container(engine, "MultiPak-Fedora-45")


def test_deb_without_required_field_has_a_clear_message(tmp_path):
    from conftest import make_deb

    path = make_deb(tmp_path / "no-version.deb", "Package: demo\nArchitecture: all\n")
    with pytest.raises(ValueError, match="missing the Version field"):
        inspect_package(path, None)


def test_association_rollback_restores_original_bytes(tmp_path):
    from multipak.file_associations import FORMATS, FileAssociations

    settings = FileAssociations(
        config_home=tmp_path / "config", data_home=tmp_path / "data", refresher=lambda: None
    )
    settings.path.parent.mkdir(parents=True)
    state = settings.state_path
    state.parent.mkdir(parents=True)
    state.write_bytes(b'{"before": {}, "note": "\xe9"}')  # Not valid UTF-8.
    settings.path.write_text("[Default Applications]\n")

    def fail():
        raise RuntimeError("cache failure")

    settings.refresher = fail
    with pytest.raises(RuntimeError, match="cache failure"):
        settings.apply(dict.fromkeys(FORMATS, True))
    assert state.read_bytes() == b'{"before": {}, "note": "\xe9"}'
    assert settings.path.read_text() == "[Default Applications]\n"


def test_appimage_icon_is_found_without_its_shortcut(store):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from multipak.kirigami.manager_icons import application_icon

    app = QApplication.instance() or QApplication([])
    assert app is not None
    tx = Transaction("e" * 32, "", "", "Demo", "1", "appimage", "", method="appimage")
    store.save(tx)
    directory = store.appimage_directory(tx.id)
    directory.mkdir(parents=True)
    (directory / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" fill="blue"/></svg>'
    )
    assert application_icon(store, tx).endswith("/icon.svg")


def test_bootstrap_code_is_fixed_on_the_command_line(monkeypatch):
    from multipak import privileged

    calls = []

    def run(argv, **options):
        calls.append(argv)
        if argv[0] == "cat":
            raise CommandError(argv, 1, "missing")
        return "MULTIPAK_PRIVILEGED_HELPERS_READY\n"

    privileged.ensure_installed(SimpleNamespace(run=run))
    command = calls[-1]
    assert command[:5] == ["pkexec", "/usr/bin/python3", "-I", "-u", "-c"]
    source = Path(privileged.__file__).with_name("bootstrap_helpers.py").read_text()
    assert command[5] == source
    assert command[6].endswith("privileged.tar.gz")
    assert not any(arg.endswith("bootstrap_helpers.py") for arg in command)

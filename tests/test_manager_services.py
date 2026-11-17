"""The Application Manager's list of the snaps' background services."""

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_manager_repairs import InlineWorker  # noqa: E402

from multipak import snap, snap_daemons, snap_meta  # noqa: E402
from multipak.kirigami.manager import Manager  # noqa: E402
from multipak.models import Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402

YAML = (
    "name: served\napps:\n  open:\n    command: o\n"
    "  web:\n    command: w\n    daemon: simple\n"
    "  mine:\n    command: m\n    daemon: simple\n    daemon-scope: user\n"
    "  tick:\n    command: t\n    daemon: dbus\n"
)


@pytest.fixture(scope="module")
def application():
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def manager(tmp_path, application, monkeypatch):
    store = Store(tmp_path / "data", tmp_path / "applications")
    tx = Transaction(
        "a" * 32, "", "", "Served", "1", "snap", "a" * 64, method="snap", status="installed"
    )
    store.save(tx)
    info = snap_meta.parse(YAML)
    monkeypatch.setattr(snap, "_context", lambda store, tx: (info, None))
    states = {"web": "active", "mine": "inactive", "tick": "inactive"}
    actions = []

    class FakeServices:
        def __init__(self, tx, info, call=None, **_kwargs):
            pass

        def state(self, name):
            return states[name]

        def act(self, action, names):
            actions.append((action, names))
            name = names[0].split(".")[1]
            states[name] = "active" if action == "start" else "inactive"

    monkeypatch.setattr(snap_daemons, "Services", FakeServices)
    controller = Manager(store, worker_factory=InlineWorker)
    controller.actions = actions
    assert controller.hasSnaps
    controller.openList("services")
    return controller


def test_the_services_are_listed_with_their_state(manager):
    rows = manager.rows
    assert [row["title"] for row in rows] == ["served.web", "served.mine", "served.tick"]
    assert [row["subtitle"] for row in rows] == ["Running", "Stopped", "Stopped"]
    assert "session" in rows[1]["detail"]
    assert rows[2]["note"] == "MultiPak cannot run this service: dbus"
    manager.select(2)
    assert not manager.canAct


def test_a_service_is_started_and_stopped(manager):
    manager.select(1)
    assert manager.canAct
    manager.controlService("start")
    assert manager.actions == [("start", ["served.mine"])]
    assert manager.rows[1]["subtitle"] == "Running"
    manager.controlService("stop")
    assert manager.rows[1]["subtitle"] == "Stopped"
    manager.controlService("restart")  # Not offered here.
    assert len(manager.actions) == 2


def test_each_row_has_its_own_start_or_stop_button(manager):
    rows = manager.rows
    assert [row["running"] for row in rows] == [True, False, False]
    assert [row["supported"] for row in rows] == [True, True, False]
    # The button of a row acts on that row, whatever was selected before.
    manager.select(0)
    manager.controlServiceRow(1, "start")
    assert manager.actions == [("start", ["served.mine"])]
    assert manager.selectedIndex == 1
    assert manager.rows[1]["running"]
    manager.controlServiceRow(2, "start")  # dbus: MultiPak cannot run it.
    assert len(manager.actions) == 1


def test_an_administrator_command_runs_the_launcher_with_its_option(manager, monkeypatch):
    import subprocess
    import sys

    manager.openList("commands")
    rows = manager.rows
    # Only apps: the services are not commands.
    assert [row["title"] for row in rows] == ["served.open"]
    assert not manager.canAct and manager.adminCommandName == ""
    manager.select(0)
    assert manager.canAct and manager.adminCommandName == "served.open"
    assert manager.validAdminArguments("app:install 'a b'")
    assert not manager.validAdminArguments("unclosed 'quote")
    seen = []

    class FakeProcess:
        def __init__(self, argv, **options):
            seen.append((argv, options))
            self.stdout = __import__("io").StringIO("Nextcloud is installed\n")

        def wait(self):
            return 3

    class LoggingWorker(InlineWorker):
        # As the real worker: what the operation logs reaches the details.
        def __init__(self, *arguments):
            super().__init__(*arguments)
            self.engine.notify_log = self.log.emit

    manager.worker_factory = LoggingWorker
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    manager.runAdminCommand("unclosed 'quote")
    assert seen == []
    manager.runAdminCommand("app:install 'a b'")
    ((argv, options),) = seen
    assert argv == [
        sys.executable,
        "-m",
        "multipak.snap_launcher",
        "--admin",
        "a" * 32,
        "open",
        "app:install",
        "a b",
    ]
    assert options["stdin"] == subprocess.DEVNULL
    assert "Nextcloud is installed" in manager.logs
    assert "served.open app:install a b" in manager.logs
    assert "exit code 3" in manager.status and manager.hasError

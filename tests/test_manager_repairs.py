"""Repair and delete actions for AppImages whose container was renamed or deleted."""

import threading

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from multipak import appimage_container  # noqa: E402
from multipak.engine import Engine  # noqa: E402
from multipak.kirigami.manager import Manager  # noqa: E402
from multipak.models import Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402


class InlineWorker(QObject):
    """Runs the operation at once, on the calling thread."""

    progress = Signal(int, str)
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str, object)
    finished = Signal()

    def __init__(self, store, operation, parent=None):
        super().__init__(parent)
        self.engine = Engine(store, threading.Event())
        self.operation = operation

    def start(self):
        try:
            self.succeeded.emit(self.operation(self.engine))
        except Exception as error:
            self.failed.emit(str(error), None)
        self.finished.emit()


@pytest.fixture
def manager(tmp_path):
    store = Store(tmp_path / "data", tmp_path / "applications")
    tx = Transaction(
        "a" * 32,
        appimage_container.CONTAINER_NAME,
        "",
        "Flatpak-Builder2",
        "1.0",
        "appimage",
        "",
        method="appimage",
        status="installed",
        appimage_mode="container",
        container_id="c" * 64,
        launchers=["multipak-" + "a" * 32 + "-appimage.desktop"],
    )
    store.save(tx)
    directory = store.appimage_directory(tx.id)
    (directory / "AppDir").mkdir(parents=True)
    (directory / "application.AppImage").write_bytes(b"AppImage")
    store.launcher(tx, tx.launchers[0]).write_text("[Desktop Entry]\n")
    controller = Manager(store, worker_factory=InlineWorker)
    controller._repairs = [(tx.id, tx.package_name)]
    return controller


def test_a_broken_appimage_is_listed_with_its_escaped_name(manager):
    manager._repairs = [("a" * 32, "<i>App</i>")]
    assert manager.repairs == [
        {"id": "a" * 32, "name": "<i>App</i>", "html": "<b>&lt;i&gt;App&lt;/i&gt;</b>"}
    ]


def test_repair_asks_first_and_explains_a_renamed_container(manager, monkeypatch):
    plan = {
        "application": "Flatpak-Builder2",
        "old": "MultiPak-AppImages",
        "current": "Meus AppImages",
        "target": "MultiPak-AppImages",
        "kind": "renamed",
        "deleted": False,
    }
    monkeypatch.setattr(appimage_container, "repair_plan", lambda engine, identifier: plan)
    repaired = []
    monkeypatch.setattr(
        appimage_container,
        "repair",
        lambda engine, identifier, on_system=False: repaired.append(identifier),
    )
    asked = []
    manager.repairConfirmationRequested.connect(lambda: asked.append(True))
    manager.requestRepair("a" * 32)
    assert asked and not repaired
    assert "renamed to Meus AppImages" in manager.repairExplanation
    manager.cancelRepair()
    manager.confirmRepair()
    assert not repaired  # Cancel leaves everything as it was.


def test_delete_removes_the_copy_and_shortcut_but_not_the_container(manager, monkeypatch):
    store = manager.store
    tx = store.transactions()[0]
    manager.checkRepairs = lambda: None
    asked = []
    manager.deleteConfirmationRequested.connect(lambda: asked.append(True))
    manager.requestDelete(tx.id)
    assert asked and manager.deleteName == "Flatpak-Builder2"
    manager.confirmDelete()
    assert store.transactions() == []
    assert not store.appimage_directory(tx.id).exists()
    assert not store.launcher(tx, tx.launchers[0]).exists()
    assert manager.repairs == []


def test_the_uninstall_button_of_a_row_asks_about_that_application(manager):
    tx = manager.store.transactions()[0]
    manager._mode, manager._items, manager._selected = "remove", [tx], -1
    assert manager.rows[0]["removable"]
    asked = []
    manager.confirmationRequested.connect(lambda: asked.append(True))
    manager.removeRow(0)
    assert asked == [True]
    assert manager.selectedIndex == 0
    assert manager.confirmationName == "Flatpak-Builder2"


def test_an_application_without_its_container_is_marked_and_forgotten_on_request(
    manager, monkeypatch
):
    from multipak import containers

    store = manager.store
    app = Transaction(
        "d" * 32,
        "meu-fedora",
        "image",
        "gimp",
        "3.0",
        "rpm",
        "",
        status="installed",
        container_id="e" * 64,
        launchers=["multipak-" + "d" * 32 + "-gimp.desktop"],
    )
    store.save(app)
    store.launcher(app, app.launchers[0]).write_text("[Desktop Entry]\n")
    monkeypatch.setattr(
        containers, "abandoned", lambda engine: [t for t in store.transactions() if t.id == app.id]
    )
    manager.openList("remove")
    rows = {row["title"]: row for row in manager.rows}
    # Listed and marked, not removed on its own.
    assert "Container not found" in rows["gimp"]["note"]
    assert rows["gimp"]["removable"]
    assert store.launcher(app, app.launchers[0]).exists()
    assert rows["Flatpak-Builder2"]["note"] != rows["gimp"]["note"]
    forgotten = []
    monkeypatch.setattr(
        containers,
        "forget",
        lambda engine, tx: (forgotten.append(tx.id), store.remove_directory(tx))[1],
    )
    index = [row["title"] for row in manager.rows].index("gimp")
    manager.removeRow(index)
    assert "shortcut" in manager.removalExplanation
    manager.confirmRemoval()
    assert forgotten == [app.id]
    assert app.id not in {tx.id for tx in store.transactions()}

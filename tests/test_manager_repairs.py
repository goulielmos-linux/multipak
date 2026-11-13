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

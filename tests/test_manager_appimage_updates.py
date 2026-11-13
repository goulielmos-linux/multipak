"""The Application Manager's Update AppImages list and its confirmations."""

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_manager_repairs import InlineWorker  # noqa: E402

from multipak import appimage_updates  # noqa: E402
from multipak.appimage_updates import Check, LinkMismatch, Prepared, Release  # noqa: E402
from multipak.kirigami.manager import Manager  # noqa: E402
from multipak.models import Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def application():
    # Other tests need a full QApplication; only one application object may exist.
    return QCoreApplication.instance() or QApplication([])


def settle():
    for _ in range(5):
        QCoreApplication.processEvents()


@pytest.fixture
def manager(tmp_path, application, monkeypatch):
    store = Store(tmp_path / "data", tmp_path / "applications")
    for token, name in (("a", "Alpha"), ("b", "Beta")):
        tx = Transaction(
            token * 32,
            "",
            "",
            name,
            "1.0",
            "appimage",
            token * 64,
            method="appimage",
            status="installed",
            appimage_mode="extracted",
        )
        store.save(tx)
        store.appimage_directory(tx.id).mkdir(parents=True)
    checks = {
        "a" * 32: Check("available", "https://github.com/o/a", Release("https://f/a", "a", "2.0")),
        "b" * 32: Check("no-source"),
    }
    monkeypatch.setattr(appimage_updates, "check", lambda engine, force=False: dict(checks))
    monkeypatch.setattr(appimage_updates, "running", lambda store, tx, *a: [])
    controller = Manager(store, worker_factory=InlineWorker)
    signals = []
    for name in ("updateLinkRequested", "updateClosingRequested", "updateDependenciesRequested"):
        getattr(controller, name).connect(lambda name=name: signals.append(name))
    controller.signals = signals
    controller.openList("appimages")
    return controller


def prepared(packages=(), link=""):
    return Prepared("a" * 32, None, "Alpha", "2.0", link, packages, "• Library libnew.so.1")


def test_rows_show_what_each_check_found(manager):
    notes = [row["note"] for row in manager.rows]
    assert notes == [
        "Update available: 2.0",
        "The application does not indicate its update address.",
    ]
    assert [row["title"] for row in manager.rows] == ["Alpha", "Beta"]
    manager.select(0)
    assert manager.canAct


def test_an_update_downloads_and_installs(manager, monkeypatch):
    installed = []
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared())

    def install(engine, value):
        installed.append(value)
        tx = manager.store.transactions()[0]
        tx.package_version = "2.0"
        return tx

    monkeypatch.setattr(appimage_updates, "install", install)
    manager.select(0)
    manager.updateSelected()
    settle()
    assert installed and manager.status == "Alpha was updated."
    assert manager.rows[0]["note"] == "Up to date"
    assert manager.signals == []


def test_new_dependencies_are_confirmed_and_going_back_discards(manager, monkeypatch):
    discarded = []
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared(("libnew",)))
    monkeypatch.setattr(appimage_updates, "discard", lambda store, tx_id: discarded.append(tx_id))
    monkeypatch.setattr(appimage_updates, "install", lambda *a: pytest.fail("not confirmed"))
    manager.select(0)
    manager.updateSelected()
    assert manager.signals == ["updateDependenciesRequested"]
    assert "libnew.so.1" in manager.updateDependencies
    manager.cancelAppImageUpdate()
    assert discarded == ["a" * 32] and manager.updateName == ""


def test_a_running_application_must_be_closed_first(manager, monkeypatch):
    state = {"running": True}
    monkeypatch.setattr(
        appimage_updates, "running", lambda store, tx, *a: [7] if state["running"] else []
    )
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared(("libnew",)))

    def close(store, tx):
        state["running"] = False
        return True

    monkeypatch.setattr(appimage_updates, "close", close)
    manager.select(0)
    manager.updateSelected()
    assert manager.signals == ["updateClosingRequested"] and manager.updateName == "Alpha"
    manager.closeUpdatedApplication()
    settle()
    assert manager.signals[-1] == "updateDependenciesRequested"


def test_an_application_that_does_not_close_is_asked_again(manager, monkeypatch):
    monkeypatch.setattr(appimage_updates, "running", lambda store, tx, *a: [7])
    monkeypatch.setattr(appimage_updates, "close", lambda store, tx: False)
    manager.select(0)
    manager.updateSelected()
    manager.closeUpdatedApplication()
    assert manager.signals == ["updateClosingRequested", "updateClosingRequested"]
    assert "did not close" in manager.status


def test_without_an_address_the_link_is_asked_and_checked(manager, monkeypatch):
    def check_link(engine, identifier, text):
        if "wrong" in text:
            raise appimage_updates.UpdateError("The address was not found.")
        return Check("available", text, Release("https://f/b", "b", "3"), link=text)

    downloads = []
    monkeypatch.setattr(appimage_updates, "check_link", check_link)

    def download(engine, identifier, release, link=""):
        downloads.append(link)
        if len(downloads) == 1:
            raise LinkMismatch
        return prepared(("libnew",), link)

    monkeypatch.setattr(appimage_updates, "download", download)
    manager.select(1)
    assert manager.canAct
    manager.updateSelected()
    assert manager.signals == ["updateLinkRequested"] and manager.updateLinkError == ""
    manager.submitUpdateLink("https://wrong.example/x.AppImage")
    assert manager.updateLinkError == "The address was not found."
    manager.submitUpdateLink("https://github.com/o/b")
    settle()
    # The download showed another application: the link is asked for again.
    assert manager.updateLinkError == str(LinkMismatch())
    assert manager.signals.count("updateLinkRequested") == 3
    manager.submitUpdateLink("https://github.com/o/right")
    settle()
    assert downloads[-1] == "https://github.com/o/right"
    assert manager.signals[-1] == "updateDependenciesRequested"


def test_reloading_consults_the_servers_again(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(
        appimage_updates, "check", lambda engine, force=False: calls.append(force) or {}
    )
    manager.refresh()
    assert calls == [True]
    manager.openList("appimages")
    assert calls == [True, False]
    assert manager.status == "No AppImages are available to update."

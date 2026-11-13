"""Exercise per-container actions and dialogs with a simulated Android backend."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
if not os.environ.get("MULTIPAK_CHECK_INSTALLED"):
    sys.path.insert(0, str(PROJECT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MULTIPAK_LANGUAGE", "pt_BR")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
from PySide6.QtCore import QEvent, QMetaObject, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak import android_instances as environments  # noqa: E402
from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    output = PROJECT.parent / "work/android-mode-ui"
    output.mkdir(exist_ok=True)
    entries = [
        dict(id="androida", label="MultiPak-Android-13", version="Android 13", initialized=True),
        dict(id="androidb", label="Jogos", version="Android 13", initialized=True),
        dict(id="androidc", label="Aplicativos antigos", version="Android 11", initialized=False),
    ]
    states = {"androida": "FROZEN", "androidb": "STOPPED", "androidc": "STOPPED"}
    calls = []
    fail = []
    update_mode = ["updated"]

    def factory(store, operation, parent):
        worker = Worker(store, operation, parent)

        def run(args, **kwargs):
            if args[0] == "cat":
                identifier = args[1].split("lxc.payload.")[1].split("/")[0]
                if states[identifier] == "STOPPED":
                    raise RuntimeError("cgroup does not exist")
                frozen = int(states[identifier] == "FROZEN")
                return f"usage_usec 1500000\nuser_usec 1000000\n805306368\n{frozen}\n"
            assert args[0] == environments.CLI and args[1] == "--instance" and args[3] == "status"
            state = states[args[2]]
            return (
                "Session:\tSTOPPED\n"
                if state == "STOPPED"
                else ("Session:\tRUNNING\nContainer:\t" + state + "\n")
            )

        worker.engine.runner.run = run
        return worker

    class Android:
        def __init__(self, runner, identifier):
            self.identifier = identifier

        def ready(self, progress):
            calls.append(("start", self.identifier))
            if fail:
                raise RuntimeError("Simulated start failure")
            states[self.identifier] = "RUNNING"
            next(e for e in entries if e["id"] == self.identifier)["initialized"] = True
            progress(100, "Ready")

        def stop(self):
            calls.append(("stop", self.identifier))
            states[self.identifier] = "STOPPED"

        def command(self, *args):
            assert args == ("show-full-ui",)
            calls.append(("open", self.identifier))

    def create(runner, profile, label, **kwargs):
        calls.append(("create", profile, label))
        entries.append(dict(id="androidd", label=label, version="Android 11", initialized=True))
        states["androidd"] = "STOPPED"

    def remove(runner, identifier, **kwargs):
        calls.append(("remove", identifier, kwargs["delete_data"]))
        entries[:] = [e for e in entries if e["id"] != identifier]

    def upgrade(runner, identifier, *, progress):
        calls.append(("upgrade", identifier))
        progress(-1, "Verificando atualização…")
        if update_mode[0] == "failed":
            raise RuntimeError("Simulated update failure")
        return update_mode[0] == "updated"

    def wait(predicate):
        for _ in range(250):
            if predicate():
                QTest.qWait(80)
                return
            QTest.qWait(20)
        assert predicate()

    with (
        tempfile.TemporaryDirectory(prefix="multipak-android-cards-") as temp,
        patch.object(environments, "discover", lambda _: list(entries)),
        patch.object(environments, "InstanceWaydroid", Android),
        patch.object(environments, "create", create),
        patch.object(environments, "remove", remove),
        patch.object(environments, "upgrade", upgrade),
    ):
        os.environ["XDG_DATA_HOME"] = temp + "/data"
        os.environ["XDG_CONFIG_HOME"] = temp + "/config"
        manager = Manager(
            Store(Path(temp) / "records", Path(temp) / "applications"), worker_factory=factory
        )
        engine, manager, window = create_manager(app, manager)
        manager.android.worker_factory = factory
        manager.openAndroidEnvironments()
        wait(lambda: not manager.busy)
        QTest.qWait(200)
        page = window.property("currentPage")

        def find(name):
            item = page.findChild(QObject, name)
            pending = [window.contentItem()]
            while item is None and pending:
                candidate = pending.pop()
                if candidate.objectName() == name:
                    item = candidate
                elif isinstance(candidate, QQuickItem):
                    pending.extend(candidate.childItems())
            assert item is not None, name
            return item

        def click(name):
            item = find(name)
            assert item.isVisible() and item.property("enabled"), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            QTest.qWait(120)

        assert [r["state"] for r in manager.android.rows] == ["frozen", "stopped", "uninitialized"]
        wait(lambda: "androida" in manager.android.stats)
        assert manager.android.stats["androida"]["cpu"].startswith("0")
        assert manager.android.stats["androida"]["memory"]
        assert "androidb" not in manager.android.stats
        assert "RAM:" in find("androidManagerStats0").property("text")
        assert window.grabWindow().save(str(output / "android-container-cards.png"))
        # Updating needs confirmation and targets the card that opened the menu.
        click("androidManagerMore2")
        assert not find("androidManagerUpdate2").property("enabled")
        QTest.keyClick(window, Qt.Key_Escape)
        QTest.qWait(100)
        count = len(calls)
        click("androidManagerMore1")
        update_action = find("androidManagerUpdate1")
        QTest.keyClick(window, Qt.Key_Escape)
        QTest.qWait(100)
        QMetaObject.invokeMethod(update_action, "triggered")
        update_dialog = find("androidUpdateDialog")
        wait(lambda: update_dialog.property("opened"))
        assert window.grabWindow().save(str(output / "android-container-update.png"))
        QMetaObject.invokeMethod(update_dialog, "reject")
        wait(lambda: not update_dialog.property("visible"))
        QTest.qWait(200)
        assert len(calls) == count
        click("androidManagerMore1")
        update_action = find("androidManagerUpdate1")
        QTest.keyClick(window, Qt.Key_Escape)
        QTest.qWait(100)
        QMetaObject.invokeMethod(update_action, "triggered")
        QMetaObject.invokeMethod(update_dialog, "accept")
        wait(lambda: not manager.busy)
        assert calls[-1] == ("upgrade", "androidb")
        assert manager.android.status == "Contêiner Android atualizado."
        assert len(manager.android.rows) == 3
        update_mode[0] = "current"
        manager.android.updateInstance("androidb")
        wait(lambda: not manager.busy)
        assert manager.android.status == "O contêiner Android já está atualizado."
        update_mode[0] = "failed"
        manager.android.updateInstance("androidb")
        wait(lambda: not manager.busy)
        assert manager.android.hasError and "update failure" in manager.android.status
        assert len(manager.android.rows) == 3
        # Each button routes by immutable instance ID, regardless of previous selection.
        manager.android.select(0)
        click("androidManagerPower1")
        wait(lambda: not manager.busy)
        assert calls[-1] == ("start", "androidb")
        click("androidManagerPower0")
        wait(lambda: not manager.busy)
        assert calls[-1] == ("stop", "androida")
        click("androidManagerOpen2")
        wait(lambda: not manager.busy)
        assert calls[-2:] == [("start", "androidc"), ("open", "androidc")]
        click("androidManagerNew")
        dialog = find("androidCreateDialog")
        wait(lambda: dialog.property("opened"))
        assert find("androidManagerName").property("text") == ""
        assert not find("androidManagerCreate").property("enabled")
        find("androidManagerProfile").setProperty("currentIndex", 1)
        find("androidManagerName").setProperty("text", "Android de testes")
        assert window.grabWindow().save(str(output / "android-container-create.png"))
        click("androidManagerCreate")
        wait(lambda: not manager.busy)
        assert calls[-1] == ("create", "android-11", "Android de testes")
        assert not dialog.property("opened")
        # Confirm the selected card and data option; preserve unrelated containers.
        click("androidManagerMore0")
        QMetaObject.invokeMethod(find("androidManagerRemove0"), "triggered")
        QTest.qWait(100)
        removal = find("androidRemoveDialog")
        wait(lambda: removal.property("opened"))
        assert not find("androidDeleteData").property("checked")
        find("androidDeleteData").setProperty("checked", True)
        QMetaObject.invokeMethod(removal, "accept")
        wait(lambda: not manager.busy)
        assert calls[-1] == ("remove", "androida", True)
        assert [e["id"] for e in entries] == ["androidb", "androidc", "androidd"]
        fail.append(True)
        manager.android.actOn("androidd", "start")
        wait(lambda: not manager.busy)
        assert manager.android.hasError and "Simulated" in manager.android.status
        assert len(manager.android.rows) == 3
        manager.android._log("Registro de uma operação simulada.\nNenhum Android foi alterado.")
        QTest.qWait(200)
        assert not find("androidManagerLogView").isVisible()
        assert window.grabWindow().save(str(output / "android-details-collapsed.png"))
        click("androidManagerDetailsButton")
        assert find("androidManagerLogView").isVisible()
        assert window.grabWindow().save(str(output / "android-details-expanded.png"))
        click("androidManagerDetailsButton")
        assert not find("androidManagerLogView").isVisible()
        window.close()
        engine.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    print("Android cards, states, start/stop/open, create/remove dialogs and errors verified.")


if __name__ == "__main__":
    main()

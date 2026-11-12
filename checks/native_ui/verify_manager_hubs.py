"""Verify manager navigation and package picker without launching applications."""

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
from PySide6.QtCore import QEvent, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.store import Store  # noqa: E402


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    output = PROJECT.parent / "verification/ui"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="multipak-manager-hubs-") as temp:
        os.environ["XDG_DATA_HOME"] = temp + "/data"
        os.environ["XDG_CONFIG_HOME"] = temp + "/config"
        store = Store(Path(temp) / "records", Path(temp) / "applications")
        manager = Manager(store, preview=True)
        engine, manager, window = create_manager(app, manager)
        QTest.qWait(350)

        def click(name):
            page = window.property("currentPage")
            item = page.findChild(QObject, name)
            assert item is not None and item.isVisible() and item.property("enabled"), name
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            QTest.qWait(150)

        home = window.property("currentPage")
        names = [
            "managerInstallButton",
            "managerRemoveButton",
            "managerUpdateButton",
            "managerSettingsButton",
        ]
        y = [home.findChild(QObject, n).mapToScene(QPointF(0, 0)).y() for n in names]
        assert y == sorted(y)
        assert window.grabWindow().save(str(output / "manager-home.png"))
        click("managerSettingsButton")
        assert window.property("currentPage").objectName() == "managerSettingsPage"
        assert window.grabWindow().save(str(output / "manager-settings.png"))
        for _ in range(3):
            for _ in range(50):
                if not manager.busy:
                    break
                QTest.qWait(20)
            current = window.property("currentPage")
            assert current.findChild(QObject, "languageCombo") is not None
            notice = current.findChild(QObject, "languageFallbackNotice")
            app._multipak_languages.choose(0)
            QTest.qWait(50)
            assert notice.isVisible()
            color = notice.property("color")
            assert color.red() > color.green()
            app._multipak_languages.choose(1)
            QTest.qWait(50)
            assert not notice.isVisible()
            assert len(manager.formatOptions) == 4
            assert all("apk" not in item["label"].lower() for item in manager.formatOptions)
            assert abs(current.mapToScene(QPointF(0, 0)).x()) < 1
            assert abs(current.width() - window.width()) < 1
            click("associationBackButton")
            QTest.qWait(200)
            assert window.property("currentPage").objectName() == "managerPage"
            click("managerSettingsButton")
        app._multipak_languages.choose(0)
        QTest.qWait(80)
        assert window.grabWindow().save(str(output / "manager-settings-return.png"))
        for _ in range(50):
            if not manager.busy:
                break
            QTest.qWait(20)
        click("associationBackButton")
        QTest.qWait(250)
        manager.preview = False
        with (
            patch(
                "PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=("/tmp/app.deb", "")
            ),
            patch("PySide6.QtCore.QProcess.startDetached", return_value=(True, 123)) as launch,
        ):
            click("managerInstallButton")
            assert launch.call_args.args[1][-1] == "/tmp/app.deb"
        window.close()
        engine.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    print("Manager home, unified settings, back navigation and package picker verified.")


if __name__ == "__main__":
    main()

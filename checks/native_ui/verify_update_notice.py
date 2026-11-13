"""Verify both update dialogs and the shared reminder without contacting the network."""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MULTIPAK_LANGUAGE", "pt_BR")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
from PySide6.QtCore import QEvent, QMetaObject, QObject, QUrl, Slot  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak import __version__  # noqa: E402
from multipak.kirigami.main import create_preview  # noqa: E402
from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.kirigami.update_notice import UpdateNotice  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.update_notice import DAY, Policy, version_tuple  # noqa: E402


def find(window, name):
    result = window.findChild(QObject, name)
    items = [window.contentItem()]
    while result is None and items:
        item = items.pop()
        if item.objectName() == name:
            result = item
        else:
            items.extend(item.childItems())
    assert result is not None, name
    return result


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    with tempfile.TemporaryDirectory(prefix="multipak-update-ui-") as directory:
        os.environ["XDG_DATA_HOME"] = directory + "/data"
        os.environ["XDG_CONFIG_HOME"] = directory + "/config"
        output = PROJECT.parent / "verification/ui"
        output.mkdir(parents=True, exist_ok=True)
        manager_engine, manager, manager_window = create_manager(
            app, Manager(Store(Path(directory) / "records", Path(directory) / "apps"), preview=True)
        )
        manager_notice = manager_engine._update_notice
        major, minor, patch_number = version_tuple(__version__)
        next_version = f"{major}.{minor}.{patch_number + 1}"
        ordinary = Policy(next_version, __version__)
        asynchronous = UpdateNotice(manager_engine, enabled=True, state=manager_notice.state)
        with patch.object(asynchronous.state, "checked_policy", return_value=ordinary):
            asynchronous.start()
            for _ in range(100):
                if not asynchronous.checking:
                    break
                QTest.qWait(10)
        assert not asynchronous.checking and asynchronous.policy == ordinary
        manager_notice._received(ordinary)
        QTest.qWait(100)
        manager_dialog = find(manager_window, "applicationUpdateDialog")
        assert manager_dialog.property("opened")
        assert find(manager_dialog, "updateRemindButton").property("text") == "Lembrar depois"
        manager_dialog.close()
        assert not manager_dialog.property("opened")
        assert "snooze_until" not in manager_notice.state.read()
        manager_notice._received(ordinary)
        QTest.qWait(100)
        assert manager_dialog.property("opened")
        QMetaObject.invokeMethod(find(manager_dialog, "updateRemindButton"), "clicked")
        assert not manager_dialog.property("opened")
        deadline = manager_notice.state.read()["snooze_until"]
        assert DAY - 2 <= deadline - time.time() <= DAY

        wizard_engine, _, wizard = create_preview(app)
        notice = wizard_engine._update_notice
        notice._received(ordinary)
        QTest.qWait(100)
        dialog = find(wizard, "applicationUpdateDialog")
        assert not dialog.property("opened")
        notice.state.save(snooze_until=time.time() - 1)
        notice._received(ordinary)
        QTest.qWait(100)
        assert dialog.property("opened")
        assert dialog.grabWindow().save(str(output / "update-optional.png"))
        QMetaObject.invokeMethod(find(dialog, "updateRemindButton"), "clicked")
        assert not manager_notice.state.should_show(ordinary, __version__)

        critical = Policy(next_version, next_version)
        notice._received(critical)
        QTest.qWait(100)
        assert dialog.property("opened") and notice.required and not notice.installAllowed
        assert find(dialog, "updateRemindButton").property("text") == "Fechar"
        assert dialog.grabWindow().save(str(output / "update-required.png"))

        class Links(QObject):
            @Slot(QUrl)
            def capture(self, url):
                self.url = url.toString()

        links = Links()
        QDesktopServices.setUrlHandler("appstream", links, "capture")
        try:
            QMetaObject.invokeMethod(find(dialog, "updateNowButton"), "clicked")
            assert links.url == "appstream://io.github.goulielmos_linux.multipak"
        finally:
            QDesktopServices.unsetUrlHandler("appstream")
        QMetaObject.invokeMethod(find(dialog, "updateRemindButton"), "clicked")
        QTest.qWait(100)
        assert not wizard.isVisible()

        manager_notice._received(critical)
        QTest.qWait(100)
        assert manager_dialog.property("opened")
        QMetaObject.invokeMethod(find(manager_dialog, "updateRemindButton"), "clicked")
        QTest.qWait(100)
        assert manager_window.isVisible()
        assert not find(manager_window, "managerInstallButton").property("enabled")
        assert find(manager_window, "managerRemoveButton").property("enabled")
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName") as picker:
            manager.openInstaller()
            picker.assert_not_called()
        manager_window.close()
        for engine in (manager_engine, wizard_engine):
            engine.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    print("Both update dialogs, shared 24-hour reminder, store action and security gate verified.")


if __name__ == "__main__":
    main()

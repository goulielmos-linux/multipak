"""Verify About data, links and layout without running containers."""

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
from PySide6.QtCore import QEvent, QMetaObject, QObject, QUrl, Slot  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.about import AboutInfo  # noqa: E402
from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.store import Store  # noqa: E402


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
    output = PROJECT.parent / "verification/ui"
    with tempfile.TemporaryDirectory(prefix="multipak-about-android-") as temp:
        root = Path(temp)
        output.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_DATA_HOME"] = str(root / "data")
        os.environ["XDG_CONFIG_HOME"] = str(root / "config")
        commands = {
            "distrobox": ("--version", "distrobox: 1.8.2.5"),
            "podman": ("--version", "podman version 6.1.3"),
        }
        for name, (flag, version) in commands.items():
            command = root / name
            command.write_text(
                '#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = "'
                + flag
                + '" ] || exit 3\nprintf "%s\\n" "'
                + version
                + '"\n'
            )
            command.chmod(0o700)
        engine, controller, window = create_manager(
            app, Manager(Store(root / "records", root / "applications"), preview=True)
        )
        dialog = window.findChild(QObject, "managerAboutDialog")
        with (
            patch(
                "multipak.kirigami.about.host.which",
                lambda name: str(root / name) if name in commands else None,
            ),
            patch("multipak.kirigami.about.host.command", lambda argv, **options: list(argv)),
        ):
            QMetaObject.invokeMethod(dialog, "open")
            for _ in range(250):
                if not engine._about_info.loading:
                    break
                QTest.qWait(20)
        assert not engine._about_info.loading
        rows = {row["id"]: row for row in engine._about_info.backends}
        assert list(rows) == ["Distrobox", "Podman"]
        assert rows["Distrobox"]["version"] == "1.8.2.5"
        assert rows["Podman"]["version"] == "6.1.3"
        QTest.qWait(200)

        class Links(QObject):
            def __init__(self):
                super().__init__()
                self.urls = []

            @Slot(QUrl)
            def capture(self, url):
                self.urls.append(url.toString())

        links = Links()
        QDesktopServices.setUrlHandler("https", links, "capture")
        try:
            author_link = find(dialog, "aboutAuthorInfoButton")
            QMetaObject.invokeMethod(author_link, "clicked")
            assert links.urls[-1] == "https://github.com/goulielmos-linux"
            assert dialog.findChild(QObject, "aboutAuthorDialog") is None
            for library in engine._about_info.libraries:
                link = find(dialog, "aboutLibraryLicenseButton_" + library["name"])
                assert link.property("text") == library["name"]
                QMetaObject.invokeMethod(link, "clicked")
                assert links.urls[-1] == library["licenseUrl"]
        finally:
            QDesktopServices.unsetUrlHandler("https")
        copy_button = find(dialog, "aboutCopyInformationButton")
        QMetaObject.invokeMethod(copy_button, "clicked")
        copied = app.clipboard().text()
        assert "Python " in copied and "Podman 6.1.3" in copied
        assert engine._about_info.environment[0]["name"].endswith("(offscreen)")
        with patch("multipak.kirigami.about.host.is_flatpak", return_value=False):
            assert not engine._about_info.flatpak
            assert "nativa" in engine._about_info.environment[0]["name"]
        with patch("multipak.kirigami.about.host.is_flatpak", return_value=True):
            assert engine._about_info.flatpak
            assert "Flatpak" in engine._about_info.environment[0]["name"]
        scroll = dialog.property("scrollContent")
        assert (
            abs(
                scroll.property("rightPadding")
                - scroll.property("effectiveScrollBarWidth")
                - scroll.property("leftPadding")
            )
            < 1
        )
        assert dialog.findChild(QObject, "aboutWaydroidDialog") is None
        assert dialog.grabWindow().save(str(output / "about-top.png"))
        viewport = dialog.property("scrollContent").property("contentItem")
        close = find(dialog, "informationCloseButton")
        assert viewport.property("contentHeight") > viewport.height()
        assert close.isVisible()
        assert dialog.height() <= dialog.property("preferredMaximumHeight")
        viewport.setProperty("contentY", viewport.property("contentHeight") - viewport.height())
        QTest.qWait(100)
        assert dialog.grabWindow().save(str(output / "about-backends.png"))
        # A longer author list must grow the scrollable content, not the window.
        previous_height = dialog.height()
        with patch(
            "multipak.kirigami.about.AUTHORS",
            [(f"Author {i}", "https://github.com/goulielmos-linux") for i in range(40)],
        ):
            engine._about_info.changed.emit()
            QTest.qWait(150)
            assert dialog.height() == previous_height
            assert viewport.property("contentHeight") > dialog.height() * 2
            dialog.setProperty("preferredMaximumHeight", 420)
            QTest.qWait(150)
            assert dialog.height() <= 420
            assert close.isVisible()
            assert close.mapToScene(close.boundingRect().center()).y() < dialog.height()
        dialog.close()
        window.close()
        engine.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        missing = AboutInfo([])
        with patch("multipak.kirigami.about.host.which", return_value=None):
            missing.refresh()
        assert all(row["version"] == "Não instalado" for row in missing.backends)
    print("About links, clipboard, environment, scrolling and backend probes verified.")


if __name__ == "__main__":
    main()

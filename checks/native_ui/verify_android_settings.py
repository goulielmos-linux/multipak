"""Exercise the companion window without changing or restarting any real Android instance."""

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

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.android_settings import AndroidSettings, create_window  # noqa: E402
from multipak.store import Store  # noqa: E402


def wait(controller):
    for _ in range(200):
        QTest.qWait(10)
        if not controller.busy:
            return
    raise AssertionError("worker did not finish")


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    state = {
        "touch": False,
        "keyboard": False,
        "navigation": False,
        "arrows": False,
        "label": "MultiPak-Android-13",
    }
    calls = []

    def configure(_runner, identifier, package, **options):
        assert identifier == "androida" and package == "com.sega.sonicdash"
        calls.append(options)
        changed = state["touch"] != options.get("touch", state["touch"])
        state.update(options)
        return {**state, "touch_changed": changed}

    def reopen(_runner, identifier, package, progress):
        assert identifier == "androida" and package == "com.sega.sonicdash"
        calls.append("reopen")
        progress(-1, "Reiniciando…")
        return dict(state)

    with (
        tempfile.TemporaryDirectory() as directory,
        patch("multipak.android_settings.configure", configure),
        patch("multipak.android_settings.reopen", reopen),
    ):
        controller = AndroidSettings(Store(Path(directory)), "androida", "com.sega.sonicdash")
        controller.name = "Sonic Dash"
        engine, window = create_window(app, controller)
        try:
            controller.refresh()
            wait(controller)
            assert controller.ready and not controller.keyboard and not controller.touch
            touch = window.findChild(QObject, "androidMouseTouch")
            keyboard = window.findChild(QObject, "androidVirtualKeyboard")
            arrows = window.findChild(QObject, "androidArrowGestures")
            assert arrows is not None and not arrows.property("checked")
            assert not touch.property("checked") and not keyboard.property("checked")
            touch.setProperty("checked", True)
            keyboard.setProperty("checked", True)
            arrows.setProperty("checked", True)
            controller.save(
                touch.property("checked"), keyboard.property("checked"), arrows.property("checked")
            )
            wait(controller)
            assert state["touch"] and state["keyboard"] and state["arrows"]
            assert "Reabra" in controller.status
            output = PROJECT.parent / "work/android-mode-ui/android-app-settings.png"
            output.parent.mkdir(exist_ok=True)
            QTest.qWait(350)
            assert window.grabWindow().save(str(output))
            controller.reopen()
            wait(controller)
            assert calls[-1] == "reopen"
            assert "reaberto" in controller.status
            controller.save(False, False, False)
            wait(controller)
            assert not state["touch"] and not state["keyboard"]
            assert window.isVisible()
            print("Companion: defaults, save, reopen, translations and window lifecycle verified.")
        finally:
            controller.shutdown()
            window.close()
            engine.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    main()

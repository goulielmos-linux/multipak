"""Small window that asks which program opens an address in conflict (Android app links)."""

import os
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QStyleFactory

from multipak import android_link_choice as choice
from multipak.localization import tr

from .identity import configure_application
from .languages import Languages
from .shutdown import release_engine


class LinkChoice(QObject):
    changed = Signal()

    def __init__(self, uri, programs):
        super().__init__()
        self._uri, self._programs, self._error = uri, programs, ""

    @Property(str, constant=True)
    def address(self):
        return self._uri

    @Property("QVariantList", constant=True)
    def programs(self):
        return [{"name": name, "icon": icon} for _, _, name, icon in self._programs]

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Slot(int, result=bool)
    def open(self, index):
        try:
            choice.launch(self._programs[index][1], self._uri)
        except Exception as error:
            self._error = tr("Could not open the address: {0}").format(error)
            self.changed.emit()
            return False
        return True


def main():
    uri = sys.argv[1]
    programs = choice.handlers(choice.scheme_of(uri) or "")
    if len(programs) == 1:  # The conflict is gone: open without asking.
        choice.launch(programs[0][1], uri)
        return 0
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = QApplication(["io.github.multipak.installer"])
    if "breeze" in (key.lower() for key in QStyleFactory.keys()):
        app.setStyle("Breeze")
    configure_application(app)
    engine = QQmlApplicationEngine(app)
    engine._languages = Languages(app)
    controller = LinkChoice(uri, programs)
    controller.setParent(engine)
    engine.rootContext().setContextProperty("linkChoice", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("LinkChoice.qml"))))
    if not engine.rootObjects() or not isinstance(engine.rootObjects()[0], QQuickWindow):
        raise RuntimeError(tr("Could not load the window."))
    window = engine.rootObjects()[0]
    window.setIcon(app.windowIcon())
    try:
        return app.exec()
    finally:
        release_engine(app, engine, window)

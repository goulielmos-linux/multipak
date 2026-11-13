"""Nonblocking companion window for applications in owned Android instances."""

import argparse
import hashlib
import os
from pathlib import Path

from PySide6.QtCore import Property, QEvent, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QStyleFactory

from multipak import android_settings as settings
from multipak.localization import tr
from multipak.store import Store
from multipak.worker import Worker

from .identity import configure_application
from .languages import Languages


class AndroidSettings(QObject):
    changed = Signal()

    def __init__(self, store, identifier, package, *, worker_factory=Worker):
        super().__init__()
        self.store, self.identifier, self.package = store, identifier, package
        self.worker_factory = worker_factory
        self.worker = None
        self._ready = False
        self._touch = self._keyboard = self._arrows = False
        self._label = identifier
        self._status = ""
        self._error = False
        self._operation = ""
        self.name = next(
            (
                tx.package_name
                for tx in store.transactions()
                if tx.android_instance_id == identifier
                and tx.android_package_id == package
                and tx.status == "installed"
            ),
            package,
        )

    @Property(str, constant=True)
    def applicationName(self):
        return self.name

    @Property(str, notify=changed)
    def containerName(self):
        return self._label

    @Property(bool, notify=changed)
    def busy(self):
        return self.worker is not None

    @Property(bool, notify=changed)
    def ready(self):
        return self._ready

    @Property(bool, notify=changed)
    def touch(self):
        return self._touch

    @Property(bool, notify=changed)
    def keyboard(self):
        return self._keyboard

    @Property(bool, notify=changed)
    def arrows(self):
        return self._arrows

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def hasError(self):
        return self._error

    def _start(self, operation, callback):
        if self.busy:
            return
        self._operation = operation
        self._error = False
        self._status = tr("Applying preferences…") if operation == "save" else ""
        worker = self.worker_factory(self.store, callback, self)
        self.worker = worker
        worker.succeeded.connect(self._succeeded)
        worker.failed.connect(self._failed)
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        self.changed.emit()
        worker.start()

    @Slot()
    def refresh(self):
        self._start(
            "read", lambda engine: settings.configure(engine.runner, self.identifier, self.package)
        )

    @Slot(bool, bool, bool)
    def save(self, touch, keyboard, arrows):
        if not self.ready:
            return
        self._start(
            "save",
            lambda engine: settings.configure(
                engine.runner,
                self.identifier,
                self.package,
                touch=touch,
                keyboard=keyboard,
                arrows=arrows,
            ),
        )

    @Slot()
    def reopen(self):
        if not self.ready:
            return
        self._start(
            "reopen",
            lambda engine: settings.reopen(
                engine.runner, self.identifier, self.package, engine.progress
            ),
        )

    @Slot(object)
    def _succeeded(self, result):
        self._touch, self._keyboard = result["touch"], result["keyboard"]
        self._arrows = result["arrows"]
        self._label = result["label"]
        self._ready = True
        self._status = ""
        if self._operation == "save":
            self._status = (
                tr("Preferences saved. Reopen the application to apply mouse input changes.")
                if result.get("touch_changed")
                else tr("Preferences applied.")
            )
        elif self._operation == "reopen":
            self._status = tr("The application was reopened.")
        self.changed.emit()

    @Slot(str, object)
    def _failed(self, message, _transaction):
        self._error = True
        self._status = tr("Could not change the Android preferences: {0}").format(message)
        self.changed.emit()

    @Slot(int, str)
    def _progress(self, _value, message):
        self._status = message
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.changed.emit()

    @Slot()
    def shutdown(self):
        if self.worker is not None:
            self.worker.cancel_requested.set()
            self.worker.wait()


def create_window(app, controller):
    configure_application(app, manager=True)
    engine = QQmlApplicationEngine(app)
    languages = Languages(app)
    engine._languages = languages
    controller.setParent(engine)
    engine.rootContext().setContextProperty("androidSettings", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("AndroidSettings.qml"))))
    if not engine.rootObjects() or not isinstance(engine.rootObjects()[0], QQuickWindow):
        raise RuntimeError(tr("Could not load the Android settings window."))
    window = engine.rootObjects()[0]
    window.setIcon(app.windowIcon())
    app.aboutToQuit.connect(controller.shutdown)
    return engine, window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = QApplication(["io.github.multipak.android-settings"])
    if "breeze" in (key.lower() for key in QStyleFactory.keys()):
        app.setStyle("Breeze")
    digest = hashlib.sha256(f"{os.getuid()}:{args.instance}:{args.package}".encode()).hexdigest()
    server_name = "multipak-input-" + digest
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(250):
        socket.write(b"activate")
        socket.waitForBytesWritten(250)
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(server_name):
        return 1
    controller = AndroidSettings(Store(), args.instance, args.package)
    engine, window = create_window(app, controller)

    def activate():
        connection = server.nextPendingConnection()
        if connection:
            connection.close()
            connection.deleteLater()
        window.showNormal()
        window.raise_()
        window.requestActivate()

    server.newConnection.connect(activate)
    QTimer.singleShot(0, controller.refresh)
    result = app.exec()
    window.close()
    server.close()
    engine.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return result

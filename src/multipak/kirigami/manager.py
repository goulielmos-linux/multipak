"""Native application manager; journals and container operations stay in the core."""

import argparse
import os
import sys
from pathlib import Path
from threading import Lock

from PySide6.QtCore import (
    Property,
    QEvent,
    QObject,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QStyleFactory

from multipak import DISPLAY_VERSION, host
from multipak.containers import ManagedContainer, remove_container, uninstall_application
from multipak.environments import discover
from multipak.file_associations import FORMATS, FileAssociations
from multipak.localization import tr
from multipak.models import ExistingEnvironment, Transaction
from multipak.store import Store
from multipak.system import (
    SYSTEM_REMOVABLE_STATUSES,
    system_removal_states,
    uninstall_system,
)
from multipak.updates import update_container
from multipak.worker import Worker

from .about import bind_about, license_text
from .identity import configure_application
from .languages import bind_languages
from .legacy_migration import bind_legacy_migration
from .manager_icons import GENERIC_ICON, application_icon
from .update_notice import bind_update_notice, installation_allowed


class Manager(QObject):
    changed = Signal()
    rowsChanged = Signal()
    confirmationRequested = Signal()
    containerRemovalRequested = Signal()
    associationSettingsRequested = Signal()
    associationsSaved = Signal()
    languageSettingsRequested = Signal()
    companionWarning = Signal(str)

    def __init__(
        self,
        store,
        parent=None,
        *,
        worker_factory=Worker,
        discoverer=discover,
        updater=update_container,
        preview=False,
        association_settings=None,
    ):
        super().__init__(parent)
        self.store = store
        self.worker_factory = worker_factory
        self.discoverer = discoverer
        self.updater = updater
        self.preview = preview
        self.association_settings = association_settings or FileAssociations()
        self._associations = {}
        self._associations_ready = False
        self.worker = None
        self._items = []
        self._mode = "home"
        self._selected = -1
        self._status = self._logs = ""
        self._error = False
        self._progress = -1
        self._pending = None
        self._outcome = (False, tr("The operation was not completed."))
        self._success = None
        self._removal_method = ""
        self._system_states = {}
        self._unused_container = None
        self._container_error = ""
        self._remove_container_home = False
        # Long container updates print many lines. Batch them so each line does not
        # trigger a full interface refresh, as the installer already does.
        self._pending_logs = ""
        self._log_lock = Lock()
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(150)
        self._log_timer.timeout.connect(self._flush_logs)

    @Slot()
    def openInstaller(self):
        if self.busy or not installation_allowed(self):
            return
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            tr("Install applications"),
            str(Path.home()),
            tr("Application packages") + " (*.rpm *.deb *.pkg.tar *.pkg.tar.zst "
            "*.pkg.tar.xz *.pkg.tar.gz *.pkg.tar.bz2 *.AppImage *.appimage);;"
            + tr("All files")
            + " (*)",
        )
        if not path:
            return
        if self.preview:
            self._status = tr("This is a preview. No application was opened.")
            self.changed.emit()
            return
        bootstrap = str(Path(__file__).with_name("bootstrap.py"))
        result = QProcess.startDetached(sys.executable, [bootstrap, path])
        if not (result[0] if isinstance(result, tuple) else result):
            self.companionWarning.emit(tr("Could not open the application installer."))

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(bool, constant=True)
    def previewMode(self):
        return self.preview

    @Property(bool, notify=changed)
    def busy(self):
        return self.worker is not None

    @Property(bool, notify=changed)
    def canClose(self):
        return not self.busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def hasError(self):
        return self._error

    @Property(str, notify=changed)
    def logs(self):
        return self._logs

    @Property(int, notify=changed)
    def progress(self):
        return self._progress

    @Property(int, notify=changed)
    def selectedIndex(self):
        return self._selected

    @Property(bool, notify=changed)
    def canAct(self):
        if self.busy or not 0 <= self._selected < len(self._items):
            return False
        item = self._items[self._selected]
        return (
            self._mode != "remove"
            or item.method != "system"
            or self._system_states.get(item.id) == "installed"
        )

    @Property(str, notify=changed)
    def confirmationName(self):
        return self._pending.package_name if self._pending else ""

    @Property(str, notify=changed)
    def removalExplanation(self):
        if self._pending and self._pending.method == "system":
            return tr(
                "rpm-ostree will remove the package and recalculate the required "
                "dependencies. A restart will be needed to finish. Your personal data "
                "will be preserved."
            )
        if self._pending and self._pending.method == "appimage":
            return tr(
                "The managed AppImage copy and its shortcuts will be removed. Personal "
                "data will be preserved."
            )
        return tr("The container, other applications and personal data will be preserved.")

    @Property(str, notify=changed)
    def unusedContainerName(self):
        return self._unused_container.name if self._unused_container else ""

    @Property(str, notify=changed)
    def containerRemovalError(self):
        return self._container_error

    @Property(bool, notify=changed)
    def canRemoveContainerHome(self):
        return bool(self._unused_container and self._unused_container.managed_home)

    @Property(bool, notify=changed)
    def removeContainerHome(self):
        return self._remove_container_home

    @Slot(bool)
    def setRemoveContainerHome(self, value):
        if not self.busy and self.canRemoveContainerHome:
            self._remove_container_home = value
            self.changed.emit()

    @Slot()
    def keepContainer(self):
        if not self.busy:
            self._unused_container, self._container_error = None, ""
            self._remove_container_home = False
            self.changed.emit()

    @Slot()
    def confirmContainerRemoval(self):
        if self.busy or self._unused_container is None:
            return
        if self.preview:
            self._status = tr("This is a preview. No container was removed.")
            self.keepContainer()
            return
        container = self._unused_container
        remove_home = self._remove_container_home
        self._container_error = ""
        self._status = tr("Removing container {0}…").format(container.name)
        self._error = False
        self._run(
            lambda engine: remove_container(engine, container, remove_home=remove_home),
            self._container_removed,
            self._container_removal_failed,
        )

    def _container_removed(self, _result):
        self._unused_container, self._container_error = None, ""
        self._status = (
            tr("Container and its home folder removed.")
            if self._remove_container_home
            else tr("Container removed. Its home folder was preserved.")
        )
        self._remove_container_home = False
        self._progress = 100

    def _container_removal_failed(self, error):
        self._container_error = tr("Could not remove the container: {0}").format(error)
        self._status, self._error = self._container_error, True
        self.containerRemovalRequested.emit()

    @Property(str, constant=True)
    def version(self):
        return DISPLAY_VERSION

    @Property(str, constant=True)
    def licenseText(self):
        return license_text()

    @Property("QVariantList", notify=rowsChanged)
    def rows(self):
        if self._mode == "remove":
            return [
                {
                    "title": tx.package_name,
                    "icon": GENERIC_ICON if self.preview else application_icon(self.store, tx),
                    "subtitle": tr("Version: {0} · {1}").format(
                        (
                            tr("Not provided")
                            if tx.package_kind == "appimage"
                            and tx.package_version == "Not provided"
                            else tx.package_version
                        ),
                        tx.package_kind.upper(),
                    ),
                    "detail": (
                        tr("Installed directly on the system")
                        if tx.method == "system"
                        else tr("AppImage managed by MultiPak")
                        if tx.method == "appimage"
                        else tr("Container: {0}").format(tx.container)
                    ),
                    "note": (
                        {
                            "install_pending": tr("Installation pending. Restart to apply."),
                            "removal_pending": tr("Uninstallation pending. Restart to finish."),
                            "blocked": tr(
                                "There are pending system changes. Restart before uninstalling."
                            ),
                            "unavailable": tr("Could not query the system status."),
                        }.get(self._system_states.get(tx.id), "")
                        if tx.method == "system"
                        else tr("Removal pending")
                        if tx.status == "cleanup_failed"
                        else ""
                    ),
                }
                for tx in self._items
            ]
        return [
            {
                "title": env.display_name,
                "subtitle": f"{env.family.title()} {env.version}".strip(),
                "detail": env.name,
                "note": "",
            }
            for env in self._items
        ]

    @Slot(int)
    def select(self, index):
        if not self.busy and -1 <= index < len(self._items) and index != self._selected:
            self._selected = index
            self.changed.emit()

    def _removable(self):
        return sorted(
            (
                tx
                for tx in self.store.transactions()
                if tx.method in ("distrobox", "appimage")
                and tx.status in ("installed", "cleanup_failed")
            ),
            key=lambda tx: (tx.package_name.casefold(), tx.container.casefold(), tx.id),
        )

    def _load_removals(self, engine):
        transactions = self.store.transactions()
        system = [
            tx
            for tx in transactions
            if tx.method == "system"
            and tx.package_kind == "rpm"
            and tx.status in SYSTEM_REMOVABLE_STATUSES
        ]
        states, error = {}, ""
        if system:
            try:
                states = system_removal_states(engine, system)
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                states = {tx.id: "unavailable" for tx in system}
                error = tr("Could not query direct installations: ") + str(exc)
        items = self._removable() + [tx for tx in system if states.get(tx.id) != "absent"]
        items.sort(key=lambda tx: (tx.package_name.casefold(), tx.container, tx.id))
        return items, states, error

    @Property("QVariantList", notify=changed)
    def formatOptions(self):
        return [
            {"id": key, "label": tr(label), "selected": self._associations.get(key, False)}
            for key, (label, _) in FORMATS.items()
        ]

    @Property(bool, notify=changed)
    def associationsReady(self):
        return self._associations_ready

    @Slot()
    def initializeAssociations(self):
        if self.busy or self.preview:
            return
        self._status, self._error = tr("Loading preferences…"), False
        self._run(
            lambda engine: self.association_settings.initialize_defaults(),
            self._initialized_associations,
        )

    def _initialized_associations(self, _changed):
        self._status = ""

    @Slot()
    def openAssociations(self):
        if self.busy:
            return
        self._mode, self._items, self._selected = "associations", [], -1
        self._pending = None
        self._status, self._logs, self._error = tr("Loading preferences…"), "", False
        self._associations_ready = False
        self.associationSettingsRequested.emit()
        self._run(
            lambda engine: (
                {key: True for key in FORMATS} if self.preview else self.association_settings.load()
            ),
            self._loaded_associations,
        )

    def _loaded_associations(self, selected):
        self._associations, self._associations_ready = selected, True
        self._status = ""

    @Slot(str, bool)
    def setAssociation(self, key, selected):
        if not self.busy and self._associations_ready and key in FORMATS:
            self._associations[key] = selected
            self.changed.emit()

    @Slot()
    def saveAssociations(self):
        if self.busy or self._mode != "associations" or not self._associations_ready:
            return
        if self.preview:
            self._status = tr("This is a preview. No associations were changed.")
            self.changed.emit()
            self.associationsSaved.emit()
            return
        selected = dict(self._associations)
        self._status, self._logs, self._error = tr("Saving preferences…"), "", False
        self._run(
            lambda engine: self.association_settings.apply(selected), self._saved_associations
        )

    def _saved_associations(self, selected):
        self._associations = selected
        self.associationsSaved.emit()
        self._status = tr("Preferences saved.")

    @Slot()
    def openLanguages(self):
        if self.busy:
            return
        self._mode, self._items, self._selected = "languages", [], -1
        self._status, self._logs, self._error = "", "", False
        self.languageSettingsRequested.emit()
        self.changed.emit()

    @Slot(str)
    def openList(self, mode):
        if self.busy or mode not in ("remove", "update"):
            return
        self._mode, self._selected, self._items = mode, -1, []
        self._pending = None
        self.rowsChanged.emit()
        self._status, self._logs, self._error = tr("Loading…"), "", False
        if mode == "remove":
            self._run(self._load_removals, self._loaded_removals)
        else:

            def operation(engine):
                environments = self.discoverer(engine.runner)
                return sorted(
                    (env for env in environments if env.multipak_managed or env.custom_named),
                    key=lambda env: env.name.casefold(),
                )

            self._run(operation, self._loaded_environments)

    def _loaded_removals(self, result):
        self._items, self._system_states, error = result
        self._error = bool(error)
        self._status = error or (
            "" if self._items else tr("No applications are available to uninstall.")
        )

    def _loaded_environments(self, items):
        self._items = items
        self._status = "" if items else tr("No MultiPak containers are available to update.")

    @Slot()
    def goHome(self):
        if not self.busy:
            self._mode, self._items, self._selected = "home", [], -1
            self._status, self._logs, self._error = "", "", False
            self._pending = None
            self.rowsChanged.emit()
            self.changed.emit()

    @Slot()
    def refresh(self):
        self.openList(self._mode)

    @Slot()
    def requestRemoval(self):
        if self.canAct and self._mode == "remove":
            self._pending = self._items[self._selected]
            self.changed.emit()
            self.confirmationRequested.emit()

    @Slot()
    def cancelRemoval(self):
        self._pending = None
        self.changed.emit()

    @Slot()
    def confirmRemoval(self):
        if self.busy or self._mode != "remove" or self._pending is None:
            return
        transaction, self._pending = self._pending, None
        self._removal_method = transaction.method
        if self.preview:
            self._status = tr("This is a preview. No application was removed.")
            self.changed.emit()
            return
        self._status = tr("Uninstalling {0}… Please wait.").format(transaction.package_name)
        self._logs, self._error = "", False
        self._run(
            (lambda engine: uninstall_system(engine, transaction))
            if transaction.method == "system"
            else (lambda engine: uninstall_application(engine, transaction)),
            self._removed,
        )

    def _removed(self, result):
        if self._removal_method == "system":
            if not isinstance(result, Transaction) or result.status not in (
                "system_remove_pending",
                "system_removed",
            ):
                raise RuntimeError(tr("rpm-ostree did not confirm uninstallation."))
            state = "removal_pending" if result.status == "system_remove_pending" else "absent"
            for item in self._items:
                if item.method == "system" and item.package_name == result.package_name:
                    self._system_states[item.id] = state
            self._items = [
                item for item in self._items if self._system_states.get(item.id) != "absent"
            ]
            self._status = (
                tr("Restart your computer to complete the uninstallation.")
                if result.status == "system_remove_pending"
                else tr("Direct installation request removed by rpm-ostree.")
            )
        else:
            remaining = {tx.id for tx in self._removable()}
            self._items = [
                item for item in self._items if item.method == "system" or item.id in remaining
            ]
            self._status = (
                tr("AppImage uninstalled. Personal data was preserved.")
                if self._removal_method == "appimage"
                else tr("Application uninstalled. The container and personal data were preserved.")
            )
        self._selected = -1
        if isinstance(result, ManagedContainer):
            self._unused_container, self._container_error = result, ""
            self._remove_container_home = False
            self.changed.emit()
            self.containerRemovalRequested.emit()

    @Slot()
    def updateSelected(self):
        if not self.canAct or self._mode != "update":
            return
        self._logs, self._error = "", False
        if self.preview:
            self._status = tr("This is a preview. No update was started.")
            self.changed.emit()
            return
        name = self._items[self._selected].name
        self._status = tr("Updating {0}… Please wait.").format(name)
        self._run(lambda engine: self.updater(engine, name), self._updated)

    def _updated(self, _result):
        self._progress = 100
        self._status = tr(
            "Update complete. All applications and packages in the container are up to date."
        )

    @Slot()
    def requestClose(self):
        if self.busy:
            self._status = tr("Wait for the operation to finish before closing the manager.")
            self.changed.emit()

    def _run(self, operation, success, failure=None):
        self._progress = -1
        self._outcome = (False, tr("The operation was not completed."))
        self._success = success
        self._failure = failure
        self.worker = self.worker_factory(self.store, operation, self)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._set_progress)
        self.worker.log.connect(self._queue_log, Qt.DirectConnection)
        self.worker.finished.connect(self._finished)
        self.changed.emit()
        self._log_timer.start()
        self.worker.start()

    @Slot(object)
    def _succeeded(self, value):
        self._outcome = (True, value)

    @Slot(str, object)
    def _failed(self, message, _transaction):
        self._outcome = (False, message)

    @Slot(int, str)
    def _set_progress(self, percent, text):
        self._progress, self._status = percent, text
        self.changed.emit()

    @Slot(str)
    def _append_log(self, text):
        self._logs = (self._logs + text + "\n")[-200_000:]
        self.changed.emit()

    @Slot(str)
    def _queue_log(self, text):
        # Called on the worker thread: no QML or QObject mutations here.
        with self._log_lock:
            self._pending_logs = (self._pending_logs + text + "\n")[-200_000:]

    @Slot()
    def _flush_logs(self):
        with self._log_lock:
            pending, self._pending_logs = self._pending_logs, ""
        if pending:
            self._append_log(pending.removesuffix("\n"))

    @Slot()
    def _finished(self):
        self._log_timer.stop()
        self._flush_logs()
        worker, self.worker = self.worker, None
        worker.deleteLater()
        ok, value = self._outcome
        if ok:
            try:
                self._success(value)
            except Exception as exc:
                self._status, self._error = tr("Could not refresh the list: ") + str(exc), True
        elif self._failure is not None:
            self._failure(str(value))
        else:
            self._status, self._error = tr("Could not complete: ") + str(value), True
        self.rowsChanged.emit()
        self.changed.emit()


def create_manager(app, controller):
    configure_application(app, manager=True)
    engine = QQmlApplicationEngine(app)
    languages = bind_languages(app, engine, controller, manager=True)
    bind_about(engine, languages)
    migration = bind_legacy_migration(engine, controller, preview=controller.preview)
    bind_update_notice(engine, controller, preview=controller.preview, migration=migration)
    controller.setParent(engine)
    engine.rootContext().setContextProperty("manager", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Manager.qml"))))
    if not engine.rootObjects() or not isinstance(engine.rootObjects()[0], QQuickWindow):
        raise RuntimeError(tr("Could not load the Kirigami manager."))
    window = engine.rootObjects()[0]
    window.setIcon(app.windowIcon())
    if host.is_flatpak() and not controller.preview:
        migration.when_ready(controller.initializeAssociations)
    return engine, controller, window


def create_preview_manager(app):
    class PreviewStore:
        def transactions(self):
            return [
                Transaction(
                    "a" * 32,
                    "MultiPak-Fedora-45",
                    "image",
                    "Strawberry",
                    "1.2.31",
                    "rpm",
                    "hash",
                    status="installed",
                ),
                Transaction(
                    "b" * 32,
                    "MultiPak-Ubuntu-26.04",
                    "image",
                    "Google Chrome",
                    "142.0.7444.59",
                    "deb",
                    "hash",
                    status="installed",
                ),
            ]

    controller = Manager(
        PreviewStore(),
        preview=True,
        discoverer=lambda runner: (
            ExistingEnvironment("MultiPak-Fedora-45", "fedora", "fedora", "45"),
            ExistingEnvironment("MultiPak-Ubuntu-26.04", "ubuntu", "ubuntu", "26.04"),
        ),
    )
    return create_manager(app, controller)


def main():
    parser = argparse.ArgumentParser(description=tr("Application Manager — MultiPak"))
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--page", choices=("home", "remove", "update"), default="home")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.screenshot and not args.preview:
        parser.error("Use --preview to capture a screen with sample data.")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = QApplication(["io.github.multipak.manager"])
    if "breeze" in (key.lower() for key in QStyleFactory.keys()):
        app.setStyle("Breeze")
    engine, controller, window = (
        create_preview_manager(app) if args.preview else create_manager(app, Manager(Store()))
    )
    if args.page != "home":
        controller._legacy_migration.when_ready(lambda: controller.openList(args.page))
    window.requestActivate()
    if args.screenshot:

        def capture():
            if controller.busy:
                QTimer.singleShot(100, capture)
            else:
                app.exit(0 if window.grabWindow().save(str(args.screenshot)) else 1)

        QTimer.singleShot(1000, capture)
    result = app.exec()
    window.close()
    engine.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())

"""Run package operations for the MultiPak installation assistant."""

from pathlib import Path
from threading import Lock

from PySide6.QtCore import Property, QProcess, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from multipak.catalog import load_catalog, package_environment
from multipak.distrobox_setup import (
    DistroboxMissing,
    DistroboxRequirement,
    distrobox_requirement,
    install_distrobox,
    reboot_after_distrobox,
)
from multipak.environments import container_names, discover
from multipak.file_associations import FileAssociations
from multipak.localization import tr
from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS, PackageInspection, Transaction
from multipak.process import Cancelled
from multipak.software_store import search_command
from multipak.store import Store
from multipak.suggestions import suggestion_details
from multipak.system import install_system, system_install_available
from multipak.worker import Worker

from .main import Welcome
from .update_notice import installation_allowed


def inspect_installation(engine, path):
    # File-type preferences are a convenience. A failure here must never prevent
    # the package from being opened.
    try:
        FileAssociations().initialize_defaults()
    except Exception as error:
        engine._log(tr("Could not update file associations: {0}").format(error))
    package = inspect_package(path, engine.runner)
    if package.kind == "appimage":
        report = appimage_dependencies(engine, package)
        if report is None or not report.required:
            return PackageInspection(package, dependencies=report)
        existing, profiles, name = appimage_environment(engine)
        return PackageInspection(
            package, existing, profiles, dependencies=report, container_names=(name,)
        )
    profiles = load_catalog(engine.runner, engine.store.root / "image-catalog.json")
    try:
        existing = tuple(discover(engine.runner, profiles))
        names = container_names(engine.runner)
    except Cancelled:
        raise
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        engine._log(tr("Could not query existing environments: {0}").format(error))
        existing, names = (), ()
    return PackageInspection(package, existing, profiles, container_names=names)


def appimage_dependencies(engine, package):
    """Reads the AppImage without running it. A failed check never blocks installation."""
    from multipak.appimage import Archive
    from multipak.appimage_dependencies import analyze

    try:
        return analyze(
            Archive(package.path, engine.runner), engine.runner, scratch=engine.store.root
        )
    except Cancelled:
        raise
    except Exception as error:
        engine._log(tr("Could not check the AppImage dependencies: {0}").format(error))
        return None


def appimage_environment(engine):
    """The catalog's current Fedora and MultiPak's AppImage container, if it exists.

    Both only describe the installation on screen; failures are logged, never raised.
    """
    from multipak.appimage_container import CONTAINER_NAME, update_entry
    from multipak.environment_choices import free_container_name

    profiles = ()
    try:
        profiles = load_catalog(engine.runner, engine.store.root / "image-catalog.json")
    except Cancelled:
        raise
    except Exception as error:
        engine._log(tr("Could not query existing environments: {0}").format(error))
    existing = ()
    try:
        entry = update_entry(engine.store)
        if entry is not None:
            engine.runner.run(["podman", "container", "exists", entry.container_id], timeout=30)
            existing = (entry,)
    except Cancelled:
        raise
    except Exception:
        existing = ()  # Missing or unreadable: it will be created.
    if not existing:
        # Shown as the name it will get: the default one or the lowest free C2-, C3-...
        try:
            names = container_names(engine.runner)
        except Cancelled:
            raise
        except Exception:
            names = ()
        existing = ()
        planned = free_container_name(CONTAINER_NAME, names)
        return (), profiles, planned
    return existing, profiles, existing[0].name


class LiveInstaller(Welcome):
    operationChanged = Signal()
    logsChanged = Signal()

    def __init__(self, path, *, store=None, worker_factory=Worker, inspector=inspect_installation):
        super().__init__()
        self.is_live = True
        self.path = Path(path)
        self.store = store if store is not None else Store()
        self.worker_factory = worker_factory
        self.inspector = inspector
        self.package = None
        self._state = "loading"
        self._status = tr("Loading package information…")
        self._percent = -1
        self._logs = ""
        self._pending_logs = ""
        self._log_lock = Lock()
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(150)
        self._log_timer.timeout.connect(self._flush_logs)
        self._mode = "inspect"
        self._outcome = None
        self._handler = None
        self._cancel_pending = False
        self._close_pending = False
        self.existing = ()
        self.profiles = ()

    @Property(str, notify=operationChanged)
    def status(self):
        return self._status

    @Property(int, notify=operationChanged)
    def progress(self):
        return self._percent

    @Property(str, notify=logsChanged)
    def logs(self):
        return self._logs

    @Property(bool, notify=operationChanged)
    def canCancel(self):
        return (
            self.worker is not None
            and self._mode not in ("cleanup", "system", "distrobox", "reboot")
            and not self._cancel_pending
        )

    @Property(bool, notify=operationChanged)
    def canRetry(self):
        return (
            self.state == "error"
            and self.worker is None
            and self._mode not in ("system", "distrobox", "reboot")
        )

    @Property(bool, notify=Welcome.dataChanged)
    def systemInstallAvailable(self):
        return self.kind == "RPM" and system_install_available()

    @Property(bool, notify=operationChanged)
    def canReturn(self):
        return (
            self.state == "error"
            and self.transaction is None
            and self.package is not None
            and self._mode != "inspect"
        )

    def _set_state(self, state):
        self._state = state
        self.dataChanged.emit()
        self.operationChanged.emit()
        self.stateChanged.emit()

    def _run(self, operation, handler, mode):
        if self.worker is not None:
            return
        self._mode, self._handler, self._outcome = mode, handler, None
        self._cancel_pending = False
        self._percent = -1 if mode in ("inspect", "search", "cleanup", "distrobox", "reboot") else 0
        self._status = {
            "inspect": tr("Loading package information…"),
            "search": tr("Opening the app store…"),
            "install": tr("Preparing installation…"),
            "system": tr("Preparing to install directly on your system…"),
            "cleanup": tr("Removing the components created by this installation…"),
            "distrobox": tr("Installing Distrobox using rpm-ostree…"),
            "reboot": tr("Requesting a system restart…"),
        }[mode]
        worker = self.worker_factory(self.store, operation, self)
        self.worker = worker
        worker.progress.connect(self._progress)
        # Bound output before it can build up a queue of GUI events.
        worker.log.connect(self._queue_log, Qt.DirectConnection)
        worker.succeeded.connect(self._success)
        worker.failed.connect(self._failure)
        worker.abandoned.connect(self._abandoned)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        self._set_state(
            {
                "inspect": "loading",
                "search": "searching",
                "cleanup": "cleaning",
                "install": "installing",
                "system": "installing",
                "distrobox": "installing",
                "reboot": "installing",
            }[mode]
        )
        self._log_timer.start()
        worker.start()

    @Slot()
    def inspect(self):
        self._run(lambda engine: self.inspector(engine, self.path), self._inspected, "inspect")

    def _inspected(self, inspection):
        self.package = inspection.package
        self.name, self.version = self.package.name, self.package.version
        self.kind = self.package.kind.upper()
        if self.isAppImage:
            from multipak.appimage_container import CONTAINER_NAME

            planned = (inspection.container_names or (CONTAINER_NAME,))[0]
            self.method, self.mode = "appimage", "standard"
            self.environment, self._selection = None, None
            self.set_dependency_report(inspection.dependencies)
            fedora = next(
                (
                    item
                    for item in (*inspection.environments, *ENVIRONMENTS)
                    if item.id == "fedora-latest"
                ),
                None,
            )
            self.set_appimage_environment(
                tr(fedora.title) if fedora is not None else "Fedora",
                bool(inspection.existing),
                planned,
            )
            self._set_state("ready")
            return
        self.existing = inspection.existing
        self.profiles = tuple(
            p for p in (inspection.environments or ENVIRONMENTS) if p.kind == self.package.kind
        )
        if not self.profiles:
            raise ValueError(tr("No compatible environment is available in the catalog."))
        self.environment = (
            package_environment(self.package.distro_hint, list(self.profiles)) or self.profiles[0]
        )
        self.configure(self.profiles, self.existing, self.environment, inspection.container_names)
        self.strength, explanation = suggestion_details(
            self.package, self.environment, self.environment.title
        )
        self.explanation = explanation
        self._set_state("ready")

    @Slot()
    def searchStore(self):
        if self.package is not None:
            self._run(
                lambda engine: search_command(engine.runner, self.package.name),
                self._open_store,
                "search",
            )

    def _open_store(self, command):
        from multipak import host

        invocation = host.command(command.argv, watch_bus=False)
        success, _pid = QProcess.startDetached(invocation[0], invocation[1:])
        if not success:
            raise RuntimeError(tr("Could not open the software store."))
        self._set_state("ready")
        if not command.search_supported:
            QApplication.clipboard().setText(command.term)
            self.notice.emit(
                tr(
                    "The app store is open. Search for “{0}”. The search term has "
                    "been copied to your clipboard."
                ).format(command.term)
            )

    @Slot()
    def requestInstallation(self):
        if (
            not self.installation_allowed()
            or not installation_allowed(self)
            or self.transaction is not None
            or self.worker is not None
        ):
            return
        package, selection = self.package, self._selection
        appimage_options = self.appimage_options()
        self._logs = ""
        self.logsChanged.emit()

        def install(engine):
            if package.kind == "appimage":
                try:
                    return engine.install_appimage(package, **appimage_options)
                except DistroboxMissing:
                    return distrobox_requirement(engine)
            try:
                if selection.custom_name:
                    return engine.install(
                        package, selection.environment, container_name=selection.custom_name
                    )
                return engine.install(package, selection.environment, selection.existing)
            except DistroboxMissing:
                return distrobox_requirement(engine)

        self._run(install, self._installed, "install")

    def _show_distrobox_requirement(self, requirement):
        self.transaction = None
        self._distrobox_can_install = requirement.can_install
        self._distrobox_restart_required = requirement.restart_required
        self._distrobox_message = requirement.message
        self._set_state("ready")
        if self._close_pending:
            self.closeRequested.emit()
        else:
            self.distroboxRequired.emit()

    @Slot()
    def requestDistroboxInstallation(self):
        if (
            not self.distroboxCanInstall
            or self.worker is not None
            or self.transaction is not None
            or not installation_allowed(self)
        ):
            return
        self._distrobox_can_install = False
        self._logs = ""
        self.logsChanged.emit()
        self._run(install_distrobox, self._distrobox_installed, "distrobox")

    def _distrobox_installed(self, restart_required):
        if type(restart_required) is not bool:
            raise RuntimeError(tr("Distrobox installation could not be confirmed."))
        message = (
            tr(
                "Distrobox was installed using rpm-ostree. Restart your computer, then "
                "reopen the package to install your application."
            )
            if restart_required
            else tr("Distrobox is available. You can continue installing the application.")
        )
        self._show_distrobox_requirement(DistroboxRequirement(False, restart_required, message))

    @Slot()
    def requestDistroboxRestart(self):
        if (
            not self.distroboxRestartRequired
            or self.worker is not None
            or self.transaction is not None
        ):
            return
        self._run(reboot_after_distrobox, self._distrobox_restart_requested, "reboot")

    def _distrobox_restart_requested(self, _result):
        self._set_state("ready")
        self.closeRequested.emit()

    @Slot(bool)
    def requestSystemInstallation(self, accepted):
        if (
            not accepted
            or not installation_allowed(self)
            or self.method != "system"
            or self.mode != "advanced"
            or self.package is None
            or self.worker is not None
            or self.transaction is not None
            or not self.systemInstallAvailable
        ):
            return
        package = self.package
        self._logs = ""
        self.logsChanged.emit()
        self._run(lambda engine: install_system(engine, package), self._system_installed, "system")

    def _system_installed(self, tx):
        if (
            not isinstance(tx, Transaction)
            or tx.method != "system"
            or tx.status != "system_pending"
        ):
            raise RuntimeError(
                tr("Direct installation ended without confirming the pending deployment.")
            )
        self.transaction = None
        self._result_title = tr("Restart required")
        self._result_message = tr(
            "{0} will be installed the next time you start your computer."
        ).format(self.name)
        self._result_note = tr("Restart your computer to complete the installation.")
        self._set_state("success")
        if self._close_pending:
            self.closeRequested.emit()

    def _installed(self, tx):
        if isinstance(tx, DistroboxRequirement):
            self._show_distrobox_requirement(tx)
            return
        if not isinstance(tx, Transaction) or tx.status != "installed":
            raise RuntimeError(
                tr("Installation ended without confirming that the package was installed.")
            )
        self.transaction = None
        self._result_title = tr("Installation completed")
        self._result_message = (
            tr("The AppImage was installed and added to the application launcher.")
            if tx.method == "appimage"
            else tr("The application was installed in the {0} environment.").format(
                self.environment.title
            )
        )
        self._result_note = (
            tr("You can now open it from the application launcher.")
            if tx.launchers
            else tr(
                "This package does not include an application shortcut. You can "
                "run it from a terminal inside the container."
            )
        )
        if tx.warnings:
            self._result_note += "\n\n" + "\n".join(tx.warnings)
        # Installed, but something it needs is still missing: shown as a warning.
        self._result_warning = bool(tx.appimage_missing)
        self._set_state("success")
        if self._close_pending:
            self.closeRequested.emit()

    @Slot(int, str)
    def _progress(self, percent, message):
        self._percent, self._status = percent, message
        self.operationChanged.emit()

    @Slot(str)
    def _log(self, text):
        self._logs = (self._logs + text.rstrip() + "\n")[-200000:]
        self.logsChanged.emit()

    @Slot(str)
    def _queue_log(self, text):
        # Called on the worker thread: no QML or QObject mutations here.
        with self._log_lock:
            self._pending_logs = (self._pending_logs + text.rstrip() + "\n")[-200000:]

    @Slot()
    def _flush_logs(self):
        with self._log_lock:
            pending, self._pending_logs = self._pending_logs, ""
        if pending:
            self._log(pending)

    @Slot(object)
    def _success(self, result):
        self._outcome = ("success", result)

    @Slot(str, object)
    def _failure(self, message, transaction):
        self._outcome = ("error", message, transaction)

    @Slot()
    def _abandoned(self):
        self._outcome = ("cancelled",)

    @Slot()
    def _finished(self):
        self._log_timer.stop()
        self._flush_logs()
        worker, outcome, handler = self.worker, self._outcome, self._handler
        self.worker = self._outcome = self._handler = None
        if outcome is None:
            outcome = ("error", tr("The operation ended without a result."), worker.engine.tx)
        if outcome[0] == "error":
            self._show_error(outcome[1], outcome[2])
        elif outcome[0] == "cancelled":
            self._cleaned(None)
        else:
            try:
                handler(outcome[1])
            except Exception as error:
                self._show_error(str(error), worker.engine.tx)

    def _show_error(self, message, transaction):
        system = self._mode in ("system", "distrobox", "reboot") or (
            transaction and transaction.method == "system"
        )
        self.transaction = None if system else transaction
        self._status = message.splitlines()[0][:400]
        if system:
            self._status += tr(
                "\n\nCheck the rpm-ostree status before trying again. No system deployment "
                "was automatically undone."
            )
        self._log(message)
        self._cancel_pending = self._close_pending = False
        self._set_state("error")

    @Slot()
    def retry(self):
        if not self.canRetry:
            return
        if self.transaction:
            if self.transaction.status == "cleanup_failed":
                self.abandon()
            else:
                if not installation_allowed(self):
                    return
                tx = self.transaction
                self._run(lambda engine: engine.retry(tx), self._installed, "install")
        elif self.package is None or self._mode == "inspect":
            self.inspect()
        elif self._mode == "search":
            self.searchStore()
        else:
            self.requestInstallation()

    @Slot()
    def returnToWelcome(self):
        if self.canReturn:
            self._set_state("ready")

    @Slot()
    def abandon(self):
        if self.worker is not None:
            if self.canCancel:
                self._cancel_pending = True
                self.worker.cancel_requested.set()
                self._status = tr("Waiting for the current step to finish before cleaning up…")
                self.operationChanged.emit()
        elif self.transaction is not None:
            tx = self.transaction
            self._run(lambda engine: engine.cleanup(tx), self._cleaned, "cleanup")
        else:
            self.closeRequested.emit()

    def _cleaned(self, _result):
        self.transaction = None
        self._result_title = tr("Installation cancelled")
        self._result_message = tr("The operation was cancelled.")
        self._result_note = (
            tr(
                "The managed copy and shortcuts for this installation were removed. The "
                "original file and your personal data were preserved."
            )
            if self.isAppImage
            else tr(
                "The components owned by this installation were removed. Shared "
                "environments and personal files were preserved."
            )
        )
        self._set_state("cancelled")
        if self._close_pending:
            self.closeRequested.emit()

    @Slot()
    def requestClose(self):
        if self.worker is not None and self._mode in ("system", "distrobox", "reboot"):
            self._close_pending = True
            self._status = tr(
                "Wait for rpm-ostree to finish. The window will close when the operation ends."
            )
            self.operationChanged.emit()
        elif not self.canClose:
            self._close_pending = True
            self.abandon()
        else:
            self.closeRequested.emit()

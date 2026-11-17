"""Run package operations for the MultiPak installation assistant."""

import dataclasses
from pathlib import Path
from threading import Lock

from PySide6.QtCore import Property, QProcess, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from multipak.android_translation import preview_plan
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
from multipak.models import (
    ENVIRONMENTS,
    PackageInspection,
    Transaction,
    package_profiles,
    slitaz_environment,
    void_environment,
)
from multipak.process import Cancelled
from multipak.software_store import search_command
from multipak.store import Store
from multipak.suggestions import suggestion_details
from multipak.system import install_system, system_install_available
from multipak.worker import Worker

from .main import Welcome
from .update_notice import installation_allowed


class SnapRequest(str):
    """A snap to download from the Snap Store, by name, with the channel the link
    asked for (empty: the latest stable)."""

    channel = ""

    def __new__(cls, name, channel=""):
        value = super().__new__(cls, name)
        value.channel = channel
        return value


def installer_target(argument):
    """What the installer was asked to open: a Path, or a Snap Store name to download.

    Launchers pass %u, so a local file may arrive as a file:// URL, and the Snap Store
    website's "View in Desktop store" button passes snap://NAME.
    """
    from urllib.parse import unquote, urlsplit

    from multipak.snap_store import store_channel, store_name

    if not isinstance(argument, str):
        return Path(argument)
    if argument.startswith("file://"):
        return Path(unquote(urlsplit(argument).path))
    name = store_name(argument)
    if name and not Path(argument).exists():
        return SnapRequest(name, store_channel(argument))
    return Path(argument)


def download_snap(engine, name, channel=""):
    """Download a snap from the Snap Store to inspect and install it; channel as
    track/risk, or empty for the latest stable."""
    import time

    from multipak import snap_store
    from multipak.snap import inspect_snap

    directory = engine.store.snap_download_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Downloads that were never installed are removed after a day.
    for old in directory.glob("*.snap"):
        try:
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink()
        except OSError:
            pass
    from multipak.snap import already_installed, installed_snap

    if installed_snap(engine.store, name) is not None:
        raise ValueError(already_installed())  # Before downloading it again.
    engine.progress(-1, tr("Looking for {0} in the Snap Store…").format(name))
    release = snap_store.release(name, channel=channel)
    target = directory / f"{release.name}_{release.revision}.snap"

    def progress(received, total):
        percent = min(99, received * 100 // max(1, total))
        engine.progress(
            percent,
            tr("Downloading {0} from the Snap Store: {1} of {2} MB…").format(
                name, received // 1024**2, max(1, total // 1024**2)
            ),
        )

    snap_store.download(release, target, progress=progress, cancelled=engine.cancelled)
    package = inspect_snap(target, engine.runner, revision=release.revision)
    package = dataclasses.replace(
        package,
        snap_resources=release.resources,
        snap_channel=release.channel,
        snap_channels=release.channels,
    )
    if package.name != release.name:
        target.unlink(missing_ok=True)
        raise ValueError(tr("The snap {0} has unexpected contents.").format(name))
    return package


def missing_services(engine, info):
    """Features the snap may lose here; a failed check never blocks installation."""
    from multipak.snap import missing_service_features

    try:
        return missing_service_features(info)
    except Exception as error:
        engine._log(tr("Could not check the services the snap uses: {0}").format(error))
        return []


def inspect_installation(engine, path):
    # File-type preferences are a convenience. A failure here must never prevent
    # the package from being opened.
    try:
        FileAssociations().initialize_defaults()
    except Exception as error:
        engine._log(tr("Could not update file associations: {0}").format(error))
    if isinstance(path, str):
        package = download_snap(engine, path, getattr(path, "channel", ""))
    else:
        package = inspect_package(path, engine.runner)
    if package.kind == "android":
        from multipak.android_instances import discover as discover_android
        from multipak.android_instances import discover_system

        environments = list(discover_android(engine.runner))
        personal = discover_system(engine.runner)
        if personal:
            environments.append(personal)
        return PackageInspection(
            package,
            android=preview_plan(package),
            android_environments=tuple(environments),
        )
    if package.kind == "snap":
        import platform

        from multipak.snap import already_installed, installed_snap, missing_runtime
        from multipak.snap_meta import refusal

        reason = refusal(package.snap, platform.machine())
        if not reason and installed_snap(engine.store, package.snap.name) is not None:
            reason = already_installed()
        missing = () if reason else tuple(missing_runtime(engine.store, package.snap))
        services = () if reason else tuple(missing_services(engine, package.snap))
        return PackageInspection(
            package, snap_refusal=reason, snap_missing=missing, snap_services=services
        )
    if package.kind == "appimage":
        report = appimage_dependencies(engine, package)
        if report is None or not report.required:
            return PackageInspection(package, dependencies=report)
        existing, profiles, name = appimage_environment(engine)
        return PackageInspection(
            package, existing, profiles, dependencies=report, container_names=(name,)
        )
    from multipak.package_icons import read_package_icon

    icon, suffix = read_package_icon(package, getattr(engine, "cancelled", None))
    if icon:
        package = dataclasses.replace(package, icon_data=icon, icon_extension=suffix)

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
        self.path = installer_target(path)
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
        self._percent = (
            -1 if mode in ("inspect", "channel", "search", "cleanup", "distrobox", "reboot") else 0
        )
        self._status = {
            "inspect": tr("Loading package information…"),
            "channel": tr("Loading package information…"),
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
                "channel": "switching",
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

    def _choose_channel(self, channel):
        """Download the snap again from another channel, then show its review."""
        shown = getattr(self.package, "snap_channel", "")
        if isinstance(self.path, str) and channel != shown and self.worker is None:
            # The review page stays open while the other channel downloads.
            self._channel_previous = self.path
            request = SnapRequest(str(self.path), channel)
            self.path = request
            self._run(lambda engine: self.inspector(engine, request), self._inspected, "channel")

    def _inspected(self, inspection):
        self.package = inspection.package
        self.name, self.version = self.package.name, self.package.version
        self.kind = self.package.kind.upper()
        if self.isAndroid:
            self._android_environments = inspection.android_environments
            self.reset_android_default()
            self.android_plan = inspection.android or preview_plan(self.package)
            self.method, self.mode = "waydroid", "standard"
            self.environment, self._selection = None, None
            self._set_state("ready")
            return
        if self.isSnap:
            self.method, self.mode = "snap", "standard"
            self.environment, self._selection = None, None
            self.set_snap_details(
                inspection.snap_refusal, inspection.snap_missing, inspection.snap_services
            )
            self._set_state("ready")
            return
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
        self.profiles = package_profiles(self.package, inspection.environments or ENVIRONMENTS)
        if self.package.kind == "slitaz" and self.package.architecture != "any":
            # Each SliTaz repository holds the programs of one architecture.
            wanted = slitaz_environment(self.package.architecture)
            self.profiles = tuple(p for p in self.profiles if p.id == wanted.id)
        elif self.package.kind == "xbps" and self.package.architecture != "noarch":
            # glibc, musl and 32-bit packages each have their own Void environment.
            wanted = void_environment(self.package.architecture)
            self.profiles = tuple(p for p in self.profiles if p.id == wanted.id)
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
        if self.isAndroid:
            from multipak.android_instances import identifier

            personal = self.androidUsesPersonalEnvironment
            if personal and self.mode != "advanced":
                return
            instance_id = "" if personal else self._android_environment or identifier()
            android_profile = "" if personal else self._android_profile
            android_label = "Waydroid" if personal else self.androidEnvironmentName
        appimage_options = self.appimage_options()
        snap_components = self.snap_component_choice() if self.isSnap else []
        self._logs = ""
        self.logsChanged.emit()

        def install(engine):
            if package.kind == "android":
                if personal:
                    from multipak.android import CONFIG, ROOTFS

                    engine.runner.run(["test", "-s", CONFIG])
                    engine.runner.run(["test", "-d", ROOTFS])
                return engine.install_android(
                    package,
                    allow_translation=True,
                    instance_id=instance_id,
                    android_profile=android_profile,
                    android_label=android_label,
                )
            if package.kind == "snap":
                return engine.install_snap(package, snap_components)
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
        self._distrobox_update_required = requirement.update_required
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
            tr("The Android application was installed and added to the application launcher.")
            if tx.method == "waydroid"
            else tr("The snap was installed and added to the application launcher.")
            if tx.method == "snap"
            else tr("The AppImage was installed and added to the application launcher.")
            if tx.method == "appimage"
            else tr("The application was installed in the {0} environment.").format(
                self.environment.title
            )
        )
        self._result_note = (
            tr("You can now open it from the application launcher.")
            if tx.launchers or tx.method == "waydroid"
            else ""
            if tx.method == "snap"  # Its warning already says there is no shortcut.
            else tr(
                "This package does not include an application shortcut. You can "
                "run it from a terminal inside the container."
            )
        )
        if tx.warnings:
            notes = [self._result_note] if self._result_note else []
            self._result_note = "\n\n".join([*notes, "\n".join(tx.warnings)])
        # Installed, but something it needs is still missing or could not be provided.
        self._result_warning = bool(tx.appimage_missing or tx.limitations)
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
        if self._mode == "channel" and outcome[0] != "success":
            # The review of the channel shown before stays; nothing was installed.
            self.path = getattr(self, "_channel_previous", self.path)
            self._cancel_pending = False
            self._set_state("ready")
            if outcome[0] == "error":
                self._log(outcome[1])
                self.notice.emit(outcome[1].splitlines()[0][:400])
            return
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
                if tx.method == "waydroid":
                    tx.android_translation_allowed = True
                    self.store.save(tx)
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
            if self.isAppImage or self.isSnap
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

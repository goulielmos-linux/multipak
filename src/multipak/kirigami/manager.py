"""Native application manager; journals and container operations stay in the core."""

import argparse
import html
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Lock

from PySide6.QtCore import (
    Property,
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

from multipak import DISPLAY_VERSION, host, package_updates, snap_updates
from multipak.containers import ManagedContainer, remove_container, uninstall_application
from multipak.environments import discover
from multipak.file_associations import FORMATS, FileAssociations
from multipak.localization import tr
from multipak.models import ExistingEnvironment, Transaction
from multipak.package_updates import check_container
from multipak.process import CommandError
from multipak.snap import LOCAL_REVISION
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
from .shutdown import release_engine
from .update_notice import bind_update_notice, installation_allowed

# Distribution names that title case would misspell.
FAMILY_NAMES = {"slitaz": "SliTaz", "opensuse": "openSUSE", "alt": "ALT Linux"}


def package_format(tx) -> str:
    """The format the user installed: a converted Puppy Linux PET shows as PET."""
    return "PET" if getattr(tx, "converted_from", "") == "puppy" else tx.package_kind.upper()


@dataclass(frozen=True)
class ContainerApp:
    """An application in the update list. Updating it updates its whole container:
    updating one package alone could break the other applications sharing it."""

    env: ExistingEnvironment
    tx: Transaction


class Manager(QObject):
    changed = Signal()
    rowsChanged = Signal()
    confirmationRequested = Signal()
    containerRemovalRequested = Signal()
    associationSettingsRequested = Signal()
    associationsSaved = Signal()
    languageSettingsRequested = Signal()
    companionWarning = Signal(str)
    repairConfirmationRequested = Signal()
    deleteConfirmationRequested = Signal()
    updateLinkRequested = Signal()
    updateClosingRequested = Signal()
    updateDependenciesRequested = Signal()
    groupUpdateRequested = Signal()
    snapPermissionsRequested = Signal(str)
    _repairsFound = Signal(object)
    _packageChecksFound = Signal(object)

    def __init__(
        self,
        store,
        parent=None,
        *,
        worker_factory=Worker,
        discoverer=discover,
        updater=update_container,
        package_checker=check_container,
        preview=False,
        association_settings=None,
    ):
        super().__init__(parent)
        self.store = store
        self.worker_factory = worker_factory
        self.discoverer = discoverer
        self.updater = updater
        self.package_checker = package_checker
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
        # AppImages whose container was renamed or deleted, as (transaction ID, name).
        self._repairs = []
        self._checking_repairs = False
        self._repair_plan = None
        self._repair_target = ""
        self._pending_delete = None
        self._delete_name = ""
        # AppImage updates: the result of each check, and the update in progress.
        self._checks = {}
        self._snap_checks = {}
        self._snap_permission_reply = None
        self._snap_permission_approved = False
        self._pending_snap_close = None
        self._force_check = False
        self._appimage_update = None
        self._update_link_error = ""
        # "Update all": what is left to update and what happened to each item.
        self._queue = None
        self._repairsFound.connect(self._set_repairs, Qt.QueuedConnection)
        # (container, application, other applications) waiting for the user's answer.
        self._group_update = None
        # Whether each application in a container has a newer version, by transaction.
        self._package_checks = {} if preview else package_updates.cached(store)
        self._checking_packages = False
        # AppImages and snaps with an update, from their last checks.
        self._other_updates = 0
        self._packageChecksFound.connect(self._set_package_checks, Qt.QueuedConnection)
        # Long container updates print many lines. Batch them so each line does not
        # trigger a full interface refresh, as the installer already does.
        self._pending_logs = ""
        self._log_lock = Lock()
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(150)
        self._log_timer.timeout.connect(self._flush_logs)

    @Slot()
    def openInstall(self):
        if self.busy or not installation_allowed(self):
            return
        self._mode, self._items, self._selected = "install", [], -1
        self._status, self._logs, self._error = "", "", False
        self._pending = None
        self.rowsChanged.emit()
        self.changed.emit()

    @Slot()
    def openInstaller(self):
        if self.busy or not installation_allowed(self):
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            tr("Install applications"),
            str(Path.home()),
            tr("Application packages") + " (*.rpm *.deb *.pkg.tar *.pkg.tar.zst "
            "*.pkg.tar.xz *.pkg.tar.gz *.pkg.tar.bz2 *.AppImage *.appimage *.snap);;"
            + tr("All files")
            + " (*)",
        )
        if not path:
            return
        self._start_installer(path)

    def _start_installer(self, argument):
        from PySide6.QtCore import QProcess

        if self.preview:
            self._status = tr("This is a preview. No application was opened.")
            self.changed.emit()
            return
        bootstrap = str(Path(__file__).with_name("bootstrap.py"))
        result = QProcess.startDetached(sys.executable, [bootstrap, argument])
        if not (result[0] if isinstance(result, tuple) else result):
            self.companionWarning.emit(tr("Could not open the application installer."))

    @Slot(str, result=bool)
    def validSnapName(self, text):
        from multipak.snap_store import store_name

        return bool(store_name(text))

    @Slot(str)
    def installSnap(self, text):
        """Open the installer for a snap from the Snap Store, by name or link."""
        from multipak.snap_store import store_name

        name = store_name(text)
        if self.busy or not name or not installation_allowed(self):
            return
        self._start_installer("snap:" + name)

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
    def working(self):
        """An operation is running, or "Update all" is waiting for an answer."""
        return self.busy or self._queue is not None or self._pending_snap_close is not None

    @Property(bool, notify=changed)
    def canClose(self):
        return not self.working

    @Property(bool, notify=changed)
    def canUpdateAll(self):
        return (
            self._mode == "update"
            and not self.working
            and any(
                isinstance(item, (ExistingEnvironment, ContainerApp))
                or (isinstance(item, Transaction) and item.method == "appimage")
                or self._snap_update_available(item)
                for item in self._items
            )
        )

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

    def _snap_update_available(self, item):
        if not (
            isinstance(item, snap_updates.Runtime)
            or (
                isinstance(item, Transaction)
                and item.method == "snap"
                and item.snap_revision != LOCAL_REVISION
            )
        ):
            return False
        check = self._snap_checks.get(item.id)
        return check is not None and check.status == "available" and not check.stale

    @Property(bool, notify=changed)
    def canAct(self):
        if self.working or not 0 <= self._selected < len(self._items):
            return False
        item = self._items[self._selected]
        if self._mode == "services":
            return not item["unsupported"]
        if self._mode == "commands":
            return True
        if self._mode == "update":
            return self._updatable(item)
        return (
            self._mode != "remove"
            or item.method != "system"
            or self._system_states.get(item.id) == "installed"
        )

    def _updatable(self, item):
        if isinstance(item, ContainerApp):
            # Nothing newer, or nowhere to get it from: no button for this application.
            check = self._package_checks.get(item.tx.id)
            return check is None or check.status not in ("current", "no-source")
        if isinstance(item, ExistingEnvironment):
            return True
        if isinstance(item, snap_updates.Runtime) or (
            isinstance(item, Transaction) and item.method == "snap"
        ):
            return self._snap_update_available(item)
        if not isinstance(item, Transaction) or item.method != "appimage":
            return False
        check = self._checks.get(item.id)
        return (
            check is not None
            and check.status in ("available", "no-source")
            and item.id not in {identifier for identifier, _ in self._repairs}
        )

    @Property(bool, notify=changed)
    def hasPreviousSnap(self):
        if self._mode != "update" or not 0 <= self._selected < len(self._items):
            return False
        item = self._items[self._selected]
        return (
            isinstance(item, Transaction)
            and item.method == "snap"
            and bool(item.snap_previous_revision)
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
        if self._pending and self._pending.method == "snap":
            return tr(
                "MultiPak's copy of the snap and its shortcuts will be removed. The "
                "application's data in the snap folder of your home will be preserved."
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

    @Property("QVariantList", notify=changed)
    def repairs(self):
        return [
            {"id": identifier, "name": name, "html": "<b>" + html.escape(name) + "</b>"}
            for identifier, name in self._repairs
        ]

    @Slot()
    def checkRepairs(self):
        """Look for broken AppImages without blocking the window or other operations."""
        if self.preview or self._checking_repairs:
            return
        self._checking_repairs = True
        store = self.store

        def work():
            from multipak.appimage_container import needing_repair
            from multipak.engine import Engine

            try:
                found = [
                    (tx.id, tx.package_name)
                    for tx in needing_repair(Engine(store, threading.Event()))
                ]
            except Exception:
                found = None
            self._repairsFound.emit(found)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def _set_repairs(self, found):
        self._checking_repairs = False
        if found is not None and found != self._repairs:
            self._repairs = found
            self.rowsChanged.emit()
            self.changed.emit()

    def _repair_name(self, identifier):
        return next((name for item, name in self._repairs if item == identifier), "")

    @Property(str, notify=changed)
    def repairName(self):
        return self._repair_plan["application"] if self._repair_plan else ""

    @Property(str, notify=changed)
    def repairExplanation(self):
        plan = self._repair_plan
        if not plan:
            return ""
        application, old = plan["application"], plan["old"]
        current, target = plan["current"], plan["target"]
        if plan["kind"] == "system":
            return tr(
                "The dependencies of {0} were found on your system. MultiPak will "
                "open it directly on the system, without a container."
            ).format(application)
        if plan["kind"] == "renamed":
            return tr(
                "The container {0} was renamed to {1}. MultiPak will take it back and "
                "rename it to {2} so that {3} works again."
            ).format(old, current, target, application)
        first = (
            tr("The container {0} was deleted.")
            if plan["deleted"]
            else tr("The container {0} is no longer available.")
        ).format(old)
        if plan["kind"] == "install":
            second = tr("MultiPak will install the dependencies of {0} in the container {1}.")
            return first + " " + second.format(application, target)
        return (
            first
            + " "
            + tr("MultiPak will create it again so that {0} works again.").format(application)
        )

    @Slot(str)
    def requestRepair(self, identifier):
        if self.busy or not self._repair_name(identifier):
            return
        if self.preview:
            self._status = tr("This is a preview. No application was repaired.")
            self.changed.emit()
            return
        from multipak.appimage_container import repair_plan

        self._repair_target = identifier
        self._status, self._logs, self._error = tr("Checking the container…"), "", False
        self._run(lambda engine: repair_plan(engine, identifier), self._planned_repair)

    def _planned_repair(self, plan):
        self._status = ""
        self._repair_plan = plan | {"id": self._repair_target}
        if plan["kind"] == "relink":
            # Nothing to explain: the container already has the right name again.
            QTimer.singleShot(0, self.confirmRepair)
        else:
            self.repairConfirmationRequested.emit()

    @Slot()
    def cancelRepair(self):
        self._repair_plan = None
        self.changed.emit()

    @Slot()
    def confirmRepair(self):
        if self.busy or self._repair_plan is None:
            return
        from multipak.appimage_container import repair

        plan, self._repair_plan = self._repair_plan, None
        identifier, on_system = plan["id"], plan["kind"] == "system"
        self._status = tr("Repairing {0}…").format(plan["application"])
        self._logs, self._error = "", False
        self._run(lambda engine: repair(engine, identifier, on_system=on_system), self._repaired)

    def _repaired(self, tx):
        self._repairs = [item for item in self._repairs if item[0] != tx.id]
        self._status, self._progress = tr("{0} was repaired.").format(tx.package_name), 100
        # Taking a container back repairs every AppImage in it.
        self.checkRepairs()

    @Property(str, notify=changed)
    def deleteName(self):
        return self._pending_delete[1] if self._pending_delete else ""

    @Slot(str)
    def requestDelete(self, identifier):
        name = self._repair_name(identifier)
        if not self.busy and name:
            self._pending_delete = (identifier, name)
            self.changed.emit()
            self.deleteConfirmationRequested.emit()

    @Slot()
    def cancelDelete(self):
        self._pending_delete = None
        self.changed.emit()

    @Slot()
    def confirmDelete(self):
        if self.busy or self._pending_delete is None:
            return
        (identifier, name), self._pending_delete = self._pending_delete, None
        if self.preview:
            self._status = tr("This is a preview. No application was removed.")
            self.changed.emit()
            return

        def operation(engine):
            tx = next((item for item in self.store.transactions() if item.id == identifier), None)
            if tx is None or tx.method != "appimage":
                raise RuntimeError(tr("This AppImage installation is unavailable."))
            # Only MultiPak's copy and shortcuts: the container is left as it is.
            engine.cleanup(tx)
            return identifier

        self._status = tr("Deleting {0}…").format(name)
        self._logs, self._error = "", False
        self._delete_name = name
        self._run(operation, self._deleted)

    def _deleted(self, identifier):
        self._repairs = [item for item in self._repairs if item[0] != identifier]
        self._status, self._progress = tr("{0} was deleted.").format(self._delete_name), 100

    @Property(str, constant=True)
    def version(self):
        return DISPLAY_VERSION

    @Property(str, constant=True)
    def licenseText(self):
        return license_text()

    @Property("QVariantList", notify=rowsChanged)
    def rows(self):
        if self._mode == "remove":
            repair_ids = {identifier for identifier, _ in self._repairs}
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
                        package_format(tx),
                    ),
                    "detail": (
                        tr("Installed directly on the system")
                        if tx.method == "system"
                        else tx.container
                        if tx.method == "appimage" and tx.appimage_mode == "container"
                        else tr("AppImage managed by MultiPak")
                        if tx.method == "appimage"
                        else tr("Snap with classic confinement")
                        if tx.method == "snap" and tx.snap_confinement == "classic"
                        else tr("Snap managed by MultiPak")
                        if tx.method == "snap"
                        else tx.container
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
                        else tr("This application's container was replaced.")
                        if tx.id in repair_ids
                        else ""
                    ),
                    "removable": tx.method != "system"
                    or self._system_states.get(tx.id) == "installed",
                }
                for tx in self._items
            ]
        if self._mode == "services":
            return [self._service_row(item) for item in self._items]
        if self._mode == "commands":
            return [self._command_row(item) for item in self._items]
        if self._mode != "update":
            return []
        from multipak.appimage_container import update_entry

        appimages = None if self.preview else update_entry(self.store)
        transactions = self.store.transactions()
        rows, previous = [], None
        for item in self._items:
            if isinstance(item, ContainerApp):
                kind, row = "container-app", self._container_app_row(item)
            elif isinstance(item, ExistingEnvironment):
                kind, row = "container", self._container_row(item, appimages, transactions)
            elif isinstance(item, snap_updates.Runtime):
                kind, row = "snap-runtime", self._snap_update_row(item)
            elif item.method == "snap":
                kind, row = "snap", self._snap_update_row(item)
            else:
                kind, row = "appimage", self._appimage_row(item)
            group = self._group(item)
            row["section"] = "" if group == previous else self._group_title(group)
            row["kind"], previous = kind, group
            row["updatable"] = self._updatable(item)
            rows.append(row)
        return rows

    # Background services of snaps

    @Property(bool, notify=changed)
    def hasSnaps(self):
        return not self.preview and any(
            tx.method == "snap" and tx.status == "installed" for tx in self.store.transactions()
        )

    def _load_services(self, engine):
        """Every service of the installed snaps, with its state."""
        from multipak.snap import _context
        from multipak.snap_daemons import Services

        items = []
        snaps = [
            tx
            for tx in self.store.transactions()
            if tx.method == "snap" and tx.status == "installed"
        ]
        for tx in sorted(snaps, key=lambda item: item.package_name.casefold()):
            try:
                info, _state = _context(self.store, tx)
            except Exception as error:
                engine._log(str(error))
                continue
            services = Services(tx, info, store=self.store)
            for name in info.services:
                app = info.app(name)
                items.append(
                    {
                        "tx": tx,
                        "info": info,
                        "name": name,
                        "state": services.state(name),
                        "unsupported": app.unsupported,
                    }
                )
        return items

    def _loaded_services(self, items):
        self._items = items
        # Without snaps, the message above the tabs already says so.
        self._status = (
            tr("No installed snap has background services.") if self.hasSnaps and not items else ""
        )

    def _service_row(self, item):
        app = item["info"].app(item["name"])
        state = item["state"]
        if app.timer and state == "active":
            text = tr("Scheduled")
        else:
            text = {
                "active": tr("Running"),
                "activating": tr("Starting"),
                "deactivating": tr("Stopping"),
                "failed": tr("Failed"),
            }.get(state, tr("Stopped"))
        if item["unsupported"]:
            detail = ""
        elif app.timer:
            detail = tr("Runs on its schedule ({0}).").format(app.timer)
        elif app.scope == "user":
            detail = tr("Runs with your account and starts with your session.")
        elif app.install_mode == "enable":
            detail = tr("Started after installation and when you ask.")
        else:
            detail = tr("Started only when you ask.")
        return {
            "title": f"{item['info'].name}.{item['name']}",
            "icon": GENERIC_ICON if self.preview else application_icon(self.store, item["tx"]),
            "subtitle": text,
            "detail": detail,
            "note": (
                tr("MultiPak cannot run this service: {0}").format(", ".join(item["unsupported"]))
                if item["unsupported"]
                else ""
            ),
            "kind": "service",
            "section": "",
            "running": state in ("active", "activating"),
            "supported": not item["unsupported"],
        }

    def _selected_service(self):
        if self._mode != "services" or not self.canAct:
            return None
        return self._items[self._selected]

    @Slot(str)
    def controlService(self, action):
        """Start or stop the selected service, as snapctl start and stop do."""
        item = self._selected_service()
        if item is None or action not in ("start", "stop"):
            return
        if self.preview:
            self._status = tr("This is a preview. No service was changed.")
            self.changed.emit()
            return
        from multipak.snap_daemons import Services

        tx, info, name = item["tx"], item["info"], item["name"]
        label = f"{info.name}.{name}"

        def operation(engine):
            services = Services(tx, info, store=self.store)
            services.act(action, [label])
            return services.state(name)

        self._status = (tr("Starting {0}…") if action == "start" else tr("Stopping {0}…")).format(
            label
        )
        self._logs, self._error = "", False
        self._run(operation, lambda state: self._service_changed(item, state))

    def _service_changed(self, item, state):
        item["state"] = state
        self._status, self._progress = "", 100
        self.rowsChanged.emit()

    @Slot()
    def showServiceLog(self):
        """What the service wrote, from the user's journal."""
        item = self._selected_service()
        if item is None or self.preview:
            return
        from multipak.snap_daemons import host_call, unit

        name = unit(item["tx"], item["name"])
        units = [name, name.removesuffix(".service") + ".timer"]

        def operation(engine):
            command = ["journalctl", "--user", "--no-pager", "-n", "300", "-o", "short-iso"]
            for value in units:
                command += ["-u", value]
            code, output = host_call(command)
            return output.strip() or tr("The service has not written anything yet.")

        self._status, self._logs, self._error = tr("Loading…"), "", False
        self._run(operation, self._service_log)

    def _service_log(self, text):
        self._logs = text
        self._status = tr("The log is in the details below.")

    # Administrator commands of snaps: an app run as root in its sandbox, as sudo
    # runs it with snapd (Nextcloud's occ, for example). Never root on the computer.

    def _load_commands(self, engine):
        from multipak.snap import _context, admin_apps, graphical_apps

        items = []
        snaps = [
            tx
            for tx in self.store.transactions()
            if tx.method == "snap" and tx.status == "installed"
        ]
        for tx in sorted(snaps, key=lambda item: item.package_name.casefold()):
            try:
                info, _state = _context(self.store, tx)
            except Exception as error:
                engine._log(str(error))
                continue
            names = admin_apps(info, graphical_apps(self.store, tx))
            items.extend({"tx": tx, "info": info, "name": name} for name in names)
        return items

    def _loaded_commands(self, items):
        self._items = items
        self._status = (
            tr("No installed snap has commands that MultiPak can run as administrator.")
            if self.hasSnaps and not items
            else ""
        )

    def _command_row(self, item):
        from multipak.snap import admin_label

        return {
            "title": admin_label(item["info"], item["name"]),
            "icon": GENERIC_ICON if self.preview else application_icon(self.store, item["tx"]),
            "subtitle": item["tx"].package_name,
            "detail": "",
            "note": "",
            "kind": "command",
            "section": "",
        }

    def _selected_command(self):
        if self._mode != "commands" or not self.canAct:
            return None
        return self._items[self._selected]

    @Property(str, notify=changed)
    def adminCommandName(self):
        from multipak.snap import admin_label

        item = self._selected_command()
        return admin_label(item["info"], item["name"]) if item else ""

    @staticmethod
    def _admin_arguments(text):
        import shlex

        try:
            return shlex.split(text or "")
        except ValueError:
            return None

    @Slot(str, result=bool)
    def validAdminArguments(self, text):
        return self._admin_arguments(text) is not None

    def admin_invocation(self, tx, name, arguments):
        """The snap launcher with --admin, in a process of its own: its output is
        shown in the details as it comes."""
        return [
            sys.executable,
            "-m",
            "multipak.snap_launcher",
            "--admin",
            tx.id,
            name,
            *arguments,
        ]

    @Slot(str)
    def runAdminCommand(self, text):
        """Run the selected app as the snap's administrator, after the user confirmed
        it in the dialog. text: its arguments, as typed in a terminal."""
        item = self._selected_command()
        arguments = self._admin_arguments(text)
        if item is None or arguments is None:
            return
        from multipak.snap import admin_label

        label = admin_label(item["info"], item["name"])
        if self.preview:
            self._status = tr("This is a preview. Nothing was run.")
            self.changed.emit()
            return
        argv = self.admin_invocation(item["tx"], item["name"], arguments)

        def operation(engine):
            import subprocess

            engine._log("$ " + " ".join([label, *arguments]))
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                env=os.environ | {"PYTHONUNBUFFERED": "1"},
            )
            with process.stdout:
                for line in process.stdout:
                    engine._log(line.rstrip("\n"))
            return process.wait()

        self._status = tr("Running {0} as administrator…").format(label)
        self._logs, self._error = "", False
        self._run(operation, self._admin_finished)

    def _admin_finished(self, code):
        self._progress = 100
        if code == 0:
            self._status = tr("The command finished. Its output is in the details below.")
        else:
            self._status = tr(
                "The command ended with exit code {0}. Its output is in the details below."
            ).format(code)
            self._error = True

    def _container_row(self, env, appimages, transactions):
        if appimages is not None and appimages.name == env.name:
            detail = tr("Dependencies of AppImages")
        else:
            names = sorted(
                {
                    tx.package_name
                    for tx in transactions
                    if tx.method == "distrobox"
                    and tx.container == env.name
                    and tx.status == "installed"
                },
                key=str.casefold,
            )
            detail = tr("Applications: {0}").format(", ".join(names)) if names else ""
        return {
            "title": env.display_name,
            "icon": "",
            "subtitle": f"{FAMILY_NAMES.get(env.family, env.family.title())} {env.version}".strip(),
            "detail": detail or env.name,
            "note": "",
        }

    def _container_app_row(self, item):
        tx = item.tx
        check = self._package_checks.get(tx.id)
        status = check.status if check is not None else ""
        if status == "available":
            note = (
                tr("Update available: {0}").format(check.version)
                if check.version
                else tr("Update available")
            )
        elif status == "current":
            note = tr("Up to date")
        elif status == "no-source":
            note = tr(
                "Installed from a file, without a source of updates. To update it, "
                "install the new version from a file."
            )
        elif status == "unsupported":
            note = tr("MultiPak cannot check this container for updates yet.")
        elif status == "failed":
            note = tr("Could not check for updates: {0}").format(check.error)
        else:
            note = tr("Updates not checked yet.")
        return {
            "title": tx.package_name,
            "icon": GENERIC_ICON if self.preview else application_icon(self.store, tx),
            "subtitle": tr("Version: {0} · {1}").format(
                (check.installed if check is not None else "") or tx.package_version,
                package_format(tx),
            ),
            "detail": item.env.name,
            "note": note,
        }

    # Groups of the update list: what has an update first, what is up to date last.
    GROUPS = ("updates", "no-source", "unchecked", "current", "components", "runtimes")

    def _group(self, item):
        if isinstance(item, ContainerApp):
            check = self._package_checks.get(item.tx.id)
            status = check.status if check is not None else ""
            return {"available": 0, "no-source": 1, "current": 3}.get(status, 2)
        if isinstance(item, ExistingEnvironment):
            return 4
        if isinstance(item, snap_updates.Runtime):
            return 5
        if isinstance(item, Transaction) and item.method == "snap":
            if item.snap_revision == LOCAL_REVISION:
                return 1
            if self._snap_update_available(item):
                return 0
            check = self._snap_checks.get(item.id)
            return 3 if check is not None and check.status == "current" and not check.stale else 2
        if isinstance(item, Transaction):
            if item.id in {identifier for identifier, _ in self._repairs}:
                return 2
            check = self._checks.get(item.id)
            status = check.status if check is not None else ""
            return {"available": 0, "no-source": 1, "current": 3}.get(status, 2)
        return 2

    def _group_title(self, group):
        return (
            tr("With updates"),
            tr("No update source"),
            tr("Not checked for updates"),
            tr("Already up to date"),
            tr("Components"),
            tr("Bases and content"),
        )[group]

    @staticmethod
    def _title(item):
        if isinstance(item, ContainerApp):
            return item.tx.package_name
        if isinstance(item, ExistingEnvironment):
            return item.display_name
        if isinstance(item, snap_updates.Runtime):
            return item.name
        return item.package_name

    def _resort(self):
        """Keep the update list grouped after a check or an update changed a group."""
        if self._mode != "update" or not self._items:
            return
        selected = self._items[self._selected] if 0 <= self._selected < len(self._items) else None
        self._items = sorted(
            self._items, key=lambda item: (self._group(item), self._title(item).casefold())
        )
        if selected is not None:
            self._selected = next(
                (index for index, item in enumerate(self._items) if item is selected), -1
            )
        self._other_updates = sum(
            1
            for item in self._items
            if not isinstance(item, (ContainerApp, ExistingEnvironment)) and self._group(item) == 0
        )

    # Newer versions of the applications in containers

    def _package_targets(self, transactions):
        """{(container, kind): [transactions]} that a package manager can check."""
        targets = {}
        for tx in transactions:
            if (
                tx.method == "distrobox"
                and tx.status == "installed"
                and tx.package_kind in package_updates.SUPPORTED
            ):
                targets.setdefault((tx.container, tx.package_kind), []).append(tx)
        return targets

    def _check_packages(self, runner, transactions, refresh, progress=None):
        results, now = {}, time.time()
        for (container, kind), txs in self._package_targets(transactions).items():
            if progress is not None:
                progress(tr("Checking for updates in {0}…").format(container))
            try:
                found = self.package_checker(
                    runner, container, kind, [tx.package_name for tx in txs], refresh=refresh
                )
            except Exception as error:  # noqa: BLE001 - one container must not stop the rest
                found = {
                    tx.package_name: package_updates.Check("failed", error=str(error), checked=now)
                    for tx in txs
                }
            for tx in txs:
                if tx.package_name in found:
                    results[tx.id] = found[tx.package_name]
        return results

    @Slot()
    def checkUpdatesInBackground(self):
        """Check without downloading package lists or blocking the window."""
        if self.preview or self._checking_packages:
            return
        self._checking_packages = True
        self.changed.emit()
        store = self.store

        def work():
            from multipak.appimage_updates import cached as appimage_cached
            from multipak.engine import Engine

            try:
                engine = Engine(store, threading.Event())
                results = self._check_packages(engine.runner, store.transactions(), False)
            except Exception:  # noqa: BLE001 - the list then shows "not checked"
                results = {}
            others = 0
            try:
                others += sum(
                    1 for check in appimage_cached(store).values() if check.status == "available"
                )
                others += sum(
                    1
                    for check in snap_updates.cached(store).values()
                    if check.status == "available" and not check.stale
                )
            except Exception:  # noqa: BLE001
                others = None
            self._packageChecksFound.emit((results, others))

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def _set_package_checks(self, found):
        results, others = found
        self._checking_packages = False
        if others is not None and self._mode != "update":
            self._other_updates = others
        self._merge_package_checks(results)

    def _merge_package_checks(self, results):
        if results:
            self._package_checks = dict(self._package_checks) | results
            try:
                package_updates.remember(self.store, self._package_checks)
            except OSError:
                pass  # The next opening checks again.
            self._resort()
            self.rowsChanged.emit()
        self.changed.emit()

    def _container_done(self, name, result):
        """After a container update, its applications are up to date."""
        now, updated = time.time(), {}
        for tx in self.store.transactions():
            check = self._package_checks.get(tx.id)
            if tx.method == "distrobox" and tx.container == name and check is not None:
                if check.status == "available":
                    updated[tx.id] = package_updates.Check(
                        "current", check.version or check.installed, checked=now
                    )
        self._merge_package_checks(updated)
        self._updated(result)

    @Property(bool, notify=changed)
    def checkingUpdates(self):
        return self._checking_packages

    @Property(int, notify=changed)
    def updateCount(self):
        """Applications and snaps with an update; a container counts each application."""
        installed = {tx.id for tx in self.store.transactions()}
        return self._other_updates + sum(
            1
            for identifier, check in self._package_checks.items()
            if check.status == "available" and identifier in installed
        )

    def _by_application(self, items):
        """Show each container as its applications; one without any is left out."""
        transactions = self.store.transactions()
        # The shared AppImage container has no packages of its own but serves AppImages.
        serving = {tx.container for tx in transactions if tx.status == "installed" and tx.container}
        applications, rest = [], []
        for item in items:
            own = (
                [
                    ContainerApp(item, tx)
                    for tx in transactions
                    if tx.method == "distrobox"
                    and tx.container == item.name
                    and tx.status == "installed"
                ]
                if isinstance(item, ExistingEnvironment)
                else []
            )
            applications.extend(own)
            if not own and (not isinstance(item, ExistingEnvironment) or item.name in serving):
                rest.append(item)
        return applications + rest

    def _container_label(self, name):
        """The applications of a container, as the user knows it, for messages."""
        names = sorted(
            {
                item.tx.package_name
                for item in self._items
                if isinstance(item, ContainerApp) and item.env.name == name
            },
            key=str.casefold,
        )
        return ", ".join(names) or name

    @Property(str, notify=changed)
    def groupUpdateName(self):
        return self._group_update[1] if self._group_update else ""

    @Property(str, notify=changed)
    def groupUpdateOthers(self):
        if not self._group_update:
            return ""
        # The other applications: the sentence above the list already names the chosen one.
        _container, chosen, others = self._group_update
        names = sorted({name for name in others if name != chosen}, key=str.casefold)
        return "\n".join("• " + name for name in names)

    @Slot()
    def confirmGroupUpdate(self):
        pending, self._group_update = self._group_update, None
        if pending is not None and not self.working and self._mode == "update":
            self._update_container(pending[0], pending[1])

    @Slot()
    def cancelGroupUpdate(self):
        self._group_update = None
        self.changed.emit()

    def _appimage_row(self, tx):
        check = self._checks.get(tx.id)
        if tx.id in {identifier for identifier, _ in self._repairs}:
            note = tr("This application's container was replaced.")
        elif check is None:
            note = tr("Updates not checked yet.")
        elif check.status == "available":
            release = check.release
            version = (release.version or release.name) if release else ""
            note = (
                tr("Update available: {0}").format(version) if version else tr("Update available")
            )
        elif check.status == "current":
            note = tr("Up to date")
        elif check.status == "no-source":
            note = tr("The application does not indicate its update address.")
        else:
            note = tr("Could not check for updates: {0}").format(check.error)
        if check is not None and check.stale:
            note += "\n" + tr("Could not check for updates now. Showing the last result.")
        return {
            "id": tx.id,
            "title": tx.package_name,
            "icon": GENERIC_ICON if self.preview else application_icon(self.store, tx),
            "subtitle": tr("Version: {0} · {1}").format(
                tr("Not provided") if tx.package_version == "Not provided" else tx.package_version,
                package_format(tx),
            ),
            "detail": (
                tx.container
                if tx.appimage_mode == "container"
                else tr("AppImage managed by MultiPak")
            ),
            "note": note,
        }

    def _snap_update_row(self, item):
        runtime = isinstance(item, snap_updates.Runtime)
        check = self._snap_checks.get(item.id)
        if not runtime and item.snap_revision == LOCAL_REVISION:
            note = tr("Installed from a local file. Updates are unavailable.")
        elif check is None:
            note = tr("Updates not checked yet.")
        elif (
            check.status == "available"
            and runtime
            and snap_updates.downloaded(self.store, item.name, check.revision)
        ):
            # Nothing to download: some snaps still have to switch to it.
            waiting = snap_updates.behind(self._snap_transactions(), item.name, check.revision)
            note = tr("Revision {0} is already downloaded. Still to switch: {1}").format(
                check.revision, ", ".join(tx.package_name for tx in waiting)
            )
        elif check.status == "available":
            note = (
                tr("Update available: {0} (revision {1})").format(check.version, check.revision)
                if check.version
                else tr("Update available: revision {0}").format(check.revision)
            )
        elif check.status == "current":
            note = tr("Up to date")
        else:
            note = tr("Could not check for updates: {0}").format(check.error)
        if check is not None and check.stale:
            note += "\n" + tr("Could not check for updates now. Showing the last result.")
        if runtime:
            channel = check.channel if check is not None else ""
        elif item.snap_revision != LOCAL_REVISION:
            channel = item.snap_channel or "latest/stable"
        else:
            channel = ""
        return {
            "title": item.name if runtime else item.package_name,
            "icon": GENERIC_ICON if runtime else application_icon(self.store, item),
            "subtitle": (
                tr("Installed revisions: {0}").format(", ".join(item.revisions))
                if runtime
                else tr("Version: {0} · {1}").format(item.package_version, "SNAP")
            ),
            "detail": tr("Channel: {0}").format(channel) if channel else "",
            "note": note,
        }

    def _snap_transactions(self):
        return [
            item for item in self._items if isinstance(item, Transaction) and item.method == "snap"
        ]

    def _sync_snap_items(self, *changed):
        """After a snap, base or content changed: list the snaps as installed now, each
        base and content with the revisions they use now, and adjust the results of
        the last check to match, without consulting the store."""
        if self._mode != "update" or self.preview:
            return
        overrides = {item.id: item for item in changed if item is not None}
        selected = (
            getattr(self._items[self._selected], "id", None)
            if 0 <= self._selected < len(self._items)
            else None
        )
        others = [
            item
            for item in self._items
            if not isinstance(item, snap_updates.Runtime)
            and not (isinstance(item, Transaction) and item.method == "snap")
        ]
        snaps = [
            overrides.get(item.id, item)
            for item in snap_updates.managed(self.store)
            if isinstance(item, Transaction)
        ]
        runtimes = [overrides.get(item.id, item) for item in snap_updates.runtimes(snaps)]
        self._items = others + snaps + runtimes
        self._snap_checks = {
            item.id: snap_updates.reconcile(item, self._snap_checks[item.id])
            for item in snaps + runtimes
            if item.id in self._snap_checks
        }
        self._selected = next(
            (
                index
                for index, item in enumerate(self._items)
                if selected is not None and getattr(item, "id", None) == selected
            ),
            -1,
        )
        try:
            snap_updates.remember(self.store, self._snap_checks)
        except OSError:
            pass  # Only the next reopening shows less.
        self._resort()
        self.rowsChanged.emit()

    @Slot(int)
    def select(self, index):
        if not self.working and -1 <= index < len(self._items) and index != self._selected:
            self._selected = index
            self.changed.emit()

    @Slot(int)
    def updateRow(self, index):
        """The update button of one row: select it and update it."""
        if self.working or self._mode != "update" or not 0 <= index < len(self._items):
            return
        if index != self._selected:
            self._selected = index
            self.changed.emit()
        self.updateSelected()

    def _choose(self, index, mode):
        """A row's own button: select its row first, as a click on it would."""
        if self.working or self._mode != mode or not 0 <= index < len(self._items):
            return False
        if index != self._selected:
            self._selected = index
            self.changed.emit()
        return True

    @Slot(int)
    def removeRow(self, index):
        if self._choose(index, "remove"):
            self.requestRemoval()

    @Slot(int, str)
    def controlServiceRow(self, index, action):
        if self._choose(index, "services"):
            self.controlService(action)

    @Slot(int)
    def showServiceLogRow(self, index):
        if self._choose(index, "services"):
            self.showServiceLog()

    def _removable(self):
        return sorted(
            (
                tx
                for tx in self.store.transactions()
                if tx.method in ("distrobox", "appimage", "snap")
                and tx.status in ("installed", "cleanup_failed")
            ),
            key=lambda tx: (tx.package_name.casefold(), tx.container.casefold(), tx.id),
        )

    def _load_removals(self, engine):
        from multipak.containers import forget_abandoned

        # Applications of a renamed or deleted container are no longer offered.
        forget_abandoned(engine)
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
        if self.working or mode not in ("remove", "update", "services", "commands"):
            return
        self._mode, self._selected, self._items = mode, -1, []
        self._pending = None
        self.rowsChanged.emit()
        self._status, self._logs, self._error = tr("Loading…"), "", False
        if mode == "remove":
            self._run(self._load_removals, self._loaded_removals)
        elif mode == "services":
            self._run(self._load_services, self._loaded_services)
        elif mode == "commands":
            self._run(self._load_commands, self._loaded_commands)
        else:
            force, self._force_check = self._force_check, False
            self._appimage_update = None

            def operation(engine):
                from multipak.appimage_container import update_entry
                from multipak.containers import read_record

                def answers_to_its_name(runner, env):
                    try:
                        current = runner.run(
                            [
                                "podman",
                                "container",
                                "inspect",
                                "--format",
                                "{{.Id}}",
                                "--",
                                env.name,
                            ],
                            timeout=30,
                        )
                    except (CommandError, OSError, TimeoutError):
                        return False
                    return current.strip() == env.container_id

                def custom_and_unchanged(env):
                    # A custom-named container stays MultiPak's only under the name
                    # MultiPak recorded when it created it.
                    try:
                        record = read_record(self.store, env.name)
                    except (OSError, TypeError, ValueError):
                        return False
                    return record is not None and record.container_id == env.container_id

                environments = [
                    env
                    for env in self.discoverer(engine.runner)
                    if env.multipak_managed or (env.custom_named and custom_and_unchanged(env))
                ]
                # The AppImage container is private to MultiPak and not offered for
                # package installations, but its packages still need updates. While it
                # is renamed or deleted, it waits for a repair instead.
                appimages = None if self.preview else update_entry(self.store)
                if (
                    appimages
                    and all(env.name != appimages.name for env in environments)
                    and answers_to_its_name(engine.runner, appimages)
                ):
                    environments.append(appimages)
                environments.sort(key=lambda env: env.display_name.casefold())
                applications, checks = self._load_appimages(engine, force)
                snaps, snap_checks = self._load_snap_updates(engine, force)
                # "Check for updates" downloads the package lists of every container.
                package_checks = (
                    self._check_packages(
                        engine.runner,
                        self.store.transactions(),
                        True,
                        lambda text: engine.progress(-1, text),
                    )
                    if force and not self.preview
                    else {}
                )
                return environments + applications + snaps, checks, snap_checks, package_checks

            self._run(operation, self._loaded_updates)

    def _loaded_removals(self, result):
        self._items, self._system_states, error = result
        self._error = bool(error)
        self._status = error or (
            "" if self._items else tr("No applications are available to uninstall.")
        )

    def _load_appimages(self, engine, force):
        """The managed AppImages and their checks; the servers only when asked."""
        from multipak.appimage_updates import cached, check, managed

        if self.preview:
            return [], {}
        checks = check(engine, force=True) if force else cached(self.store)
        applications = sorted(
            managed(self.store), key=lambda tx: (tx.package_name.casefold(), tx.id)
        )
        return applications, checks

    def _load_snap_updates(self, engine, force):
        if self.preview:
            return [], {}
        items = snap_updates.managed(self.store)
        checks = (
            snap_updates.check(
                self.store, cancelled=engine.cancelled, progress=engine.progress, log=engine._log
            )
            if force
            else snap_updates.cached(self.store)
        )
        return items, checks

    def _loaded_updates(self, result):
        items, self._checks, self._snap_checks, package_checks = result
        self._items = self._by_application(items)
        if package_checks:
            self._package_checks = dict(self._package_checks) | package_checks
            try:
                package_updates.remember(self.store, self._package_checks)
            except OSError:
                pass
        self._resort()
        self._status = "" if self._items else tr("Nothing installed by MultiPak can be updated.")

    def _replace_check(self, transaction_id, check):
        self._checks = dict(self._checks) | {transaction_id: check}
        self._resort()
        self.rowsChanged.emit()

    def _update_name(self):
        if self._pending_snap_close is not None:
            return self._pending_snap_close[1].package_name
        return self._appimage_update["name"] if self._appimage_update else ""

    @Property(str, notify=changed)
    def updateName(self):
        return self._update_name()

    @Property(str, notify=changed)
    def updateLinkError(self):
        return self._update_link_error

    @Property(str, notify=changed)
    def updateDependencies(self):
        prepared = (self._appimage_update or {}).get("prepared")
        return prepared.dependencies if prepared else ""

    def _start_appimage_update(self, tx=None):
        tx = tx or self._items[self._selected]
        check = self._checks.get(tx.id)
        self._appimage_update = {
            "id": tx.id,
            "name": tx.package_name,
            "release": check.release,
            "link": "",
            "prepared": None,
        }
        if check.status == "no-source":
            self._update_link_error = ""
            self.changed.emit()
            self.updateLinkRequested.emit()
        else:
            self._download_update()

    @Slot(str)
    def submitUpdateLink(self, text):
        if self.busy or self._appimage_update is None:
            return
        from multipak.appimage_updates import check_link

        identifier = self._appimage_update["id"]
        self._status, self._logs, self._error = tr("Loading…"), "", False
        self._run(
            lambda engine: check_link(engine, identifier, text),
            self._checked_link,
            self._link_failed,
        )

    def _checked_link(self, check):
        update = self._appimage_update
        self._replace_check(update["id"], check)
        if check.status == "current":
            self._appimage_update, self._progress = None, 100
            self._status = tr("{0} is up to date.").format(update["name"])
            return
        update["release"], update["link"] = check.release, check.link
        self._status = ""
        QTimer.singleShot(0, self._download_update)

    def _link_failed(self, message):
        self._status, self._update_link_error = "", message
        self.updateLinkRequested.emit()

    @Slot()
    def cancelAppImageUpdate(self):
        update, self._appimage_update = self._appimage_update, None
        if update and update.get("prepared") is not None:
            from multipak.appimage_updates import discard

            discard(self.store, update["id"])
        self._status = ""
        self.changed.emit()
        if update is not None:
            self._queue_step("skipped", update["name"])

    def _download_update(self):
        if self.busy or self._appimage_update is None:
            return
        from multipak.appimage_updates import download, running

        update = self._appimage_update
        identifier, release, link = update["id"], update["release"], update["link"]

        def operation(engine):
            tx = next(item for item in self.store.transactions() if item.id == identifier)
            if running(self.store, tx):
                return "running"
            return download(engine, identifier, release, link=link)

        self._status, self._error = tr("Downloading {0}…").format(update["name"]), False
        self._clear_logs()
        self._run(operation, self._downloaded, self._download_failed)

    def _downloaded(self, result):
        if result == "running":
            self._status = ""
            self.updateClosingRequested.emit()
            return
        self._appimage_update["prepared"] = result
        self._status = ""
        if result.packages:
            self.updateDependenciesRequested.emit()
        else:
            QTimer.singleShot(0, self.confirmAppImageUpdate)

    def _download_failed(self, message):
        from multipak.appimage_updates import LinkMismatch

        update = self._appimage_update
        if update and update["link"] and message == str(LinkMismatch()):
            # The link led to another application: ask for it again.
            self._status, self._update_link_error = "", message
            update["link"], update["release"] = "", None
            self.updateLinkRequested.emit()
            return
        self._appimage_update = None
        if not self._queue_step("failed", update["name"] if update else "", message):
            self._status, self._error = tr("Could not complete: ") + message, True

    @Slot()
    def cancelUpdateClosing(self):
        if self._pending_snap_close is None:
            self.cancelAppImageUpdate()
            return
        self._pending_snap_close = None
        self._status = tr("Snap update cancelled.")
        self._queue_step("skipped", self._queue["current"] if self._queue else "")
        self.changed.emit()

    def _close_snap_for_update(self):
        from multipak.snap_application import close

        item, blocking, retry = self._pending_snap_close
        self._status = tr("Closing {0}…").format(blocking.package_name)
        self._clear_logs()

        def finished(closed):
            if closed:
                self._pending_snap_close = None
                QTimer.singleShot(0, retry)
            else:
                self._status = tr("{0} did not close. Close it and try again.").format(
                    blocking.package_name
                )
                self.updateClosingRequested.emit()

        self._run(
            lambda engine: close(self.store, blocking),
            finished,
            lambda error: self._snap_update_failed(item, error),
        )

    @Slot()
    def closeUpdatedApplication(self):
        """Close the running application, then continue the update."""
        if self.busy:
            return
        if self._pending_snap_close is not None:
            self._close_snap_for_update()
            return
        if self._appimage_update is None:
            return
        from multipak.appimage_updates import close

        identifier, name = self._appimage_update["id"], self._appimage_update["name"]

        def operation(engine):
            tx = next(item for item in self.store.transactions() if item.id == identifier)
            return close(self.store, tx)

        self._status, self._error = tr("Closing {0}…").format(name), False
        self._clear_logs()
        self._run(operation, self._closed_application)

    def _closed_application(self, closed):
        if not closed:
            self._status = tr("{0} did not close. Close it and try again.").format(
                self._update_name()
            )
            self.updateClosingRequested.emit()
            return
        self._status = ""
        if self._appimage_update["prepared"] is None:
            QTimer.singleShot(0, self._download_update)
        else:
            QTimer.singleShot(0, self.confirmAppImageUpdate)

    @Slot()
    def confirmAppImageUpdate(self):
        update = self._appimage_update
        if self.busy or update is None or update["prepared"] is None:
            return
        from multipak.appimage_updates import install, running

        prepared = update["prepared"]

        def operation(engine):
            tx = next(
                item for item in self.store.transactions() if item.id == prepared.transaction_id
            )
            # It may have been opened again while the new version was downloading.
            if running(self.store, tx):
                return "running"
            return install(engine, prepared)

        self._status = tr("Updating {0}… Please wait.").format(update["name"])
        self._error = False
        self._clear_logs()
        self._run(operation, self._appimage_updated, self._appimage_update_failed)

    def _appimage_updated(self, result):
        if result == "running":
            self._status = ""
            self.updateClosingRequested.emit()
            return
        from multipak.appimage_updates import Check

        name = self._update_name()
        self._appimage_update = None
        self._items = [
            result if isinstance(item, Transaction) and item.id == result.id else item
            for item in self._items
        ]
        self._replace_check(result.id, Check("current", result.appimage_update_source))
        self._progress = 100
        if self._queue_step("updated", name, warnings=result.warnings):
            return
        self._status = "\n\n".join([tr("{0} was updated.").format(name), *result.warnings])
        self._error = bool(result.appimage_missing)

    def _appimage_update_failed(self, message):
        update, self._appimage_update = self._appimage_update, None
        if update is not None:
            from multipak.appimage_updates import discard

            discard(self.store, update["id"])
        if not self._queue_step("failed", update["name"] if update else "", message):
            self._status, self._error = tr("Could not complete: ") + message, True

    @Slot()
    def goHome(self):
        if not self.working:
            self._mode, self._items, self._selected = "home", [], -1
            self._status, self._logs, self._error = "", "", False
            self._pending = None
            self.rowsChanged.emit()
            self.changed.emit()
            self.checkRepairs()

    @Slot()
    def refresh(self):
        # In the update list this checks for updates: AppImages consult the servers.
        self._force_check = self._mode == "update"
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
            self._repairs = [item for item in self._repairs if item[0] in remaining]
            self._items = [
                item for item in self._items if item.method == "system" or item.id in remaining
            ]
            self._status = (
                tr("AppImage uninstalled. Personal data was preserved.")
                if self._removal_method == "appimage"
                else tr("Snap uninstalled. Personal data was preserved.")
                if self._removal_method == "snap"
                else tr("Application uninstalled. The container and personal data were preserved.")
            )
        self._selected = -1
        if isinstance(result, ManagedContainer):
            self._unused_container, self._container_error = result, ""
            self._remove_container_home = False
            self.changed.emit()
            self.containerRemovalRequested.emit()

    @staticmethod
    def _snap_item_name(item):
        return item.name if isinstance(item, snap_updates.Runtime) else item.package_name

    def _run_snap_update(self, item, operation, completed, retry):
        from multipak.snap import ApplicationRunning

        def run(engine):
            try:
                return operation(engine)
            except ApplicationRunning as error:
                return error

        def finished(result):
            self._snap_permission_reply = None
            if isinstance(result, ApplicationRunning):
                self._pending_snap_close = (item, result.transaction, retry)
                self._status = ""
                self.changed.emit()
                self.updateClosingRequested.emit()
                return
            completed(result)

        self._clear_logs()
        self._error = False
        self._run(run, finished, lambda error: self._snap_update_failed(item, error))

    def _start_snap_refresh(self, tx):
        from multipak.snap_refresh import refresh

        revision = self._snap_checks[tx.id].revision
        reply = self._snap_permission_reply = Event()
        self._snap_permission_approved = False

        def operation(engine):
            def confirm(name, permissions):
                self.snapPermissionsRequested.emit(
                    tr("{0} requests new permissions:").format(name)
                    + "\n\n"
                    + "\n".join(f"• {permission}" for permission in permissions)
                )
                while not reply.wait(0.2):
                    if engine.cancelled.is_set():
                        return False
                return self._snap_permission_approved

            return refresh(engine, tx, revision, confirm)

        self._run_snap_update(
            tx, operation, self._snap_refreshed, lambda: self._start_snap_refresh(tx)
        )

    @Slot(bool)
    def answerSnapPermissions(self, approved):
        if self._snap_permission_reply is not None:
            self._snap_permission_approved = approved
            self._snap_permission_reply.set()

    def _snap_update_failed(self, item, error):
        self._snap_permission_reply = None
        self._pending_snap_close = None
        # A failed switch may still have changed what is installed.
        self._sync_snap_items()
        if not self._queue_step("failed", self._snap_item_name(item), error):
            self._status, self._error = error, True

    def _snap_refreshed(self, tx):
        self._snap_permission_reply = None
        if tx is None:
            self._status = tr("Snap update cancelled.")
            if self._queue is not None:
                self._queue_step("skipped", self._queue["current"])
            return
        check = self._snap_checks[tx.id]
        self._snap_checks[tx.id] = replace(
            check, status="current", revision=tx.snap_revision, version=tx.package_version
        )
        # The snap may now use other revisions of its base and content.
        self._sync_snap_items(tx)
        self._progress = 100
        if not self._queue_step("updated", tx.package_name, warnings=tx.warnings):
            self._status = tr("{0} was updated.").format(tx.package_name)

    @Slot()
    def revertSelectedSnap(self):
        if self.working or self.preview or not self.hasPreviousSnap:
            return
        from multipak.snap_refresh import revert

        item = self._items[self._selected]
        self._logs, self._error = "", False
        self._run(lambda engine: revert(engine, item), self._snap_reverted)

    def _snap_reverted(self, tx):
        self._sync_snap_items(tx)
        self._progress = 100
        self._status = tr("{0} returned to version {1} (revision {2}).").format(
            tx.package_name, tx.package_version, tx.snap_revision
        )

    def _start_runtime_update(self, item):
        from multipak.snap_runtime_updates import update

        revision = self._snap_checks[item.id].revision

        def operation(engine):
            current = next(
                (other for other in snap_updates.managed(self.store) if other.id == item.id), None
            )
            if current is None:
                return None
            return update(engine, current, revision)

        self._run_snap_update(
            item,
            operation,
            lambda result: self._runtime_updated(result, item.name),
            lambda: self._start_runtime_update(item),
        )

    def _runtime_updated(self, runtime, name):
        if runtime is None:
            if not self._queue_step("skipped", name):
                self._status = tr("The selected snap base or content is unavailable.")
            return
        self._sync_snap_items(runtime)
        self._progress = 100
        if not self._queue_step("updated", runtime.name):
            self._status = tr("{0} was updated.").format(runtime.name)

    @Slot()
    def updateSelected(self):
        if not self.canAct or self._mode != "update":
            return
        item = self._items[self._selected]
        if isinstance(item, Transaction):
            if self.preview:
                self._status = tr("This is a preview. No update was started.")
                self.changed.emit()
                return
            if item.method == "snap":
                self._start_snap_refresh(item)
            else:
                self._start_appimage_update()
            return
        if isinstance(item, snap_updates.Runtime):
            self._start_runtime_update(item)
            return
        if isinstance(item, ContainerApp):
            others = sorted(
                {
                    other.tx.package_name
                    for other in self._items
                    if isinstance(other, ContainerApp)
                    and other.env.name == item.env.name
                    and other.tx.id != item.tx.id
                },
                key=str.casefold,
            )
            if others:
                self._group_update = (item.env.name, item.tx.package_name, others)
                self.changed.emit()
                self.groupUpdateRequested.emit()
                return
            self._update_container(item.env.name, item.tx.package_name)
            return
        self._update_container(item.name, item.display_name)

    def _update_container(self, name, label):
        self._logs, self._error = "", False
        if self.preview:
            self._status = tr("This is a preview. No update was started.")
            self.changed.emit()
            return
        self._status = tr("Updating {0}… Please wait.").format(label)
        self._run(
            lambda engine: self.updater(engine, name),
            lambda result: self._container_done(name, result),
        )

    def _updated(self, _result):
        self._progress = 100
        if self._queue is not None:
            self._queue_step("updated", self._queue["current"])
            return
        self._status = tr(
            "Update complete. All applications and packages in the container are up to date."
        )

    def _clear_logs(self):
        # "Update all" keeps the details of every step.
        if self._queue is None:
            self._logs = ""

    @Slot()
    def updateAll(self):
        """Update containers, AppImages, snaps and shared runtimes one at a time."""
        if not self.canUpdateAll:
            return
        if self.preview:
            self._status = tr("This is a preview. No update was started.")
            self.changed.emit()
            return
        self._queue = {
            "items": [],
            "current": "",
            "updated": [],
            "failed": [],
            "skipped": [],
            "no_source": [],
            "unchecked_snaps": [],
            "warnings": [],
        }
        self._selected, self._logs, self._error = -1, "", False
        self._status = tr("Checking for updates…")
        applications = any(
            isinstance(item, Transaction) and item.method == "appimage" for item in self._items
        )

        def operation(engine):
            from multipak.appimage_updates import check

            # Recent results are reused; the others are checked now.
            return check(engine) if applications else {}

        self._run(operation, self._queue_checked, self._queue_check_failed)

    def _queue_checked(self, checks):
        self._checks = dict(self._checks) | checks
        self.rowsChanged.emit()
        queue = self._queue
        repairs = {identifier for identifier, _ in self._repairs}
        for item in self._items:
            if isinstance(item, ContainerApp):
                # The container is updated once, for all its applications.
                if item.env not in queue["items"]:
                    queue["items"].append(item.env)
                continue
            if isinstance(item, ExistingEnvironment):
                queue["items"].append(item)
                continue
            if isinstance(item, snap_updates.Runtime) or (
                isinstance(item, Transaction) and item.method == "snap"
            ):
                if self._snap_update_available(item):
                    queue["items"].append(item)
                elif isinstance(item, snap_updates.Runtime) or item.snap_revision != LOCAL_REVISION:
                    check = self._snap_checks.get(item.id)
                    name = self._snap_item_name(item)
                    if check is None or check.stale:
                        queue["unchecked_snaps"].append(name)
                    elif check.status == "failed":
                        queue["failed"].append((name, check.error))
                continue
            if not isinstance(item, Transaction) or item.method != "appimage":
                continue
            check = self._checks.get(item.id)
            status = check.status if check is not None else "failed"
            if item.id in repairs:
                queue["failed"].append(
                    (item.package_name, tr("This application's container was replaced."))
                )
            elif status == "available":
                queue["items"].append(item)
            elif status == "no-source":
                queue["no_source"].append(item.package_name)
            elif status == "failed":
                queue["failed"].append(
                    (item.package_name, check.error if check is not None else "")
                )
        QTimer.singleShot(0, self._next_update)

    def _queue_check_failed(self, message):
        self._queue = None
        self._status, self._error = tr("Could not complete: ") + message, True

    def _queue_step(self, outcome, name, message="", *, warnings=()):
        """Record how one item of "Update all" ended and go on to the next one."""
        queue = self._queue
        if queue is None:
            return False
        if outcome == "failed":
            queue["failed"].append((name, message))
        else:
            queue[outcome].append(name)
        queue["warnings"].extend(warnings)
        self.changed.emit()
        QTimer.singleShot(0, self._next_update)
        return True

    @Slot()
    def _next_update(self):
        queue = self._queue
        if queue is None or self.busy:
            return
        if not queue["items"]:
            self._finish_queue()
            return
        item = queue["items"].pop(0)
        if isinstance(item, snap_updates.Runtime):
            current = next(
                (other for other in self._items if getattr(other, "id", None) == item.id), None
            )
            if current is None or not self._snap_update_available(current):
                # A snap updated earlier in the queue no longer uses it, or every snap
                # already uses the new revision: nothing is left to do.
                QTimer.singleShot(0, self._next_update)
                return
            queue["current"] = current.name
            self._start_runtime_update(current)
            return
        if isinstance(item, Transaction):
            queue["current"] = item.package_name
            if item.method == "snap":
                self._start_snap_refresh(item)
            else:
                self._start_appimage_update(item)
            return
        name = item.name
        label = self._container_label(name)
        if label == name:
            label = item.display_name
        queue["current"] = label
        self._status, self._error = tr("Updating {0}… Please wait.").format(label), False
        self._run(
            lambda engine: self.updater(engine, name),
            lambda result: self._container_done(name, result),
            lambda message: self._queue_step("failed", label, message),
        )

    def _finish_queue(self):
        queue, self._queue = self._queue, None
        self._sync_snap_items()
        lines = []
        if queue["updated"]:
            lines.append(tr("Updated: {0}").format(", ".join(queue["updated"])))
        lines.extend(queue["warnings"])
        if queue["failed"]:
            failures = [
                f"{name}: {message}" if message else name for name, message in queue["failed"]
            ]
            lines.append(tr("Could not update:") + "\n" + "\n".join(failures))
        if queue["skipped"]:
            lines.append(tr("Not updated: {0}").format(", ".join(queue["skipped"])))
        if queue["no_source"]:
            lines.append(
                tr(
                    "These AppImages do not indicate their update address. Select each one "
                    "and choose Update: {0}"
                ).format(", ".join(queue["no_source"]))
            )
        if queue["unchecked_snaps"]:
            lines.append(
                tr("Check for updates to include these snaps, bases or content: {0}").format(
                    ", ".join(queue["unchecked_snaps"])
                )
            )
        self._status = "\n\n".join(lines) or tr("Everything is up to date.")
        self._error = bool(queue["failed"])
        self._progress = 100
        self._resort()
        self.rowsChanged.emit()
        self.changed.emit()

    @Slot()
    def requestClose(self):
        if self.working:
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
    if not controller.preview:
        migration.when_ready(controller.checkRepairs)
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
    # Sample results, so the update list shows its groups.
    controller._package_checks = {
        "a" * 32: package_updates.Check("available", "1.2.31", "1.2.33"),
        "b" * 32: package_updates.Check("current", "142.0.7444.59"),
    }
    return create_manager(app, controller)


def main():
    parser = argparse.ArgumentParser(description=tr("Application Manager — MultiPak"))
    parser.add_argument("--preview", action="store_true")
    parser.add_argument(
        "--page", choices=("home", "install", "remove", "update", "services"), default="home"
    )
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
    if args.page == "install":
        controller._legacy_migration.when_ready(controller.openInstall)
    elif args.page != "home":
        controller._legacy_migration.when_ready(lambda: controller.openList(args.page))
    window.requestActivate()
    if args.screenshot:

        def capture():
            if controller.busy:
                QTimer.singleShot(100, capture)
            else:
                app.exit(0 if window.grabWindow().save(str(args.screenshot)) else 1)

        QTimer.singleShot(1000, capture)
    try:
        return app.exec()
    finally:
        release_engine(app, engine, window)


if __name__ == "__main__":
    raise SystemExit(main())

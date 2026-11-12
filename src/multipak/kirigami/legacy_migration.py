"""Check and remove the Alpha Flatpak before opening the current interface."""

import logging
import subprocess
from threading import Thread

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from multipak import host
from multipak.appimage import migrate_legacy_launchers
from multipak.file_associations import FileAssociations
from multipak.localization import tr
from multipak.store import Store

LOG = logging.getLogger(__name__)
SCOPES = ("--user", "--system")


def _installed():
    installed = {}
    errors = []
    for scope in SCOPES:
        try:
            result = host.run(
                ["flatpak", "list", scope, "--app", "--columns=ref"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            refs = []
            for line in result.stdout.splitlines():
                parts = line.strip().removeprefix("app/").split("/")
                if len(parts) == 3 and parts[0] == host.LEGACY_APP_ID and all(parts):
                    refs.append("app/" + "/".join(parts))
            if refs:
                installed[scope] = refs
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(error))
    return installed, errors


class LegacyMigration(QObject):
    changed = Signal()
    ready = Signal()
    completed = Signal(bool, str)

    def __init__(self, parent=None, *, enabled=False):
        super().__init__(parent)
        self._checking = enabled
        self._required = False
        self._error = ""
        self.completed.connect(self._finished)

    @Property(bool, notify=changed)
    def checking(self):
        return self._checking

    @Property(bool, notify=changed)
    def required(self):
        return self._required

    @Property(str, notify=changed)
    def error(self):
        return self._error

    def when_ready(self, callback):
        if self.checking or self.required:
            self.ready.connect(callback)
        else:
            QTimer.singleShot(0, callback)

    def start(self):
        if self.checking:
            self._run(remove=False)

    @Slot()
    def remove(self):
        if self.checking or not self.required:
            return
        self._checking = True
        self._error = ""
        self.changed.emit()
        self._run(remove=True)

    def _run(self, *, remove):
        def work():
            try:
                required, error = self._check(remove=remove)
            except Exception:
                LOG.exception("Legacy MultiPak migration failed")
                required = remove
                error = (
                    tr("Could not migrate the old MultiPak. Try again or close this window.")
                    if remove
                    else ""
                )
            try:
                self.completed.emit(required, error)
            except RuntimeError:
                pass

        Thread(target=work, daemon=True).start()

    @staticmethod
    def _check(*, remove):
        scopes, errors = _installed()
        if errors:
            LOG.warning("Could not check for the old MultiPak: %s", "; ".join(errors))
        if scopes and not remove:
            return True, ""
        try:
            migrate_legacy_launchers(Store())
        except (OSError, ValueError, RuntimeError):
            LOG.exception("Could not migrate the old AppImage shortcuts")
            if remove:
                return True, tr(
                    "Could not update the old AppImage shortcuts. Try again or close this window."
                )
        if remove:
            uninstall_error = ""
            for scope, refs in scopes.items():
                try:
                    host.run(
                        ["flatpak", "uninstall", scope, "--assumeyes", *refs],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=300,
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    uninstall_error = getattr(error, "stderr", None) or str(error)
                    break
            remaining, check_errors = _installed()
            if check_errors:
                LOG.warning("Could not recheck the old MultiPak: %s", "; ".join(check_errors))
            if remaining or (uninstall_error and check_errors):
                detail = uninstall_error.strip()[:1000]
                message = tr(
                    "Could not remove the old MultiPak. You can remove it from a terminal "
                    "and then try again:\n\nflatpak uninstall io.github.multipak.manager"
                )
                return True, f"{message}\n\n{detail}" if detail else message
        elif errors:
            # An unavailable Flatpak command is not proof that the old app is installed.
            return False, ""
        try:
            FileAssociations().migrate_legacy(removed=remove)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            LOG.exception("Could not migrate the old file associations")
            if remove:
                return True, tr(
                    "Could not update the old file associations. Try again or close this window."
                )
        return False, ""

    @Slot(bool, str)
    def _finished(self, required, error):
        self._required, self._error, self._checking = required, error, False
        self.changed.emit()
        if not required:
            self.ready.emit()


def bind_legacy_migration(engine, controller, *, preview=False):
    migration = LegacyMigration(engine, enabled=not preview and host.is_flatpak())
    controller._legacy_migration = migration
    engine.rootContext().setContextProperty("legacyMigration", migration)
    QTimer.singleShot(0, migration.start)
    return migration

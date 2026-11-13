"""Nonblocking startup update notices shared by the manager and installer."""

from pathlib import Path
from threading import Thread

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from multipak import __version__, host
from multipak.localization import tr
from multipak.update_notice import NoticeState

KEY = Path(__file__).parents[1] / "assets/update-signing.gpg"


class UpdateNotice(QObject):
    changed = Signal()
    offered = Signal()
    completed = Signal(object)

    def __init__(self, parent=None, *, enabled=False, state=None, key=KEY):
        super().__init__(parent)
        self.state = state or NoticeState()
        self.key = key
        self._checking = enabled
        self._enabled = enabled
        self.policy = None
        self._error = ""
        self.completed.connect(self._received)

    @Property(bool, notify=changed)
    def checking(self):
        return self._checking

    @Property(bool, notify=changed)
    def required(self):
        return bool(self.policy and self.policy.required(__version__))

    @Property(bool, notify=changed)
    def installAllowed(self):
        return not self.checking and not self.required

    @Property(str, constant=True)
    def currentVersion(self):
        return __version__

    @Property(str, notify=changed)
    def latestVersion(self):
        return self.policy.latest if self.policy else ""

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Slot()
    def start(self):
        if not self._enabled:
            return
        self._enabled = False

        def check():
            try:
                result = self.state.checked_policy(self.key)
            except Exception:
                result = None
            try:
                self.completed.emit(result)
            except RuntimeError:
                pass

        Thread(target=check, daemon=True).start()

    @Slot(object)
    def _received(self, policy):
        self.policy = policy
        self._checking = False
        self.changed.emit()
        if policy and self.state.should_show(policy, __version__):
            self.offered.emit()

    @Slot(result=bool)
    def remindLater(self):
        if not self.policy or self.required or not self.policy.available(__version__):
            return False
        try:
            self.state.remember_later()
        except OSError:
            self._error = tr("Could not save the update reminder.")
            self.changed.emit()
            return True
        return True

    @Slot()
    def openStore(self):
        if not QDesktopServices.openUrl(QUrl("appstream://" + host.APP_ID)):
            self._error = tr("Could not open the software store.")
            self.changed.emit()


def bind_update_notice(engine, controller, *, preview=False, migration=None):
    notice = UpdateNotice(engine, enabled=not preview and KEY.is_file())
    engine._update_notice = notice
    controller._application_updates = notice
    engine.rootContext().setContextProperty("appUpdates", notice)
    if migration is None:
        QTimer.singleShot(0, notice.start)
    else:
        migration.when_ready(notice.start)
    return notice


def installation_allowed(controller):
    migration = getattr(controller, "_legacy_migration", None)
    if migration is not None and (migration.checking or migration.required):
        return False
    notice = getattr(controller, "_application_updates", None)
    return notice is None or notice.installAllowed

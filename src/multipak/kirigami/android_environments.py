"""Qt controller for the owned Android environment list."""

from PySide6.QtCore import Property, QLocale, QObject, Signal, Slot

from multipak import android_instances as environments
from multipak import android_stats
from multipak.localization import tr
from multipak.worker import Worker


class AndroidEnvironments(QObject):
    changed = Signal()
    statsChanged = Signal()

    def __init__(self, store, parent=None, *, worker_factory=Worker, preview=False):
        super().__init__(parent)
        self.preview = preview
        self.store = store
        self.worker_factory = worker_factory
        self.worker = None
        self._rows = []
        self._selected = -1
        self._status = ""
        self._error = False
        self._logs = ""
        self._progress = -1
        self._outcome = None
        self._operation = ""
        self._stats_worker = None
        self._stats_result = {}
        self._stats_samples = {}
        self._stats = {}
        self._stats_generation = 0
        self._sampling_generation = 0

    @Property("QVariantMap", notify=statsChanged)
    def stats(self):
        return self._stats

    @Slot()
    def refreshStats(self):
        if self.preview or self.busy or self._stats_worker is not None or not self._rows:
            return
        rows = [dict(row) for row in self._rows]
        self._stats_result = {}
        self._sampling_generation = self._stats_generation
        worker = self.worker_factory(
            self.store, lambda engine: android_stats.sample(engine.runner, rows), self
        )
        self._stats_worker = worker
        worker.succeeded.connect(self._stats_ready)
        worker.finished.connect(self._stats_finished)
        worker.start()

    @Slot(object)
    def _stats_ready(self, result):
        self._stats_result = result

    @Slot()
    def _stats_finished(self):
        worker, self._stats_worker = self._stats_worker, None
        if worker is None:
            return
        worker.deleteLater()
        if self.busy or self._sampling_generation != self._stats_generation:
            return
        locale = QLocale()
        stats = {}
        for row in self._rows:
            identifier = row["id"]
            current = self._stats_result.get(identifier)
            if current is None:
                continue
            cpu = android_stats.cpu_percent(self._stats_samples.get(identifier), current)
            stats[identifier] = {
                "cpu": "—" if cpu is None else locale.toString(cpu, "f", 1) + "%",
                "memory": locale.formattedDataSize(current["memory"]),
            }
        self._stats_samples = self._stats_result
        self._stats = stats
        self.statsChanged.emit()

    @Slot()
    def stopStats(self):
        worker, self._stats_worker = self._stats_worker, None
        if worker is not None:
            worker.cancel_requested.set()
            worker.wait()
            worker.deleteLater()

    @Property(bool, notify=changed)
    def busy(self):
        return self.worker is not None

    @Property("QVariantList", notify=changed)
    def rows(self):
        return self._rows

    @Property("QVariantList", constant=True)
    def profiles(self):
        return [{"id": p[0], "label": p[1]} for p in environments.PROFILES]

    @Property(int, notify=changed)
    def selectedIndex(self):
        return self._selected

    @Property(bool, notify=changed)
    def canAct(self):
        return not self.busy and 0 <= self._selected < len(self._rows)

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(str, notify=changed)
    def logs(self):
        return self._logs

    @Property(bool, notify=changed)
    def hasError(self):
        return self._error

    @Property(int, notify=changed)
    def progress(self):
        return self._progress

    @Property(bool, notify=changed)
    def canCancel(self):
        return self.busy and self._operation in ("create", "refresh")

    @Property(str, notify=changed)
    def selectedName(self):
        return self._rows[self._selected]["label"] if self.canAct else ""

    @Slot(int)
    def select(self, index):
        if not self.busy and -1 <= index < len(self._rows):
            self._selected = index
            self.changed.emit()

    @Slot(str, result=bool)
    def selectInstance(self, identifier):
        if self.busy:
            return False
        index = next((i for i, row in enumerate(self._rows) if row["id"] == identifier), -1)
        if index < 0:
            return False
        self.select(index)
        return True

    @Slot(str, str)
    def actOn(self, identifier, action):
        if self.selectInstance(identifier):
            self.act(action)

    @Slot(str, bool)
    def removeInstance(self, identifier, delete_data):
        if self.selectInstance(identifier):
            self.remove(delete_data)

    @Slot(str)
    def updateInstance(self, identifier):
        if not self.selectInstance(identifier):
            return

        def operation(engine):
            with engine.store.lock():
                updated = environments.upgrade(engine.runner, identifier, progress=engine.progress)
            return {
                "rows": environments.discover_with_status(engine.runner),
                "message": tr("Android container updated.")
                if updated
                else tr("The Android container is already up to date."),
            }

        self._run(operation, "upgrade")

    @Slot()
    def refresh(self):
        self._run(lambda e: environments.discover_with_status(e.runner), "refresh")

    @Slot(str, str)
    def create(self, profile, label):
        def operation(engine):
            environments.create(engine.runner, profile, label, progress=engine.progress)
            return environments.discover_with_status(engine.runner)

        self._run(operation, "create")

    @Slot(str)
    def act(self, action):
        if not self.canAct or action not in ("open", "start", "stop"):
            return
        identifier = self._rows[self._selected]["id"]

        def operation(engine):
            android = environments.InstanceWaydroid(engine.runner, identifier)
            if action == "stop":
                android.stop()
            else:
                android.ready(engine.progress)
                if action == "open":
                    android.command("show-full-ui")
            return environments.discover_with_status(engine.runner)

        self._run(operation, action)

    @Slot(bool)
    def remove(self, delete_data):
        if not self.canAct:
            return
        identifier = self._rows[self._selected]["id"]

        def operation(engine):
            with engine.store.lock():
                environments.remove(engine.runner, identifier, delete_data=delete_data)
                for tx in engine.store.transactions():
                    if tx.method == "waydroid" and tx.android_instance_id == identifier:
                        for name in tx.launchers:
                            engine.store.launcher(tx, name).unlink(missing_ok=True)
                        engine.store.remove_directory(tx)
            return environments.discover_with_status(engine.runner)

        self._run(operation, "remove")

    @Slot()
    def cancel(self):
        if self.canCancel:
            self.worker.cancel_requested.set()

    def _run(self, operation, mode):
        if self.preview:
            self._status = tr("This is a preview. No Android environment was changed.")
            self.changed.emit()
            return
        if self.busy:
            return
        self._stats_generation += 1
        self._stats_samples = {}
        self._stats = {}
        self.statsChanged.emit()
        if self._stats_worker is not None:
            self._stats_worker.cancel_requested.set()
        self._operation = mode
        self._logs = ""
        self._error = False
        self._progress = -1
        self._status = {
            "refresh": tr("Loading Android environments…"),
            "create": tr("Creating the Android container…"),
            "start": tr("Starting the Android container…"),
            "stop": tr("Stopping the Android container…"),
            "open": tr("Opening Android…"),
            "remove": tr("Removing the Android container…"),
            "upgrade": tr("Updating the Android container…"),
        }.get(mode, tr("Preparing the Android environment…"))
        self._outcome = (False, tr("The operation was not completed."))
        self.worker = self.worker_factory(self.store, operation, self)
        self.worker.succeeded.connect(self._success)
        self.worker.failed.connect(self._failure)
        self.worker.abandoned.connect(self._cancelled)
        self.worker.progress.connect(self._update)
        self.worker.log.connect(self._log)
        self.worker.finished.connect(self._finished)
        self.changed.emit()
        self.worker.start()

    @Slot(object)
    def _success(self, value):
        self._outcome = (True, value)

    @Slot(str, object)
    def _failure(self, message, _tx):
        self._outcome = (False, message)

    @Slot()
    def _cancelled(self):
        self._outcome = (False, tr("The operation was cancelled."))

    @Slot(int, str)
    def _update(self, percent, status):
        self._progress = percent
        self._status = status
        self.changed.emit()

    @Slot(str)
    def _log(self, text):
        self._logs = (self._logs + text + "\n")[-200000:]
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        ok, value = self._outcome
        self._error = not ok
        if ok:
            self._rows = value["rows"] if self._operation == "upgrade" else value
            self._selected = -1
            self._status = (
                value["message"]
                if self._operation == "upgrade"
                else ("" if self._operation == "refresh" else tr("Operation completed."))
            )
        else:
            self._status = str(value)
        self.changed.emit()

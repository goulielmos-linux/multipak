"""Qt controller for the owned Android environment list."""

from PySide6.QtCore import Property, QLocale, QObject, QUrl, Signal, Slot

from multipak import android_instances as environments
from multipak import android_link_choice, android_stats
from multipak.localization import tr
from multipak.worker import Worker


class AndroidEnvironments(QObject):
    changed = Signal()
    statsChanged = Signal()
    sendChanged = Signal()
    microphoneChanged = Signal()
    cameraChanged = Signal()

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
        self._send = {"text": "", "progress": -1.0, "error": "", "done": False}
        self._send_worker = None
        self._send_outcome = None
        self._ask_links = None
        self._microphone = {}
        self._microphone_error = ""
        self._microphone_worker = None
        self._microphone_outcome = None
        self._camera = {}
        self._camera_error = ""
        self._camera_worker = None
        self._camera_outcome = None

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
        decimal = QLocale.DataSizeFormat.DataSizeSIFormat  # GB, like Kontainer.
        stats = {}
        for row in self._rows:
            identifier = row["id"]
            current = self._stats_result.get(identifier)
            if current is None:
                continue
            cpu = android_stats.cpu_percent(self._stats_samples.get(identifier), current)
            stats[identifier] = {
                "cpu": "—" if cpu is None else locale.toString(cpu, "f", 1) + "%",
                # As Kontainer shows it: what the container uses of the computer's memory.
                "memory": locale.formattedDataSize(current["memory"], 2, decimal)
                + " / "
                + locale.formattedDataSize(android_stats.total_memory(), 2, decimal),
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

    @Slot(str, bool)
    def setKeepReady(self, identifier, enabled):
        if not self.selectInstance(identifier):
            return
        label = self._rows[self._selected]["label"]

        def operation(engine):
            environments.set_keep_ready(identifier, enabled, label)
            if enabled:
                environments.InstanceWaydroid(engine.runner, identifier).ready(engine.progress)
            return environments.discover_with_status(engine.runner)

        self._run(operation, "ready" if enabled else "refresh")

    @Slot(str, bool)
    def setKeyboard(self, identifier, enabled):
        if not self.selectInstance(identifier):
            return

        def operation(engine):
            environments.set_keyboard(engine.runner, identifier, enabled)
            return environments.discover_with_status(engine.runner)

        self._run(operation, "refresh")

    @Property(bool, notify=changed)
    def askLinks(self):
        if self._ask_links is None:
            try:
                self._ask_links = not self.preview and android_link_choice.asking()
            except Exception:
                self._ask_links = False
        return self._ask_links

    @Slot(bool)
    def setAskLinks(self, enabled):
        if self.preview or self.busy:
            self.changed.emit()  # Puts the box back.
            return

        def operation(engine):
            try:
                android_link_choice.refresh(bool(enabled))
            except Exception:
                self._ask_links = None  # Read the saved choice again.
                raise
            return environments.discover_with_status(engine.runner)

        self._ask_links = bool(enabled)
        self._run(operation, "refresh")

    @Property("QVariantMap", notify=sendChanged)
    def sending(self):
        """text, progress (0 to 1, or -1 while unknown), error and done, for the send window."""
        return {**self._send, "busy": self._send_worker is not None}

    @Slot(str, "QVariantList")
    def sendFiles(self, identifier, urls):
        if self.preview or self._send_worker is not None:
            return
        urls = [url if isinstance(url, QUrl) else QUrl(str(url)) for url in urls]
        paths = [url.toLocalFile() or url.toString() for url in urls]
        self._send = {"text": tr("Sending…"), "progress": -1.0, "error": "", "done": False}
        self._send_outcome = None

        def operation(engine):
            def progress(done, total, name):
                engine.progress(1000 * done // total if total else 1000, name)

            environments.send_files(engine.runner, identifier, paths, progress)

        worker = self.worker_factory(self.store, operation, self)
        self._send_worker = worker
        worker.progress.connect(self._send_progress)
        worker.succeeded.connect(self._send_ready)
        worker.failed.connect(self._send_failed)
        worker.finished.connect(self._send_finished)
        self.sendChanged.emit()
        worker.start()

    @Slot()
    def cancelSend(self):
        if self._send_worker is not None:
            self._send_worker.cancel_requested.set()

    @Slot(int, str)
    def _send_progress(self, permille, name):
        self._send["text"] = tr("Sending {0}.").format(name)
        self._send["progress"] = permille / 1000
        self.sendChanged.emit()

    @Slot(object)
    def _send_ready(self, _result):
        self._send_outcome = (True, "")

    @Slot(str, object)
    def _send_failed(self, message, _tx):
        self._send_outcome = (False, message)

    @Slot()
    def _send_finished(self):
        worker, self._send_worker = self._send_worker, None
        worker.deleteLater()
        # Cancelled: neither succeeded nor failed, and the window just closes.
        ok, message = self._send_outcome or (True, "")
        self._send["done"] = ok and self._send_outcome is not None
        self._send["text"] = tr("Done") if self._send["done"] else self._send["text"]
        self._send["error"] = "" if ok else str(message)
        self.sendChanged.emit()

    @Property("QVariantList", notify=microphoneChanged)
    def microphones(self):
        return self._microphone.get("options", [])

    @Property(int, notify=microphoneChanged)
    def microphoneIndex(self):
        return self._microphone.get("index", -1)

    @Property(bool, notify=microphoneChanged)
    def microphoneBusy(self):
        return self._microphone_worker is not None

    @Property(str, notify=microphoneChanged)
    def microphoneError(self):
        return self._microphone_error

    @Slot(str)
    def loadMicrophone(self, identifier):
        self._microphone = {}
        self._choose_microphone(identifier)

    @Slot(str, str)
    def setMicrophone(self, identifier, name):
        self._choose_microphone(identifier, name)

    def _choose_microphone(self, identifier, *choice):
        if self.preview or self._microphone_worker is not None:
            return
        self._microphone_error = ""
        self._microphone_outcome = None
        worker = self.worker_factory(
            self.store,
            lambda engine: environments.microphone(engine.runner, identifier, *choice),
            self,
        )
        self._microphone_worker = worker
        worker.succeeded.connect(self._microphone_ready)
        worker.failed.connect(self._microphone_failed)
        worker.finished.connect(self._microphone_finished)
        self.microphoneChanged.emit()
        worker.start()

    @Slot(object)
    def _microphone_ready(self, value):
        self._microphone_outcome = (True, value)

    @Slot(str, object)
    def _microphone_failed(self, message, _tx):
        self._microphone_outcome = (False, message)

    @Slot()
    def _microphone_finished(self):
        worker, self._microphone_worker = self._microphone_worker, None
        worker.deleteLater()
        ok, value = self._microphone_outcome or (False, tr("The operation was not completed."))
        if ok:
            self._microphone = value
            if not value["listed"]:
                self._microphone_error = tr("The microphones could not be listed.")
        else:
            self._microphone_error = str(value)
        self.microphoneChanged.emit()

    @Property("QVariantList", notify=cameraChanged)
    def cameras(self):
        return self._camera.get("options", [])

    @Property(int, notify=cameraChanged)
    def cameraIndex(self):
        return self._camera.get("index", -1)

    @Property(str, notify=cameraChanged)
    def cameraNote(self):
        return self._camera.get("note", "")

    @Property(bool, notify=cameraChanged)
    def cameraBusy(self):
        return self._camera_worker is not None

    @Property(str, notify=cameraChanged)
    def cameraError(self):
        return self._camera_error

    @Slot(str)
    def loadCamera(self, identifier):
        self._camera = {}
        self._choose_camera(identifier)

    @Slot(str, str)
    def setCamera(self, identifier, name):
        self._choose_camera(identifier, name)

    def _choose_camera(self, identifier, *choice):
        if self.preview or self._camera_worker is not None:
            return
        self._camera_error = ""
        self._camera_outcome = None
        worker = self.worker_factory(
            self.store,
            lambda engine: environments.camera(engine.runner, identifier, *choice),
            self,
        )
        self._camera_worker = worker
        worker.succeeded.connect(self._camera_ready)
        worker.failed.connect(self._camera_failed)
        worker.finished.connect(self._camera_finished)
        self.cameraChanged.emit()
        worker.start()

    @Slot(object)
    def _camera_ready(self, value):
        self._camera_outcome = (True, value)

    @Slot(str, object)
    def _camera_failed(self, message, _tx):
        self._camera_outcome = (False, message)

    @Slot()
    def _camera_finished(self):
        worker, self._camera_worker = self._camera_worker, None
        worker.deleteLater()
        ok, value = self._camera_outcome or (False, tr("The operation was not completed."))
        if ok:
            self._camera = value
        else:
            self._camera_error = str(value)
        self.cameraChanged.emit()

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
        if not self.canAct or action not in ("open", "start", "stop", "restart"):
            return
        identifier = self._rows[self._selected]["id"]

        def operation(engine):
            android = environments.InstanceWaydroid(engine.runner, identifier)
            if action in ("stop", "restart"):
                android.stop()
            if action != "stop":
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
            "restart": tr("Restarting Android…"),
            "open": tr("Opening Android…"),
            "remove": tr("Removing the Android container…"),
            "upgrade": tr("Updating the Android container…"),
            "ready": tr("Starting the Android container…"),
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

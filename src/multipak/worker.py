"""One worker per operation; all widget access stays on the GUI thread."""

import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from multipak.localization import tr

from .engine import Engine
from .process import Cancelled
from .store import Store


class Worker(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str, object)
    abandoned = Signal()

    def __init__(self, store: Store, operation: Callable, parent=None):
        super().__init__(parent)
        self.cancel_requested = threading.Event()
        self.engine = Engine(store, self.cancel_requested, self.progress.emit, self.log.emit)
        self.operation = operation

    def run(self) -> None:
        try:
            result = self.operation(self.engine)
            if self.cancel_requested.is_set():
                raise Cancelled
        except Cancelled:
            try:
                if self.engine.tx:
                    self.progress.emit(-1, tr("Cleaning up the installation…"))
                    self.engine.cleanup(self.engine.tx)
                self.abandoned.emit()
            except Exception as exc:
                self.failed.emit(str(exc), self.engine.tx)
        except Exception as exc:
            self.failed.emit(str(exc), self.engine.tx)
        else:
            self.succeeded.emit(result)

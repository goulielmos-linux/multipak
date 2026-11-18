from types import SimpleNamespace

import pytest

from multipak.distrobox_setup import DistroboxRequirement, WaydroidMissing


def test_the_manager_offers_waydroid_instead_of_an_error(monkeypatch):
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QObject, Signal

    from multipak.kirigami import android_environments as module

    class Worker(QObject):
        succeeded = Signal(object)
        failed = Signal(str, object)
        abandoned = Signal()
        progress = Signal(int, str)
        log = Signal(str)
        finished = Signal()

        def __init__(self, _store, operation, parent):
            super().__init__(parent)
            self.operation = operation

        def start(self):
            try:
                self.succeeded.emit(
                    self.operation(SimpleNamespace(runner="runner", progress=lambda *_: None))
                )
            except Exception as error:
                self.failed.emit(str(error), None)
            self.finished.emit()

    purposes = []

    def create(*_args, **_kwargs):
        raise WaydroidMissing(
            "Install Waydroid on your system before preparing an Android environment."
        )

    def requirement(_engine, package, purpose):
        purposes.append((package, purpose))
        return DistroboxRequirement(True, False, purpose + " Install?", package=package)

    installs = []
    monkeypatch.setattr(module.environments, "create", create)
    monkeypatch.setattr(module, "host_requirement", requirement)
    monkeypatch.setattr(
        module, "install_host_package", lambda _e, package, _p: installs.append(package) or True
    )
    controller = module.AndroidEnvironments(None, worker_factory=Worker)
    controller.create("android13", "Games")
    assert purposes == [("waydroid", "MultiPak needs Waydroid to create Android containers.")]
    assert controller.waydroidSetup["canInstall"] and not controller.hasError
    assert "create Android containers" in controller.waydroidSetup["message"]
    controller.installWaydroid()
    assert installs == ["waydroid"]
    assert controller.waydroidSetup["restartRequired"]
    assert not controller.waydroidSetup["canInstall"]
    controller.dismissWaydroidSetup()
    assert controller.waydroidSetup == {}

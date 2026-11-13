"""Check Android installer UI with a real APK and a simulated installation."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.setdefault("MULTIPAK_LANGUAGE", "en")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak import host  # noqa: E402
from multipak.android_bundle import inspect_android  # noqa: E402
from multipak.kirigami.live import LiveInstaller  # noqa: E402
from multipak.kirigami.main import create_window  # noqa: E402
from multipak.localization import tr  # noqa: E402
from multipak.models import AndroidPlan, PackageInspection, Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


class SimulatedEngine:
    def __init__(self, worker):
        self.tx = None
        self.worker = worker
        self.calls = []

    def install_android(self, package, *, allow_translation=False, **options):
        assert allow_translation is True
        self.calls.append(allow_translation)
        self.worker.progress.emit(65, "Installing the Android application…")
        self.tx = Transaction(
            "a" * 32,
            "Waydroid",
            "",
            package.name,
            package.version,
            "apk",
            "hash",
            method="waydroid",
            status="installed",
            android_package_id=package.desktop_entry,
        )
        return self.tx


def factory(store, operation, parent):
    worker = Worker(store, operation, parent)
    worker.engine = SimulatedEngine(worker)
    return worker


def main():
    apk, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    host.is_flatpak = lambda: False
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix="multipak-android-ui-") as temporary:
        data = Path(temporary)
        os.environ["XDG_DATA_HOME"] = str(data / "xdg-data")
        os.environ["XDG_CONFIG_HOME"] = str(data / "xdg-config")
        store = Store(data / "installations", data / "applications")
        package = inspect_android(apk)
        bridge = LiveInstaller(apk, store=store, worker_factory=factory)
        arm_state = os.environ.get("MULTIPAK_CHECK_ARM_STATE", "native")
        plan = AndroidPlan(
            initialized=True,
            translation=arm_state,
            explanation="This Android version is not supported."
            if arm_state == "unsupported"
            else "",
        )
        bridge._inspected(PackageInspection(package, android=plan))
        engine, bridge, window = create_window(app, bridge)
        QTest.qWait(500)
        assert window.property("currentPage").objectName() == "welcomePage"
        bridge.choose("continue")
        QTest.qWait(100)
        assert window.property("currentPage").objectName() == "methodPage"
        bridge.chooseConfiguration("standard")
        bridge.android_plan = plan
        bridge.dataChanged.emit()
        QTest.qWait(100)
        page = window.property("currentPage")
        assert page.objectName() == "androidPage"
        assert bridge.selectedMethod == "waydroid" and bridge.environment is None
        assert bridge.isAndroidBundle == bool(package.android_parts)
        if package.android_parts:
            assert bridge.packaging == "Android APKM"
        assert window.grabWindow().save(str(output / "android-installation.png"))
        button = page.findChild(QObject, "androidInstallButton")
        assert page.findChild(QObject, "androidTranslationConsent") is None
        if arm_state == "unsupported":
            assert not button.property("enabled")
            bridge.requestInstallation()
            assert bridge.worker is None
        else:
            assert button.property("enabled")
            assert bridge.installation_allowed()
        if arm_state in ("required", "check", "available"):
            dependency = page.findChild(QObject, "androidTranslationDependency")
            assert dependency.isVisible()
            parent = dependency.parentItem()
            while parent is not None and parent.objectName() != "androidEnvironmentCard":
                parent = parent.parentItem()
            assert parent is not None
            assert page.findChild(QObject, "androidTranslationCard") is None
            assert window.grabWindow().save(str(output / "android-dependency.png"))
        if arm_state == "unsupported":
            window.close()
            engine.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            print("Unsupported ARM setup is shown before installation; install remains disabled.")
            return
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        for _ in range(100):
            if bridge.state == "success":
                break
            QTest.qWait(20)
        assert bridge.state == "success", bridge.status
        QTest.qWait(200)
        assert window.property("currentPage").objectName() == "completionPage"
        assert bridge.resultNote == tr("You can now open it from the application launcher.")
        assert window.grabWindow().save(str(output / "android-completion.png"))
        window.close()
        engine.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    print(
        "Android review, install-button dispatch and completion verified without host operations."
    )


if __name__ == "__main__":
    main()

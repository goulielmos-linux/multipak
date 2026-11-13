"""Exercise named Android selection and manager actions without privileged operations."""

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MULTIPAK_LANGUAGE", "pt_BR")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
from PySide6.QtCore import QEvent, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak import android_instances as environments  # noqa: E402
from multipak import host  # noqa: E402
from multipak.android_bundle import inspect_android  # noqa: E402
from multipak.kirigami.live import LiveInstaller  # noqa: E402
from multipak.kirigami.main import create_window  # noqa: E402
from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.models import AndroidPlan, PackageInspection, Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


def wait(predicate):
    for _ in range(250):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate()


def click(window, item):
    assert item is not None and item.property("enabled") and item.isVisible()
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(100)


def close(app, engine, window):
    window.close()
    engine.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def main():
    apk, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    host.is_flatpak = lambda: False
    calls = []
    with tempfile.TemporaryDirectory(prefix="multipak-environments-ui-") as directory:
        root = Path(directory)
        os.environ["XDG_DATA_HOME"] = str(root / "xdg-data")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg-config")
        store = Store(root / "installations", root / "applications")
        entries = [
            dict(
                id="androida",
                label="MultiPak-Android-13",
                profile="android-13",
                sdk=33,
                release="20.0",
                version="Android 13",
            ),
            dict(
                id="androidb",
                label="Jogos",
                profile="android-13",
                sdk=33,
                release="20.0",
                version="Android 13",
            ),
            dict(
                id="androidc",
                label="Aplicativos antigos",
                profile="android-11",
                sdk=30,
                release="18.1",
                version="Android 11",
            ),
        ]

        def factory(store, operation, parent):
            worker = Worker(store, operation, parent)

            def install(package, **kwargs):
                calls.append(("install", kwargs))
                return Transaction(
                    "a" * 32,
                    "Android",
                    "",
                    "Demo",
                    "1",
                    "apk",
                    "",
                    method="waydroid",
                    status="installed",
                    android_instance_id=kwargs["instance_id"],
                )

            worker.engine.install_android = install
            return worker

        package = inspect_android(apk)
        bridge = LiveInstaller(apk, store=store, worker_factory=factory)
        bridge._inspected(
            PackageInspection(package, android=AndroidPlan(), android_environments=tuple(entries))
        )
        engine, bridge, window = create_window(app, bridge)
        QTest.qWait(500)
        assert bridge.androidEnvironment == "androida"
        assert window.property("currentPage").objectName() == "welcomePage"
        bridge.choose("continue")
        QTest.qWait(100)
        assert window.property("currentPage").objectName() == "methodPage"
        bridge.chooseConfiguration("advanced")
        QTest.qWait(100)
        page = window.property("currentPage")
        bridge.chooseAndroidSuggested(False)
        bridge.chooseAndroidProfile("android-11")
        bridge.chooseAndroidContainerMode("existing")
        bridge.chooseAndroidEnvironment("androidc")
        QTest.qWait(200)
        assert bridge.androidEnvironment == "androidc" and bridge.androidSelectionValid
        assert len(bridge.androidEnvironments) == 2
        assert page.findChild(QObject, "androidProfileChoice").property("currentIndex") == 1
        assert window.grabWindow().save(str(output / "installer-android-11.png"))
        # ARM-only applications cannot silently download an unsupported automatic bridge.
        bridge.package = replace(package, architecture="arm64-v8a", android_parts=())
        bridge.chooseAndroidProfile("android-11")
        assert bridge.androidTranslationState == "unsupported" and not bridge.installation_allowed()
        bridge.package = replace(package, architecture="any")
        bridge.chooseAndroidProfile("android-13")
        bridge.chooseAndroidContainerMode("custom")
        bridge.setAndroidEnvironmentName("Android de testes")
        assert bridge.androidEnvironment == "" and bridge.androidSelectionValid
        bridge.setAndroidEnvironmentName("")
        assert not bridge.androidSelectionValid
        bridge.chooseAndroidProfile("android-11")
        bridge.chooseAndroidEnvironment("androidc")
        bridge.requestInstallation()
        wait(lambda: bridge.state == "success")
        assert calls[-1][1]["instance_id"] == "androidc"
        assert calls[-1][1]["android_profile"] == "android-11"
        close(app, engine, window)

        def create(runner, profile, label, **kwargs):
            calls.append(("create", profile, label))
            entries.append(
                dict(
                    id="androidd",
                    label=label,
                    profile=profile,
                    sdk=30,
                    release="18.1",
                    version="Android 11",
                )
            )
            return "androidd"

        def remove(runner, identifier, **kwargs):
            calls.append(("remove", identifier, kwargs))
            entries[:] = [e for e in entries if e["id"] != identifier]

        with (
            patch.object(environments, "discover", lambda _: list(entries)),
            patch.object(environments, "create", create),
            patch.object(environments, "remove", remove),
        ):
            controller = Manager(store)
            engine, controller, window = create_manager(app, controller)
            QTest.qWait(300)
            home_page = window.property("currentPage")
            # The Android containers card is on the manager's home page.
            click(window, home_page.findChild(QObject, "managerAndroidEnvironmentsButton"))
            wait(lambda: not controller.busy)
            QTest.qWait(300)
            page = window.property("currentPage")
            assert page.objectName() == "androidEnvironmentsPage"
            assert len(controller.android.rows) == 3
            assert window.grabWindow().save(str(output / "manager-android-environments.png"))
            controller.android.create("android-11", "Compatibilidade")
            wait(lambda: not controller.busy)
            assert calls[-1] == ("create", "android-11", "Compatibilidade")
            assert len(controller.android.rows) == 4
            controller.android.select(3)
            controller.android.remove(False)
            wait(lambda: not controller.busy)
            assert calls[-1] == ("remove", "androidd", {"delete_data": False})
            assert len(controller.android.rows) == 3 and not controller.android.hasError
            close(app, engine, window)
        # Preview screens must never run a backend operation.
        preview = Manager(store, preview=True)
        preview.android.create("android-13", "Preview")
        assert preview.android.worker is None
    print(
        "Native installer selection, compatibility guard, manager creation/removal "
        "and preview verified."
    )


if __name__ == "__main__":
    main()

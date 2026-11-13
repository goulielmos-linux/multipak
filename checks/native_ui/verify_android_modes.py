"""Verify Android mode pages and routing with simulated operations only."""

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
if not os.environ.get("MULTIPAK_CHECK_INSTALLED"):
    sys.path.insert(0, str(PROJECT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MULTIPAK_LANGUAGE", "pt_BR")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")

from PySide6.QtCore import QEvent, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak import host  # noqa: E402
from multipak.android_bundle import inspect_android  # noqa: E402
from multipak.kirigami.live import LiveInstaller  # noqa: E402
from multipak.kirigami.main import create_window  # noqa: E402
from multipak.models import PackageInspection, Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Breeze")
    host.is_flatpak = lambda: False
    output = PROJECT.parent / "work/android-mode-ui"
    output.mkdir(exist_ok=True)
    apk = PROJECT.parent / "work/android-parser-sample.apk"
    package = replace(inspect_android(apk), android_min_sdk=23, android_target_sdk=36)
    personal = dict(
        id="system-waydroid",
        label="Waydroid (ambiente existente)",
        personal=True,
        profile="",
        sdk=33,
        version="Android 13",
    )
    owned = dict(id="androida", label="MultiPak-Android-13", profile="android-13", sdk=33)
    old = dict(id="androidb", label="Compatibilidade", profile="android-11", sdk=30)
    calls = []
    prechecks = []

    def factory(store, operation, parent):
        worker = Worker(store, operation, parent)

        def install(pkg, **options):
            calls.append(options)
            return Transaction(
                "a" * 32,
                options["android_label"],
                "",
                pkg.name,
                pkg.version,
                "apk",
                "",
                method="waydroid",
                status="installed",
            )

        def run(args, **kwargs):
            assert args[0] == "test", args
            prechecks.append(args)
            return ""

        worker.engine.install_android = install
        worker.engine.runner.run = run
        return worker

    def click(window, item):
        assert item is not None and item.property("enabled")
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(80)

    with tempfile.TemporaryDirectory(prefix="multipak-android-modes-") as temp:
        root = Path(temp)
        os.environ["XDG_DATA_HOME"] = str(root / "xdg-data")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg-config")
        store = Store(root / "installations", root / "applications")
        for case in ("standard-new", "standard-reuse", "advanced-owned", "advanced-personal"):
            bridge = LiveInstaller(apk, store=store, worker_factory=factory)
            entries = (personal, old) if case == "standard-new" else (personal, owned, old)
            bridge._inspected(PackageInspection(package, android_environments=entries))
            engine, bridge, window = create_window(app, bridge)
            QTest.qWait(200)
            page = window.property("currentPage")
            assert page.objectName() == "welcomePage"
            warning = page.findChild(QObject, "warningLabel")
            assert "smartphones" in warning.property("text")
            click(window, page.findChild(QObject, "continueButton"))
            page = window.property("currentPage")
            assert page.objectName() == "methodPage"
            assert not bridge.androidUsesPersonalEnvironment
            assert all(e["id"] != "system-waydroid" for e in bridge.androidEnvironments)
            if case == "standard-new":
                assert window.grabWindow().save(str(output / "android-methods.png"))
            if case.startswith("advanced"):
                bridge.chooseConfiguration("advanced")
                QTest.qWait(150)
                page = window.property("currentPage")
                assert page.objectName() == "androidAdvancedPage"
                if case == "advanced-owned":
                    bridge.chooseAndroidContainerMode("custom")
                    QTest.qWait(80)
                    assert bridge.androidNewEnvironmentName == ""
                    assert bridge.androidContainerDescription == ""
                    assert not bridge.androidSelectionValid
                    field = page.findChild(QObject, "androidNewEnvironmentName")
                    assert field.property("text") == ""
                    assert window.grabWindow().save(str(output / "android-custom-empty.png"))
                    bridge.setAndroidEnvironmentName("Meus aplicativos — ação")
                    assert bridge.androidSelectionValid
                    bridge.chooseAndroidProfile("android-11")
                    assert bridge.androidNewEnvironmentName == "Meus aplicativos — ação"
                    bridge.setAndroidEnvironmentName("inválido\n")
                    assert not bridge.androidSelectionValid
                    bridge.chooseAndroidSuggested(False)
                    bridge.chooseAndroidProfile("android-11")
                    bridge.chooseAndroidContainerMode("existing")
                    bridge.chooseAndroidEnvironment("androidb")
                    bridge.package = replace(package, android_min_sdk=33)
                    assert not bridge.androidSelectionValid
                    bridge.package = package
                    assert bridge.androidSelectionValid
                    QTest.qWait(100)
                    assert window.grabWindow().save(str(output / "android-advanced.png"))
                else:
                    bridge.chooseAndroidContainerMode("existing")
                    bridge.chooseAndroidEnvironment("system-waydroid")
                    assert bridge.androidUsesPersonalEnvironment
                    QTest.qWait(80)
                    assert (
                        page.findChild(QObject, "androidAlternativeMethod").property("enabled")
                        is False
                    )
                    bridge.chooseConfiguration("standard")
                    assert bridge.androidEnvironment == "androida"
                    bridge.chooseConfiguration("advanced")
                    bridge.chooseAndroidContainerMode("existing")
                    bridge.chooseAndroidEnvironment("system-waydroid")
                bridge.confirmAndroidConfiguration()
            else:
                click(window, page.findChild(QObject, "methodContinueButton"))
            QTest.qWait(150)
            page = window.property("currentPage")
            assert page.objectName() == "androidPage"
            assert page.findChild(QObject, "androidProfileChoice") is None
            if case == "standard-new":
                assert bridge.androidEnvironmentName == "MultiPak-Android-13"
                assert window.grabWindow().save(str(output / "android-standard-review.png"))
                click(window, page.findChild(QObject, "androidConfidenceInfo"))
                dialog = page.findChild(QObject, "androidConfidenceDialog")
                assert dialog is not None and dialog.property("opened")
                explanation = dialog.findChild(QObject, "androidConfidenceExplanation")
                text = explanation.property("text")
                assert text.startswith("Confiança da identificação: Média.\n\n"), text
                assert all(
                    version in text for version in ("Android 6.0", "Android 13", "Android 16")
                )
                assert "API" not in text and "SDK" not in text
                assert explanation.implicitHeight() <= dialog.height()
                QTest.qWait(250)
                assert dialog.grabWindow().save(str(output / "android-confidence.png"))
                dialog.close()
                QTest.qWait(80)
            click(window, page.findChild(QObject, "androidInstallButton"))
            for _ in range(200):
                if bridge.state == "success":
                    break
                QTest.qWait(10)
            assert bridge.state == "success", bridge.status
            selected = calls[-1]
            if case == "advanced-personal":
                assert selected["instance_id"] == "" and selected["android_profile"] == ""
                assert selected["android_label"] == "Waydroid" and len(prechecks) == 2
            elif case == "advanced-owned":
                assert selected["instance_id"] == "androidb"
                assert selected["android_profile"] == "android-11"
            else:
                assert selected["instance_id"]
                assert selected["android_profile"] == "android-13"
                assert selected["android_label"] == "MultiPak-Android-13"
                if case == "standard-reuse":
                    assert selected["instance_id"] == "androida"
            window.close()
            engine.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
    print(
        "Default/new/reused, advanced/version, explicit personal routing and minimum SDK "
        "verified; no Android operations."
    )


if __name__ == "__main__":
    main()

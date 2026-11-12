"""Exercise live UI wiring with real threads and isolated, non-installing engines."""

import io
import os
import tarfile
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("MULTIPAK_LANGUAGE", "en")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")

from live import LiveInstaller  # noqa: E402
from main import create_window  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.distrobox_setup import DistroboxMissing, DistroboxRequirement  # noqa: E402
from multipak.metadata import inspect_package  # noqa: E402
from multipak.models import (  # noqa: E402
    Environment,
    ExistingEnvironment,
    PackageInspection,
    Transaction,
)
from multipak.process import Cancelled, Runner  # noqa: E402
from multipak.software_store import StoreCommand  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


def deb_fixture(path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        raw = (
            b"Package: live-demo\nVersion: 1.2\nArchitecture: all\n"
            b"Depends: libc6 (>= 2.34), libfoo | libbar\n"
        )
        info = tarfile.TarInfo("./control")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))

    def member(name, content):
        header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n"
        return header.encode() + content + (b"\n" if len(content) % 2 else b"")

    path.write_bytes(
        b"!<arch>\n"
        + member("debian-binary", b"2.0\n")
        + member("control.tar.gz", buffer.getvalue())
    )


def arch_fixture(path):
    with tarfile.open(path, "w:xz") as archive:
        raw = b"pkgname = live-arch-demo\npkgver = 2.0-1\narch = any\ndepend = glibc\n"
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Breeze")
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="multipak-live-")
        directory = Path(self.temporary.name)
        self.path = directory / "renamed.rpm"
        deb_fixture(self.path)
        self.store = Store(directory / "data", directory / "applications")
        self.config = {"calls": [], "release": threading.Event(), "fail": False}
        self.profile = Environment("debian-13", "Debian 13", "deb", "debian:13")
        self.profiles = (
            self.profile,
            Environment("ubuntu-26.04", "Ubuntu 26.04", "deb", "ubuntu:26.04"),
        )
        self.existing = ExistingEnvironment("MultiPak-Debian-13", "abc", "debian", "13")
        config = self.config

        class TestEngine:
            def __init__(self, worker):
                self.tx = None
                self.store = worker.engine.store
                self.cancelled = worker.cancel_requested
                self.progress = worker.progress.emit
                self.log = worker.log.emit
                self.runner = Runner(self.cancelled, worker.log.emit)

            def install(self, package, environment, existing=None, **options):
                if config.get("missing_distrobox"):
                    raise DistroboxMissing("Distrobox is required")
                config["calls"].append(("install", package, environment, existing, options))
                name = options.get("container_name") or (existing.name if existing else "new")
                self.tx = Transaction(
                    "a" * 32, name, "image", package.name, package.version, package.kind, "hash"
                )
                self.progress(55, "Installing the application…")
                for index in range(config.get("log_burst", 0)):
                    worker_text = f"{index}: " + "x" * 1024
                    self.log(worker_text)
                if config.get("log_burst"):
                    self.log("End of high-volume output test")
                while not config["release"].is_set():
                    if self.cancelled.wait(0.01):
                        raise Cancelled
                if config["fail"]:
                    self.tx.status = "failed"
                    raise RuntimeError("Repository test failure")
                self.tx.status = "installed"
                self.tx.launchers = ["example.desktop"]
                return self.tx

            def cleanup(self, tx):
                config["calls"].append(("cleanup", tx))

            def install_appimage(self, package):
                tx = self.install(package, None)
                tx.method = "appimage"
                return tx

            def retry(self, tx):
                config["calls"].append(("retry", tx))
                self.tx = tx
                tx.status = "installed"
                return tx

        def worker_factory(store, operation, parent):
            worker = Worker(store, operation, parent)
            worker.engine = TestEngine(worker)
            return worker

        def inspector(engine, path):
            package = inspect_package(path, engine.runner)
            return PackageInspection(package, (self.existing,), self.profiles)

        self.bridge = LiveInstaller(
            self.path, store=self.store, worker_factory=worker_factory, inspector=inspector
        )
        self.engine, _, self.window = create_window(self.app, self.bridge)
        self.window.requestActivate()
        self.bridge.inspect()
        self.wait(lambda: self.bridge.state == "ready")
        QTest.qWait(650)

    @property
    def page(self):
        self._page = self.window.property("currentPage")
        return self._page

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def click(self, name):
        item = self.page.findChild(QObject, name)
        self.assertIsNotNone(item, name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(650)

    def install(self):
        for name in ("continueButton", "methodContinueButton", "environmentInstallButton"):
            self.click(name)

    def tearDown(self):
        if self.bridge.worker is not None:
            self.bridge.abandon()
            self.config["release"].set()
            self.wait(lambda: self.bridge.worker is None)
        if self.bridge.transaction is not None:
            self.bridge.abandon()
            self.wait(lambda: self.bridge.worker is None)
        self.window.close()
        self.engine.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.temporary.cleanup()

    def test_real_metadata_and_success_only_after_worker_finishes(self):
        self.assertEqual(self.bridge.packaging, "DEB")
        self.assertEqual(self.bridge.environmentTitle, "Debian 13")
        self.assertIn("libfoo | libbar", self.bridge.dependencies)
        self.click("continueButton")
        self.click("methodContinueButton")
        self.assertEqual(self.page.objectName(), "environmentPage")
        label = self.page.findChild(QObject, "environmentTitle")
        self.assertEqual(label.property("text"), "Container based on Debian 13")
        self.assertFalse(self.config["calls"])
        self.click("environmentInstallButton")
        self.assertEqual(self.bridge.state, "installing")
        self.assertEqual(self.page.objectName(), "operationPage")
        self.assertEqual(self.config["calls"][0][3], self.existing)
        operation_height = self.window.height()
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")
        QTest.qWait(650)
        self.assertEqual(self.page.objectName(), "completionPage")
        self.assertIsNone(self.page.findChild(QObject, "recoveryAdvice"))
        # A late native configure event can report the preceding page's geometry.
        self.window.setHeight(operation_height)
        QTest.qWait(200)
        self.assertEqual(self.window.height(), self.window.property("fittedHeight"))
        fits = QQmlExpression(
            QQmlEngine.contextForObject(self.page),
            self.page,
            "flickable.contentHeight <= flickable.height + 1",
        )
        self.assertTrue(
            fits.evaluate()[0], "The completion page should fit without scrolling in this case."
        )
        self.click("completionFinishButton")
        self.assertFalse(self.window.isVisible())

    def test_arch_metadata_reaches_pacman_summary_installation_and_completion(self):
        arch_fixture(self.path)
        profile = Environment(
            "arch-rolling",
            "Arch Linux (rolling release)",
            "arch",
            "docker.io/library/archlinux:latest",
        )
        self.profiles = (profile,)
        self.bridge.inspect()
        self.wait(lambda: self.bridge.state == "ready")
        QTest.qWait(650)
        self.assertEqual(self.bridge.packaging, "Arch Linux")
        self.assertEqual(self.bridge.dependencies, "glibc")
        self.assertEqual(self.bridge.confidence, "Medium")
        self.click("continueButton")
        self.click("methodContinueButton")
        self.assertEqual(self.bridge.containerName, "MultiPak-Arch-Linux")
        label = self.page.findChild(QObject, "protectedInstallationExplanation")
        self.assertIn("Pacman", label.property("text"))
        self.assertIn("atualizados", label.property("text"))
        self.assertNotIn("APT", label.property("text"))
        self.click("environmentInstallButton")
        self.assertEqual(self.bridge.state, "installing")
        self.assertEqual(self.config["calls"][0][1].kind, "arch")
        self.assertEqual(self.config["calls"][0][2], profile)
        self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")
        QTest.qWait(650)
        self.assertIn("Arch Linux", self.bridge.resultMessage)
        self.click("completionFinishButton")
        self.assertFalse(self.window.isVisible())

    def test_new_container_is_shown_only_when_no_reusable_match_exists(self):
        self.bridge.configure(
            self.profiles,
            (
                ExistingEnvironment("MultiPak-Debian-12", "old", "debian", "12"),
                ExistingEnvironment("meu-debian", "user", "debian", "13"),
            ),
            self.profile,
        )
        self.bridge.dataChanged.emit()
        self.click("continueButton")
        self.click("methodContinueButton")
        self.assertFalse(self.bridge.containerExists)
        self.assertEqual(self.bridge.containerName, "MultiPak-Debian-13")
        label = self.page.findChild(QObject, "environmentTitle")
        self.assertEqual(label.property("text"), "Container based on Debian 13")
        self.assertFalse(self.config["calls"])
        self.click("environmentBackButton")
        self.assertEqual(self.page.objectName(), "methodPage")
        self.click("methodContinueButton")
        self.click("environmentInstallButton")
        self.assertEqual(self.config["calls"][0][3], None)
        self.bridge.abandon()
        self.wait(lambda: self.bridge.state == "cancelled")

    def test_failure_preserves_transaction_and_retry_calls_engine(self):
        self.config["fail"] = True
        self.config["release"].set()
        self.install()
        self.wait(lambda: self.bridge.state == "error")
        self.assertEqual(self.page.objectName(), "operationPage")
        self.assertIsNotNone(self.bridge.transaction)
        self.assertTrue(self.window.flags() & Qt.WindowCloseButtonHint)
        self.assertIn("test failure", self.bridge.logs)
        self.click("operationRetryButton")
        self.wait(lambda: self.bridge.state == "success")
        self.assertEqual(self.config["calls"][-1][0], "retry")

    def test_cancellation_cleans_up_and_never_shows_success(self):
        self.install()
        self.click("operationCancelButton")
        self.wait(lambda: self.bridge.state == "cancelled")
        self.assertEqual(self.bridge.resultTitle, "Installation cancelled")
        self.assertIsNone(self.page.findChild(QObject, "recoveryAdvice"))
        self.assertEqual(self.config["calls"][-1][0], "cleanup")

    def test_close_is_disabled_during_installation_without_cancelling_it(self):
        self.assertTrue(self.window.flags() & Qt.WindowCloseButtonHint)
        self.install()
        self.assertFalse(self.bridge.canClose)
        self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
        worker = self.bridge.worker
        self.window.close()
        QTest.qWait(100)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(worker.cancel_requested.is_set())
        self.assertFalse(self.bridge._close_pending)
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.flags() & Qt.WindowCloseButtonHint)
        self.assertEqual([call[0] for call in self.config["calls"]], ["install"])
        self.window.close()
        self.assertFalse(self.window.isVisible())

    def test_large_worker_output_is_bounded_and_keeps_gui_events_running(self):
        self.config["log_burst"] = 10000
        updates, pulses = [], []
        self.bridge.logsChanged.connect(lambda: updates.append(True))
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: pulses.append(True))
        timer.start()
        self.install()
        self.wait(lambda: "End of high-volume output test" in self.bridge.logs)
        self.assertGreater(len(pulses), 1)
        self.assertLess(len(updates), 100)
        self.assertLessEqual(len(self.bridge.logs), 200000)
        self.assertLessEqual(len(self.bridge._pending_logs), 200000)
        log = self.page.findChild(QObject, "operationLog")
        self.assertEqual(log.property("text"), "")
        self.click("operationDetailsButton")
        self.assertIn("End of high-volume output test", log.property("text"))
        self.assertTrue(self.page.findChild(QObject, "operationCancelButton").property("enabled"))
        self.click("operationCancelButton")
        self.wait(lambda: self.bridge.state == "cancelled")
        self.assertTrue(self.window.flags() & Qt.WindowCloseButtonHint)
        timer.stop()

    def test_app_store_uses_the_inspected_package_name(self):
        commands = (
            ["gnome-software", "--search=live demo"],
            ["plasma-discover", "--search=live demo"],
            [
                "flatpak",
                "run",
                "--user",
                "--branch=stable",
                "org.kde.discover",
                "--search=live demo",
            ],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                command = StoreCommand(argv, True, "live demo")
                with (
                    patch("multipak.kirigami.live.search_command", return_value=command) as search,
                    patch(
                        "multipak.kirigami.live.QProcess.startDetached", return_value=(True, 1)
                    ) as launch,
                ):
                    self.click("searchButton")
                    self.wait(lambda: self.bridge.state == "ready")
                    self.assertEqual(search.call_args.args[1], "live-demo")
                    launch.assert_called_once_with(argv[0], argv[1:])

    def test_older_store_copies_the_term_for_manual_search(self):
        command = StoreCommand(["bazaar"], False, "live demo")
        with (
            patch("multipak.kirigami.live.search_command", return_value=command),
            patch("multipak.kirigami.live.QProcess.startDetached", return_value=(True, 1)),
            patch("multipak.kirigami.live.QApplication.clipboard") as clipboard,
        ):
            self.click("searchButton")
            self.wait(lambda: self.bridge.state == "ready")
            clipboard.return_value.setText.assert_called_once_with("live demo")

    def test_store_launch_failure_allows_returning_to_installation(self):
        command = StoreCommand(["gnome-software", "--search=live demo"], True, "live demo")
        with (
            patch("multipak.kirigami.live.search_command", return_value=command),
            patch("multipak.kirigami.live.QProcess.startDetached", return_value=(False, 0)),
        ):
            self.click("searchButton")
            self.wait(lambda: self.bridge.state == "error")
            self.assertIn("Could not open the software store", self.bridge.status)
            self.assertTrue(self.bridge.canReturn)
            self.bridge.returnToWelcome()
            self.assertEqual(self.bridge.state, "ready")

    def advanced(self):
        self.click("continueButton")
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.assertTrue(self.window.isVisible())

    def open_distrobox_dialog(self, *, can_install=True, restart=False):
        self.config["missing_distrobox"] = True
        requirement = DistroboxRequirement(
            can_install,
            restart,
            "Distrobox will be installed using rpm-ostree. A restart is required.",
        )
        with patch("multipak.kirigami.live.distrobox_requirement", return_value=requirement):
            self.install()
            self.wait(lambda: self.bridge.worker is None)
            dialog = self.window.findChild(QObject, "distroboxSetupDialog")
            self.assertIsNotNone(dialog)
            self.wait(lambda: dialog.property("visible"))
        self.assertEqual(self.config["calls"], [])
        self.assertIsNone(self.bridge.transaction)
        return dialog

    def click_distrobox_dialog(self, dialog, name):
        item = dialog.findChild(QObject, name)
        self.assertIsNotNone(item, name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(dialog, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(250)

    def test_missing_distrobox_requires_confirmation_and_can_be_cancelled(self):
        dialog = self.open_distrobox_dialog()
        self.assertIn(
            "rpm-ostree", dialog.findChild(QObject, "distroboxSetupMessage").property("text")
        )
        with patch("multipak.kirigami.live.install_distrobox") as install:
            self.click_distrobox_dialog(dialog, "distroboxSetupCloseButton")
            self.assertFalse(dialog.property("visible"))
            install.assert_not_called()
        self.assertEqual(self.bridge.state, "ready")

    def test_confirmed_distrobox_setup_waits_and_offers_restart_after_completion(self):
        dialog = self.open_distrobox_dialog()
        release = threading.Event()

        def install(engine):
            engine.progress(-1, "Installing Distrobox using rpm-ostree…")
            release.wait(5)
            return True

        with patch("multipak.kirigami.live.install_distrobox", side_effect=install) as operation:
            try:
                self.click_distrobox_dialog(dialog, "distroboxSetupInstallButton")
                self.wait(lambda: self.bridge.state == "installing")
                self.assertFalse(self.bridge.canCancel)
                self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
            finally:
                release.set()
            self.wait(lambda: self.bridge.worker is None and dialog.property("visible"))
            operation.assert_called_once()
        self.assertTrue(self.bridge.distroboxRestartRequired)
        self.assertFalse(self.bridge.distroboxCanInstall)
        self.assertTrue(
            dialog.findChild(QObject, "distroboxSetupRestartButton").property("visible")
        )
        with patch("multipak.kirigami.live.reboot_after_distrobox") as reboot:
            self.click_distrobox_dialog(dialog, "distroboxSetupCloseButton")
            reboot.assert_not_called()
        self.assertEqual(self.config["calls"], [])

    def test_pending_distrobox_offers_only_restart(self):
        dialog = self.open_distrobox_dialog(can_install=False, restart=True)
        self.assertFalse(
            dialog.findChild(QObject, "distroboxSetupInstallButton").property("visible")
        )
        with patch("multipak.kirigami.live.reboot_after_distrobox") as reboot:
            self.click_distrobox_dialog(dialog, "distroboxSetupRestartButton")
            self.wait(lambda: self.bridge.worker is None)
            reboot.assert_called_once()
            self.assertFalse(self.window.isVisible())

    def test_dependency_installation_error_is_not_application_success(self):
        dialog = self.open_distrobox_dialog()
        with patch(
            "multipak.kirigami.live.install_distrobox",
            side_effect=RuntimeError("Authorization denied"),
        ):
            self.click_distrobox_dialog(dialog, "distroboxSetupInstallButton")
            self.wait(lambda: self.bridge.state == "error")
        self.assertIn("Authorization denied", self.bridge.status)
        self.assertFalse(self.bridge.canRetry)
        self.assertTrue(self.bridge.canReturn)
        self.assertEqual(self.config["calls"], [])

    def test_unsupported_host_does_not_offer_dependency_installation(self):
        dialog = self.open_distrobox_dialog(can_install=False)
        self.assertFalse(
            dialog.findChild(QObject, "distroboxSetupInstallButton").property("visible")
        )
        self.assertFalse(
            dialog.findChild(QObject, "distroboxSetupRestartButton").property("visible")
        )
        with patch("multipak.kirigami.live.install_distrobox") as install:
            self.bridge.requestDistroboxInstallation()
            install.assert_not_called()
        self.click_distrobox_dialog(dialog, "distroboxSetupCloseButton")

    def type_name(self, name):
        field = self.page.findChild(QObject, "customContainerName")
        field.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_A, Qt.ControlModifier)
        event = QInputMethodEvent()
        event.setCommitString(name)
        self.app.sendEvent(field, event)
        QTest.qWait(100)

    def test_advanced_custom_name_is_validated_and_sent_to_real_worker(self):
        self.advanced()
        combo = self.page.findChild(QObject, "containerModeCombo")
        combo.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_End)
        QTest.qWait(150)
        self.assertEqual(self.bridge.configuration.containerMode, "custom")
        self.assertIsNone(self.page.findChild(QObject, "customNameGuidance"))
        error = self.page.findChild(QObject, "configurationError")
        self.assertFalse(error.property("visible"))
        self.assertFalse(self.bridge.configuration.valid)
        for name in ("-", "_"):
            self.type_name(name)
            self.assertTrue(error.property("visible"))
            self.assertTrue(
                "start" in error.property("text")
                or "unsupported characters" in error.property("text")
            )
        self.type_name("MultiPak-Test")
        self.assertFalse(self.bridge.configuration.valid)
        self.assertFalse(self.page.findChild(QObject, "advancedContinueButton").property("enabled"))
        self.bridge.requestInstallation()
        self.assertFalse(self.config["calls"])
        self.type_name("Demo Tests")
        self.assertEqual(self.bridge.configuration.customName, "Demo-Tests")
        field = self.page.findChild(QObject, "customContainerName")
        self.assertEqual(field.property("text"), "Demo-Tests")
        self.assertEqual(field.property("cursorPosition"), len("Demo-Tests"))
        self.type_name("DemoTests")
        field.setProperty("cursorPosition", 4)
        QTest.keyClick(self.window, Qt.Key_Space)
        QTest.qWait(100)
        self.assertEqual(field.property("text"), "Demo-Tests")
        self.assertEqual(field.property("cursorPosition"), 5)
        self.assertFalse(error.property("visible"))
        self.assertTrue(self.bridge.configuration.valid)
        self.click("advancedContinueButton")
        self.assertEqual(self.page.objectName(), "environmentPage")
        self.assertEqual(self.bridge.containerName, "Demo-Tests")
        self.click("environmentInstallButton")
        call = self.config["calls"][0]
        self.assertIsNone(call[3])
        self.assertEqual(call[4], {"container_name": "Demo-Tests"})
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")

    def test_advanced_existing_reuses_exact_container_without_cloning(self):
        personal = ExistingEnvironment("Personal", "personal", "debian", "13")
        self.bridge.configure(self.profiles, (self.existing, personal), self.profile)
        self.bridge.dataChanged.emit()
        self.advanced()
        self.bridge.configuration.chooseContainerMode("existing")
        QTest.qWait(150)
        combo = self.page.findChild(QObject, "existingContainerCombo")
        combo.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_End)
        QTest.qWait(150)
        self.click("advancedContinueButton")
        self.assertEqual(self.bridge.containerName, personal.name)
        self.click("environmentInstallButton")
        call = self.config["calls"][0]
        self.assertEqual(call[3], personal)
        self.assertEqual(call[4], {})
        self.bridge.abandon()
        self.wait(lambda: self.bridge.state == "cancelled")

    def test_advanced_manual_base_reaches_engine_and_preserves_standard_suggestion(self):
        self.advanced()
        self.click("manualBaseButton")
        combo = self.page.findChild(QObject, "baseProfileCombo")
        combo.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_End)
        QTest.qWait(150)
        self.click("advancedContinueButton")
        self.assertTrue(self.bridge.environmentManuallySelected)
        self.assertEqual(self.bridge.environmentTitle, "Ubuntu 26.04")
        self.click("environmentBackButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.click("advancedContinueButton")
        self.click("environmentInstallButton")
        call = self.config["calls"][0]
        self.assertEqual(call[2].id, "ubuntu-26.04")
        self.assertIsNone(call[3])
        self.assertEqual(call[4], {})
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")

    def test_appimage_skips_container_configuration_and_completes(self):
        self.bridge._inspected(
            PackageInspection(
                replace(
                    self.bridge.package,
                    kind="appimage",
                    appimage_type=2,
                    name="Example AppImage",
                    dependencies="",
                    icon_data=b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                    icon_extension=".svg",
                )
            )
        )
        QTest.qWait(650)
        self.assertEqual(self.bridge.packaging, "AppImage")
        warning = self.page.findChild(QObject, "warningLabel").property("text")
        self.assertNotIn("different Linux distribution", warning)
        self.click("continueButton")
        self.assertEqual(self.page.objectName(), "appimagePage")
        self.assertEqual(self.bridge.environmentTitle, "")
        self.assertEqual(self.bridge.containerName, "")
        self.assertTrue(self.bridge.applicationIcon.toString().startswith("data:image/svg+xml"))
        self.click("appimageBackButton")
        self.assertEqual(self.page.objectName(), "welcomePage")
        self.click("continueButton")
        self.click("appimageInstallButton")
        self.assertEqual(self.bridge.state, "installing")
        self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
        self.config["release"].set()
        self.wait(lambda: self.bridge.state == "success")
        self.assertIn("AppImage", self.bridge.resultMessage)
        self.assertIn("application launcher", self.bridge.resultNote)
        self.assertEqual(len(self.config["calls"]), 1)
        self.assertIsNone(self.config["calls"][0][2])

    def system(self):
        self.bridge.package = replace(self.bridge.package, kind="rpm")
        self.bridge.kind = "RPM"
        profile = Environment("fedora-45", "Fedora 45", "rpm", "fedora:45")
        self.bridge.configure((profile,), (), profile)
        self.bridge.dataChanged.emit()
        self.advanced()
        self.click("alternativeButton")
        self.page.findChild(QObject, "systemMethod").forceActiveFocus()
        QTest.qWait(150)
        self.click("systemMethod")
        self.assertEqual(self.page.objectName(), "systemPage")
        self.assertFalse(self.page.findChild(QObject, "systemInstallButton").property("enabled"))

    def system_operation(self, engine, package):
        self.config["calls"].append(("system", package))
        tx = Transaction(
            "b" * 32,
            "",
            "",
            package.name,
            package.version,
            "rpm",
            "hash",
            method="system",
            status="system_installing",
        )
        engine.tx = tx
        engine.progress(50, "Preparing the deployment…")
        while not self.config["release"].wait(0.01):
            if engine.cancelled.is_set():
                raise RuntimeError("Direct installation was interrupted unexpectedly")
        if self.config["fail"]:
            tx.status = "system_failed"
            raise RuntimeError("rpm-ostree test failure")
        tx.status = "system_pending"
        return tx

    def test_direct_install_blocks_close_and_reports_reboot_without_container_cleanup(self):
        with (
            patch("multipak.kirigami.live.system_install_available", return_value=True),
            patch("multipak.kirigami.live.install_system", side_effect=self.system_operation),
        ):
            self.system()
            self.bridge.requestSystemInstallation(False)
            self.assertFalse(self.config["calls"])
            self.click("systemAcknowledgement")
            self.click("systemInstallButton")
            self.assertEqual(self.bridge.state, "installing")
            self.assertFalse(self.bridge.canCancel)
            worker = self.bridge.worker
            self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
            self.window.close()
            QTest.qWait(100)
            self.assertTrue(self.window.isVisible())
            self.assertFalse(worker.cancel_requested.is_set())
            self.config["release"].set()
            self.wait(lambda: self.bridge.worker is None)
            self.assertEqual(self.bridge.state, "success")
            self.assertEqual(self.bridge.resultTitle, "Restart required")
            self.assertIn("Restart", self.bridge.resultNote)
            self.assertIsNone(self.page.findChild(QObject, "recoveryAdvice"))
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.window.flags() & Qt.WindowCloseButtonHint)
            self.assertEqual([call[0] for call in self.config["calls"]], ["system"])

    def test_direct_failure_never_retries_or_undoes_system_changes_automatically(self):
        self.config["fail"] = True
        self.config["release"].set()
        with (
            patch("multipak.kirigami.live.system_install_available", return_value=True),
            patch("multipak.kirigami.live.install_system", side_effect=self.system_operation),
        ):
            self.system()
            self.click("systemAcknowledgement")
            self.click("systemInstallButton")
            self.wait(lambda: self.bridge.state == "error")
            self.assertIsNone(self.bridge.transaction)
            self.assertFalse(self.bridge.canRetry)
            self.assertIn("rpm-ostree", self.bridge.status)
            self.assertEqual([call[0] for call in self.config["calls"]], ["system"])
            self.click("operationCancelButton")
            self.assertFalse(self.window.isVisible())

    def test_direct_install_is_disabled_when_the_host_does_not_support_it(self):
        with patch("multipak.kirigami.live.system_install_available", return_value=False):
            self.system()
            checkbox = self.page.findChild(QObject, "systemAcknowledgement")
            self.assertFalse(checkbox.property("enabled"))
            self.bridge.requestSystemInstallation(True)
            self.assertFalse(self.config["calls"])
            self.click("systemCancelButton")
            self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)

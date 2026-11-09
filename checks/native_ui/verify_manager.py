"""Exercise the native manager with real threads and isolated, harmless operations."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("MULTIPAK_LANGUAGE", "en")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")

import main  # noqa: E402, F401
from PySide6.QtCore import QEvent, QMetaObject, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.manager import (  # noqa: E402
    Manager,
    create_manager,
    create_preview_manager,
)
from multipak.models import ExistingEnvironment, Transaction  # noqa: E402
from multipak.store import Store  # noqa: E402
from multipak.worker import Worker  # noqa: E402


class ManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Breeze")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="multipak-manager-")
        directory = Path(self.temporary.name)
        self.store = Store(directory / "data", directory / "applications")
        self.calls = []
        self.release = threading.Event()
        self.release.set()
        self.operation_failure = False
        self.discovery_error = False
        self.system_status = None
        self.system_calls = []
        self.association_values = {key: True for key in ("rpm", "deb", "arch", "appimage")}
        self.association_saved = []
        self.association_failure = False
        self.environments = (
            ExistingEnvironment("MultiPak-Fedora-45", "fedora", "fedora", "45"),
            ExistingEnvironment("MultiPak-Debian-13", "debian", "debian", "13"),
            ExistingEnvironment("Personal", "personal", "ubuntu", "26.04"),
            ExistingEnvironment("Other", "unrelated", "ubuntu", "24.04"),
        )

    def add_transaction(self, token="a", name="Application", container="Personal", **options):
        transaction = Transaction(
            token * 32, container, "image", name, "1.2.3", "deb", "hash", status="installed"
        )
        for key, value in options.items():
            setattr(transaction, key, value)
        self.store.save(transaction)
        return transaction

    def build(self, preview=False):
        def discoverer(runner):
            if self.discovery_error:
                raise RuntimeError("Discovery failure")
            return self.environments

        def cleanup(transaction, *, preserve_environment):
            self.calls.append((transaction.id, preserve_environment))
            self.controller.worker.log.emit("Running test removal")
            self.release.wait(5)
            if self.operation_failure:
                raise RuntimeError("Removal test failure")
            self.store.remove_directory(transaction)

        def updater(engine, name):
            self.calls.append(("update", name))
            engine._log("Downloading and updating test packages")
            self.release.wait(5)
            if self.operation_failure:
                raise RuntimeError("Update test failure")

        def worker_factory(store, operation, parent):
            worker = Worker(store, operation, parent)
            worker.engine.cleanup = cleanup
            if self.system_status is not None:
                owner = self

                class HostRunner:
                    def run(self, argv, **kwargs):
                        owner.system_calls.append(argv)
                        if argv[1] == "status":
                            return json.dumps(owner.system_status)
                        if argv[:2] != ["rpm-ostree", "uninstall"]:
                            raise AssertionError("Unexpected host command")
                        worker.log.emit("Preparing test removal through rpm-ostree")
                        owner.release.wait(5)
                        if owner.operation_failure:
                            raise RuntimeError("Simulated rpm-ostree failure")
                        booted = owner.system_status["deployments"][0]
                        pending = dict(
                            booted,
                            booted=False,
                            **{"requested-packages": [], "requested-local-packages": []},
                        )
                        owner.system_status = {
                            "deployments": [pending, booted],
                            "transaction": None,
                        }
                        return ""

                worker.engine.runner = HostRunner()
            return worker

        if preview:
            result = create_preview_manager(self.app)
        else:
            owner = self

            class Preferences:
                def load(self):
                    return dict(owner.association_values)

                def apply(self, selected):
                    if owner.association_failure:
                        raise RuntimeError("Simulated preference save failure")
                    owner.association_saved.append(dict(selected))
                    owner.association_values = dict(selected)
                    return selected

            controller = Manager(
                self.store,
                worker_factory=worker_factory,
                discoverer=discoverer,
                updater=updater,
                association_settings=Preferences(),
            )
            result = create_manager(self.app, controller)
        self.engine, self.controller, self.window = result
        self.window.requestActivate()
        QTest.qWait(400)

    @property
    def page(self):
        self.current_page = self.window.property("currentPage")
        return self.current_page

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def find(self, name, parent=None):
        item = (parent or self.page).findChild(QObject, name)
        self.assertIsNotNone(item, name)
        return item

    def click(self, name, parent=None):
        item = self.find(name, parent)
        self.assertTrue(item.property("visible"), name)
        self.assertTrue(item.property("enabled"), name)
        target = parent or self.window
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        self.assertTrue(0 <= point.x() < target.width(), (name, point))
        self.assertTrue(0 <= point.y() < target.height(), (name, point))
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(250)

    def open_list(self, mode):
        self.click("managerRemoveButton" if mode == "remove" else "managerUpdateButton")
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.controller.mode, mode)

    def tearDown(self):
        self.release.set()
        if hasattr(self, "window"):
            self.wait(lambda: not self.controller.busy)
            self.window.close()
            self.engine.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            del self.window, self.controller, self.engine
            if hasattr(self, "current_page"):
                del self.current_page
        self.temporary.cleanup()

    def test_home_identity_icon_about_license_and_no_maximize(self):
        self.build()
        self.assertEqual(self.app.desktopFileName(), "io.github.multipak.manager")
        self.assertEqual(self.app.applicationName(), "io.github.multipak.manager")
        self.assertFalse(self.window.icon().isNull())
        self.assertFalse(self.window.icon().pixmap(32, 32).isNull())
        self.assertFalse(self.window.flags() & Qt.WindowMaximizeButtonHint)
        self.assertLessEqual(self.window.width(), 700)
        self.assertFalse(self.find("managerActionButton").property("visible"))
        self.click("aboutButton")
        dialog = self.find("managerAboutDialog", self.window)
        self.assertTrue(dialog.property("opened"))
        self.assertIsNot(dialog, self.window)
        self.assertTrue(dialog.flags() & Qt.WindowTitleHint)
        self.assertIn("MultiPak", self.find("managerAboutText", dialog).property("text"))
        self.assertEqual(
            self.find("aboutDescription", dialog).property("text"),
            "Manage your applications and set your preferences.",
        )
        self.click("aboutAuthorInfoButton", dialog)
        author_dialog = self.find("aboutAuthorDialog", dialog)
        self.assertTrue(author_dialog.property("opened"))
        biography = self.find("aboutAuthorBiography", author_dialog).property("text")
        self.assertIn("historian", biography)
        self.assertIn("GPT-6.1 Sol", biography)
        QMetaObject.invokeMethod(author_dialog, "close")
        self.click("managerLicenseButton", dialog)
        license_dialog = self.find("managerLicenseDialog", self.window)
        self.assertTrue(license_dialog.property("opened"))
        self.assertIn("GNU GENERAL PUBLIC LICENSE", self.controller.licenseText)
        self.assertIn("Version 3, 29 June 2007", self.controller.licenseText)
        QMetaObject.invokeMethod(license_dialog, "close")
        QMetaObject.invokeMethod(dialog, "close")

    def test_empty_list_refresh_and_return(self):
        self.build()
        self.open_list("remove")
        self.assertEqual(self.controller.rows, [])
        self.assertFalse(self.find("managerActionButton").property("enabled"))
        self.assertIn("No applications", self.controller.status)
        self.add_transaction()
        self.click("managerRefreshButton")
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(len(self.controller.rows), 1)
        self.click("managerBackButton")
        self.assertEqual(self.controller.mode, "home")

    def test_format_selection_saves_and_can_restore_individual_formats(self):
        self.build()
        self.click("managerAssociationsButton")
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.page.objectName(), "associationPage")
        self.assertTrue(self.find("association_appimage").property("checked"))
        self.click("association_appimage")
        self.click("association_deb")
        self.click("associationSaveButton")
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(
            self.association_saved, [{"rpm": True, "deb": False, "arch": True, "appimage": False}]
        )
        self.assertIn("salvas", self.controller.status)
        self.click("associationBackButton")
        self.assertEqual(self.page.objectName(), "managerPage")
        self.click("managerAssociationsButton")
        self.wait(lambda: not self.controller.busy)
        self.assertFalse(self.find("association_appimage").property("checked"))
        self.click("association_appimage")
        self.click("associationSaveButton")
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.association_saved[-1]["appimage"])
        self.assertFalse(self.calls)

    def test_format_selection_failure_reports_error_without_claiming_success(self):
        self.build()
        self.click("managerAssociationsButton")
        self.wait(lambda: not self.controller.busy)
        self.association_failure = True
        self.click("associationSaveButton")
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.controller.hasError)
        self.assertIn("Simulated", self.controller.status)
        self.assertFalse(self.association_saved)

    def test_remove_requires_confirmation_and_preserves_shared_environment(self):
        target = self.add_transaction(name="Application < & >")
        self.add_transaction("b", "Another application", status="cleanup_failed")
        self.add_transaction("c", "Direct RPM", method="system", status="system_pending")
        self.add_transaction("d", "Incomplete installation", status="failed")
        self.build()
        self.open_list("remove")
        self.assertEqual(len(self.controller.rows), 2)
        self.controller.select(0)
        self.assertTrue(self.find("managerActionButton").property("enabled"))
        self.click("managerActionButton")
        dialog = self.find("removalDialog", self.window)
        self.assertTrue(dialog.property("opened"))
        self.assertEqual(self.calls, [])
        self.assertIn(
            "Application < & >", self.find("removalConfirmationText", dialog).property("text")
        )
        self.click("cancelRemovalButton", dialog)
        self.assertFalse(dialog.property("opened"))
        self.controller.confirmRemoval()
        self.assertEqual(self.calls, [])
        self.click("managerActionButton")
        self.click("confirmRemovalButton", dialog)
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.calls, [(target.id, True)])
        self.assertEqual(len(self.controller.rows), 1)
        self.assertIn("preserved", self.controller.status)
        self.assertTrue(self.find("managerDetailsButton").property("visible"))
        self.assertTrue(self.store.directory("b" * 32).exists())

    def test_appimage_is_removable_without_claiming_to_remove_a_container(self):
        target = self.add_transaction(
            name="Test AppImage", container="", method="appimage", package_kind="appimage"
        )
        self.build()
        self.open_list("remove")
        self.assertEqual(len(self.controller.rows), 1)
        self.assertIn("AppImage", self.controller.rows[0]["detail"])
        self.controller.select(0)
        self.click("managerActionButton")
        dialog = self.find("removalDialog", self.window)
        self.assertIn("managed copy", self.controller.removalExplanation)
        self.assertNotIn("container", self.controller.removalExplanation)
        self.click("confirmRemovalButton", dialog)
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.calls, [(target.id, True)])
        self.assertIn("AppImage", self.controller.status)
        self.assertFalse(self.controller.rows)

    def test_failed_removal_and_close_wait_for_worker_without_cancelling(self):
        self.add_transaction()
        self.build()
        self.open_list("remove")
        self.controller.select(0)
        self.release.clear()
        self.operation_failure = True
        self.click("managerActionButton")
        self.click("confirmRemovalButton", self.find("removalDialog", self.window))
        self.assertTrue(self.controller.busy)
        worker = self.controller.worker
        self.assertFalse(self.controller.canAct)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertIn("Aguarde", self.controller.status)
        self.assertFalse(worker.cancel_requested.is_set())
        self.controller.openList("update")
        self.assertEqual(self.controller.mode, "remove")
        self.release.set()
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.controller.hasError)
        self.assertIn("Removal test failure", self.controller.status)
        self.assertEqual(len(self.controller.rows), 1)
        self.assertTrue(self.controller.canClose)
        self.assertIn("test removal", self.controller.logs)

    def test_update_filters_containers_and_launches_only_the_selected_one(self):
        self.add_transaction()
        self.build()
        self.open_list("update")
        self.assertEqual(
            [row["detail"] for row in self.controller.rows],
            [
                "MultiPak-Debian-13",
                "MultiPak-Fedora-45",
            ],
        )
        self.controller.select(1)
        self.release.clear()
        self.assertEqual(self.find("managerActionButton").property("text"), "Atualizar")
        self.click("managerActionButton")
        self.assertEqual(self.calls, [("update", "MultiPak-Fedora-45")])
        self.assertTrue(self.controller.busy)
        self.assertTrue(self.find("managerProgress").property("visible"))
        self.assertTrue(self.find("managerProgress").property("indeterminate"))
        self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
        self.assertFalse(self.find("managerActionButton").property("enabled"))
        self.assertFalse(self.find("managerBackButton").property("enabled"))
        self.assertFalse(self.find("managerCloseButton").property("enabled"))
        self.assertNotIn("completed", self.controller.status)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.controller.select(0)
        self.controller.updateSelected()
        self.assertEqual(self.controller.selectedIndex, 1)
        self.assertEqual(self.calls, [("update", "MultiPak-Fedora-45")])
        self.wait(lambda: bool(self.controller.logs))
        self.click("managerDetailsButton")
        self.assertTrue(self.find("managerLog").property("visible"))
        self.release.set()
        self.wait(lambda: not self.controller.busy)
        self.assertFalse(self.controller.hasError)
        self.assertIn("Update complete", self.controller.status)
        self.assertIn("test packages", self.controller.logs)
        self.assertFalse(self.find("managerProgress").property("visible"))
        self.assertTrue(self.controller.canClose)

    def test_custom_containers_created_in_multipak_remain_available(self):
        self.environments += (
            ExistingEnvironment(
                "My-Apps",
                "custom",
                "ubuntu",
                "26.04",
                managed_home=True,
                custom_named=True,
            ),
        )
        self.build()
        self.open_list("update")
        names = [row["detail"] for row in self.controller.rows]
        self.assertIn("My-Apps", names)
        self.assertNotIn("Personal", names)
        guidance = self.find("userContainersGuidance")
        self.assertTrue(guidance.property("visible"))
        self.assertEqual(
            guidance.property("text"),
            "For containers created outside MultiPak, use <a href='kontainer'>Kontainer</a>.",
        )
        self.controller.select(names.index("My-Apps"))
        self.click("managerActionButton")
        self.wait(lambda: not self.controller.busy)
        self.assertEqual(self.calls, [("update", "My-Apps")])
        self.assertIn("completed", self.controller.status)
        self.click("managerBackButton")
        self.assertFalse(guidance.property("visible"))

    def test_kontainer_link_opens_companion_and_reports_missing_installation(self):
        self.build()
        self.open_list("update")
        link = self.find("userContainersGuidance")
        with patch("multipak.kirigami.manager.open_kontainer") as opener:
            link.forceActiveFocus()
            QTest.keyClick(self.window, Qt.Key_Return)
            self.wait(lambda: not self.controller.busy)
            opener.assert_called_once()
        self.assertFalse(self.controller.hasError)
        self.assertFalse(self.calls)
        with patch(
            "multipak.kirigami.manager.open_kontainer",
            side_effect=RuntimeError("Kontainer was not found."),
        ):
            link.forceActiveFocus()
            QTest.keyClick(self.window, Qt.Key_Space)
            self.wait(lambda: not self.controller.busy)
        dialog = self.find("kontainerWarningDialog", self.window)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.modality(), Qt.WindowModal)
        self.assertEqual(dialog.transientParent(), self.window)
        self.assertIn(
            "Kontainer was not found",
            self.find("kontainerWarningText", dialog).property("text"),
        )
        self.assertEqual(self.controller.status, "")
        self.assertEqual(self.controller.logs, "")
        self.assertFalse(self.controller.hasError)
        self.assertTrue(self.controller.canClose)
        self.assertFalse(self.calls)
        self.click("kontainerWarningOk", dialog)
        self.assertFalse(dialog.isVisible())
        self.assertTrue(self.window.isVisible())

    def prepare_system_removal(self):
        self.add_transaction(
            name="demo-app",
            container="",
            method="system",
            package_kind="rpm",
            status="system_pending",
        )
        self.system_status = {
            "deployments": [
                {
                    "booted": True,
                    "requested-packages": [],
                    "requested-local-packages": ["demo-app-1.0-1.x86_64"],
                }
            ],
            "transaction": None,
        }
        self.build()
        self.open_list("remove")
        self.assertEqual(self.controller.rows[0]["detail"], "Installed directly on the system")
        self.controller.select(0)
        self.assertTrue(self.controller.canAct)

    def test_system_removal_confirmation_close_lock_and_reboot_state(self):
        with (
            patch("multipak.system.system_install_available", return_value=True),
            patch("multipak.system.os.geteuid", return_value=1000),
        ):
            self.prepare_system_removal()
            self.click("managerActionButton")
            dialog = self.find("removalDialog", self.window)
            self.assertIn("reiniciar", self.controller.removalExplanation)
            self.click("cancelRemovalButton", dialog)
            self.assertTrue(all(call[1] == "status" for call in self.system_calls))
            self.release.clear()
            self.click("managerActionButton")
            self.click("confirmRemovalButton", dialog)
            self.assertTrue(self.controller.busy)
            self.assertFalse(self.window.flags() & Qt.WindowCloseButtonHint)
            self.assertFalse(self.find("managerCloseButton").property("enabled"))
            self.window.close()
            self.assertTrue(self.window.isVisible())
            self.release.set()
            self.wait(lambda: not self.controller.busy)
            self.assertIn("Restart", self.controller.status)
            self.assertFalse(self.controller.hasError)
            self.assertFalse(self.calls)  # No container cleanup or global reset.
            self.assertIn(["rpm-ostree", "uninstall", "demo-app"], self.system_calls)
            self.assertIn("Uninstallation pending", self.controller.rows[0]["note"])
            self.controller.select(0)
            self.assertFalse(self.controller.canAct)
            self.click("managerRefreshButton")
            self.wait(lambda: not self.controller.busy)
            self.assertEqual(len(self.controller.rows), 1)
            self.system_status = {
                "deployments": [dict(self.system_status["deployments"][0], booted=True)],
                "transaction": None,
            }
            self.click("managerRefreshButton")
            self.wait(lambda: not self.controller.busy)
            self.assertFalse(self.controller.rows)

    def test_system_removal_failure_preserves_record_and_allows_retry(self):
        with (
            patch("multipak.system.system_install_available", return_value=True),
            patch("multipak.system.os.geteuid", return_value=1000),
        ):
            self.prepare_system_removal()
            self.operation_failure = True
            self.click("managerActionButton")
            self.click("confirmRemovalButton", self.find("removalDialog", self.window))
            self.wait(lambda: not self.controller.busy)
            self.assertTrue(self.controller.hasError)
            self.assertIn("Simulated", self.controller.status)
            self.assertEqual(self.store.transactions()[0].status, "system_removal_failed")
            self.assertTrue(self.controller.canAct)
            self.operation_failure = False
            self.click("managerActionButton")
            self.click("confirmRemovalButton", self.find("removalDialog", self.window))
            self.wait(lambda: not self.controller.busy)
            self.assertIn("Restart", self.controller.status)
            self.assertFalse(self.controller.hasError)

    def test_system_query_failure_keeps_container_removal_available(self):
        self.add_transaction(name="Container app")
        self.add_transaction(
            "b",
            "Direct app",
            container="",
            method="system",
            package_kind="rpm",
            status="system_pending",
        )
        with patch("multipak.system.system_install_available", return_value=False):
            self.build()
            self.open_list("remove")
            self.assertEqual(len(self.controller.rows), 2)
            self.assertTrue(self.controller.hasError)
            self.controller.select(0)
            self.assertTrue(self.controller.canAct)
            self.controller.select(1)
            self.assertFalse(self.controller.canAct)

    def test_discovery_failure_and_retry(self):
        self.discovery_error = True
        self.build()
        self.open_list("update")
        self.assertTrue(self.controller.hasError)
        self.assertIn("Discovery failure", self.controller.status)
        self.assertFalse(self.controller.canAct)
        self.discovery_error = False
        self.click("managerRefreshButton")
        self.wait(lambda: not self.controller.busy)
        self.assertFalse(self.controller.hasError)
        self.assertEqual(len(self.controller.rows), 2)

    def test_preview_does_not_remove_or_update_anything(self):
        self.build(preview=True)
        self.open_list("remove")
        self.controller.select(0)
        self.click("managerActionButton")
        self.click("confirmRemovalButton", self.find("removalDialog", self.window))
        self.assertIn("preview", self.controller.status)
        self.assertEqual(len(self.controller.rows), 2)
        self.click("managerBackButton")
        self.open_list("update")
        self.controller.select(0)
        self.click("managerActionButton")
        self.assertIn("No update", self.controller.status)

    def test_update_failure_keeps_logs_and_allows_retry(self):
        self.build()
        self.open_list("update")
        self.controller.select(0)
        self.operation_failure = True
        self.click("managerActionButton")
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.controller.hasError)
        self.assertIn("Update test failure", self.controller.status)
        self.assertNotIn("completed", self.controller.status)
        self.assertIn("test packages", self.controller.logs)
        self.assertTrue(self.controller.canAct)
        self.assertTrue(self.controller.canClose)
        self.operation_failure = False
        self.click("managerActionButton")
        self.wait(lambda: not self.controller.busy)
        self.assertFalse(self.controller.hasError)
        self.assertIn("Update complete", self.controller.status)
        self.assertEqual(len(self.calls), 2)

    def test_update_start_failure_is_reported_without_claiming_success(self):
        self.build()
        self.open_list("update")
        self.controller.select(0)

        def missing_command(engine, name):
            raise FileNotFoundError("Distrobox unavailable")

        self.controller.updater = missing_command
        self.click("managerActionButton")
        self.wait(lambda: not self.controller.busy)
        self.assertTrue(self.controller.hasError)
        self.assertIn("Distrobox unavailable", self.controller.status)
        self.assertNotIn("completed", self.controller.status)
        self.assertTrue(self.controller.canClose)


if __name__ == "__main__":
    unittest.main(verbosity=2)

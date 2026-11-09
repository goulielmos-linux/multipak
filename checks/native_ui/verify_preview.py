"""Exercise the native preview pages using the system's matching Qt/Kirigami."""

import os
import time
import unittest

os.environ.setdefault("MULTIPAK_LANGUAGE", "en")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")

from pathlib import Path  # noqa: E402

from main import Package, create_preview  # noqa: E402
from PySide6.QtCore import QEvent, QMetaObject, QObject, QPointF, Qt  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402


class PreviewTests(unittest.TestCase):
    def test_arch_advanced_options_use_rolling_base_and_disable_host_installation(self):
        self.build(packaging="ARCH")
        self.navigate()
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.assertEqual(
            self.bridge.configuration.profileOptions, [{"title": "Arch Linux (rolling release)"}]
        )
        self.click("alternativeButton")
        self.assertFalse(self.find("systemCard").property("enabled"))
        self.assertEqual(
            self.find("systemUnavailableNotice").property("text"), "Not available for Arch Linux."
        )
        self.bridge.chooseMethod("system")
        self.assertEqual(self.bridge.selectedMethod, "container")

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        if "breeze" in (key.lower() for key in QStyleFactory.keys()):
            cls.app.setStyle("Breeze")

    def build(self, packaging="RPM", name="Strawberry", *, package=None):
        self.engine, self.bridge, self.window = create_preview(
            self.app, name, "1.2.31", packaging, package=package
        )
        self.window.requestActivate()
        self.wait(lambda: self.page.objectName() == "welcomePage")
        QTest.qWait(600)

    @property
    def page(self):
        self._current_page = self.window.property("currentPage")
        return self._current_page

    def wait(self, predicate):
        deadline = time.monotonic() + 4
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
        target = parent if isinstance(parent, QQuickWindow) else self.window
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        self.assertTrue(
            0 <= point.x() < target.width() and 0 <= point.y() < target.height(),
            (name, point),
        )
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(650)

    def dialog(self):
        return self.find("choiceDialog", self.window)

    def close_dialog(self, dialog):
        self.wait(lambda: dialog.property("opened"))
        QMetaObject.invokeMethod(dialog, "close")
        QTest.qWait(650)

    def navigate(self):
        self.click("continueButton")
        self.wait(lambda: self.page.objectName() == "methodPage")
        self.assertLessEqual(self.window.width(), 700)
        self.assertFalse(self.window.flags() & Qt.WindowMaximizeButtonHint)

    def tearDown(self):
        if hasattr(self, "window"):
            self.window.close()
            self.engine.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            del self.window, self.engine, self.bridge
            if hasattr(self, "_current_page"):
                del self._current_page

    def test_welcome_search_and_about(self):
        self.build()
        self.click("searchButton")
        self.assertEqual(self.bridge.lastChoice, "search")
        self.close_dialog(self.dialog())
        self.click("aboutButton")
        self.close_dialog(self.find("aboutDialog", self.window))
        self.assertEqual(self.page.objectName(), "welcomePage")

    def test_rpm_direct_method_is_only_inside_advanced_and_requires_confirmation(self):
        self.build()
        welcome_size = self.window.size()
        self.navigate()
        method_size = self.window.size()
        self.assertIsNone(self.page.findChild(QObject, "systemCard"))
        self.click("advancedMode")
        self.click("methodContinueButton")
        collapsed = self.window.size()
        self.assertFalse(self.find("systemCard").property("visible"))
        self.click("alternativeButton")
        self.assertTrue(self.find("systemCard").property("visible"))
        self.assertEqual(self.window.width(), collapsed.width())
        self.assertEqual(
            self.find("systemMethodSummary").property("text"), "Use only as a last resort."
        )
        self.assertIsNone(self.page.findChild(QObject, "systemDetails"))
        self.page.findChild(QObject, "systemMethod").forceActiveFocus()
        QTest.qWait(150)
        self.click("systemMethod")
        self.assertEqual(self.page.objectName(), "systemPage")
        self.assertFalse(self.find("systemInstallButton").property("enabled"))
        self.bridge.requestSystemInstallation(False)
        self.assertFalse(self.dialog().property("opened"))
        self.click("systemAcknowledgement")
        self.click("systemInstallButton")
        self.close_dialog(self.dialog())
        self.click("systemBackButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.assertEqual(self.bridge.selectedMethod, "container")
        self.click("alternativeButton")
        self.assertEqual(self.window.size(), collapsed)
        self.click("advancedBackButton")
        self.assertEqual(self.window.size(), method_size)
        self.click("methodBackButton")
        self.assertEqual(self.window.size(), welcome_size)

    def test_protected_explanation_only_appears_in_ready_summary(self):
        self.build()
        self.navigate()
        original = self.window.size()
        self.assertIsNone(self.page.findChild(QObject, "protectedInstallationExplanation"))
        self.assertIsNone(self.page.findChild(QObject, "containerDetails"))
        self.click("advancedMode")
        self.assertIsNone(self.page.findChild(QObject, "advancedDetails"))
        self.assertEqual(self.window.size(), original)
        self.click("standardMode")
        self.click("methodContinueButton")
        explanation = self.find("protectedInstallationExplanation").property("text")
        self.assertIn(
            "Distrobox container using DNF, without changing the base system image", explanation
        )
        self.click("environmentBackButton")
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.click("advancedContinueButton")
        self.assertEqual(
            self.find("protectedInstallationExplanation").property("text"), explanation
        )

    def test_deb_alternative_is_visible_but_disabled(self):
        self.build("DEB", "Google Chrome")
        self.navigate()
        self.assertIsNone(self.page.findChild(QObject, "systemCard"))
        self.assertIsNone(self.page.findChild(QObject, "alternativeButton"))
        self.bridge.chooseMethod("system")
        self.assertEqual(self.bridge.selectedMethod, "container")
        self.assertIsNone(self.page.findChild(QObject, "containerDetails"))
        self.click("methodContinueButton")
        self.assertEqual(self.bridge.selectedMethod, "container")
        self.assertEqual(self.bridge.selectedMode, "standard")
        self.wait(lambda: self.page.objectName() == "environmentPage")
        self.assertEqual(self.bridge.confidence, "Low")
        self.assertTrue(self.bridge.environmentTitle.startswith("Ubuntu"))
        self.assertIn(
            "Distrobox container using APT, without changing the base system image",
            self.find("protectedInstallationExplanation").property("text"),
        )
        self.click("environmentInstallButton")
        self.close_dialog(self.dialog())
        self.click("environmentBackButton")
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.assertTrue(self.find("alternativeButton").property("visible"))
        self.assertFalse(self.find("systemCard").property("visible"))
        self.click("alternativeButton")
        self.assertTrue(self.find("systemCard").property("visible"))
        self.assertFalse(self.find("systemCard").property("enabled"))
        self.assertFalse(self.find("systemMethod").property("enabled"))
        self.assertFalse(self.find("systemMethodTitle").property("enabled"))
        notice = self.find("systemUnavailableNotice")
        self.assertTrue(notice.property("visible"))
        self.assertEqual(notice.property("text"), "Not available for DEB.")
        self.assertIsNone(self.page.findChild(QObject, "systemDetails"))
        self.click("systemMethod")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.bridge.chooseMethod("system")
        self.assertEqual(self.bridge.selectedMethod, "container")

    def test_standard_environment_info_and_return(self):
        self.build()
        self.navigate()
        self.click("methodContinueButton")
        self.wait(lambda: self.page.objectName() == "environmentPage")
        self.assertLessEqual(self.window.width(), 700)
        self.click("aboutButton")
        self.close_dialog(self.find("aboutDialog", self.window))
        self.assertEqual(
            self.find("environmentTitle").property("text"),
            "Container based on " + self.bridge.environmentTitle,
        )
        self.click("environmentInstallButton")
        self.close_dialog(self.dialog())
        self.assertEqual(self.page.objectName(), "environmentPage")
        self.window.showMaximized()
        QTest.qWait(100)
        self.assertNotEqual(self.window.visibility(), QQuickWindow.Maximized)
        self.click("environmentBackButton")
        self.assertEqual(self.page.objectName(), "methodPage")
        self.assertTrue(self.find("standardCard").property("checked"))
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.click("advancedBackButton")
        self.assertEqual(self.page.objectName(), "methodPage")

    def test_environment_confidence_uses_evidence_and_variable_version(self):
        cases = (
            ("1.2.31-1.fc45", "fedora-45", "metadata", "High", "Fedora 45", ".fc45"),
            ("1.2.31-1.fc44", "fedora-44", "metadata", "High", "Fedora 44", ".fc44"),
            ("1.2.31", "fedora-45", "filename", "Medium", "Fedora 45", "filename"),
            ("1.2.31", "", "", "Low", "Fedora 45", "could not"),
        )
        colors = {}
        for version, hint, source, strength, title, evidence in cases:
            with self.subTest(strength=strength, title=title):
                package = Package(
                    Path("sample.fc45.rpm"),
                    "rpm",
                    "example",
                    version,
                    "x86_64",
                    distro_hint=hint,
                    distro_hint_source=source,
                )
                self.build(package=package)
                self.navigate()
                self.click("methodContinueButton")
                self.wait(lambda: self.page.objectName() == "environmentPage")
                self.assertEqual(
                    self.find("environmentTitle").property("text"),
                    "Container based on " + title,
                )
                value = self.find("confidenceValue")
                self.assertEqual(value.property("text"), strength)
                colors[strength] = value.property("color").rgba()
                self.assertNotEqual(
                    self.find("confidencePrefix").property("color"), value.property("color")
                )
                self.click("suggestionInfoButton")
                dialog = self.find("suggestionDialog")
                self.wait(lambda dialog=dialog: dialog.property("opened"))
                explanation = dialog.findChild(QObject, "suggestionExplanation").property("text")
                self.assertIn(evidence, explanation)
                self.close_dialog(dialog)
                self.click("environmentBackButton")
                self.click("advancedMode")
                self.click("methodContinueButton")
                self.assertEqual(self.page.objectName(), "advancedPage")
                self.assertEqual(self.find("confidenceValue").property("text"), strength)
                self.assertEqual(
                    self.find("confidenceValue").property("color").rgba(), colors[strength]
                )
                self.click("suggestionInfoButton")
                advanced_dialog = self.find("suggestionDialog")
                self.assertTrue(advanced_dialog.property("opened"))
                self.assertEqual(
                    self.find("suggestionExplanation", advanced_dialog).property("text"),
                    explanation,
                )
                self.close_dialog(advanced_dialog)
                self.click("manualBaseButton")
                self.assertTrue(self.find("confidenceValue").property("visible"))
                self.click("advancedCancelButton")
                self.assertFalse(self.window.isVisible())
                self.tearDown()
        self.assertEqual(len(set(colors.values())), 3)

    def test_completion_omits_recovery_advice_and_fits_its_content(self):
        self.build()
        QMetaObject.invokeMethod(self.window, "openReview")
        QTest.qWait(800)
        self.assertEqual(self.page.objectName(), "environmentPage")
        self.assertIsNone(self.page.findChild(QObject, "recoveryAdvice"))
        self.click("environmentInstallButton")
        self.close_dialog(self.dialog())
        self.assertEqual(self.page.objectName(), "environmentPage")
        QMetaObject.invokeMethod(self.window, "previewCompletion")
        QTest.qWait(800)
        self.assertEqual(self.page.objectName(), "completionPage")
        self.assertIsNone(self.page.findChild(QObject, "recoveryAdvice"))
        self.assertLessEqual(self.window.width(), 700)
        self.assertFalse(self.window.flags() & Qt.WindowMaximizeButtonHint)
        self.click("aboutButton")
        self.close_dialog(self.find("aboutDialog", self.window))
        # Extensive warnings must remain readable when they exceed the screen's height.
        self.bridge._result_note = "\n\n".join("Additional warning " + str(i) for i in range(40))
        self.bridge.dataChanged.emit()
        QTest.qWait(400)
        overflow = QQmlExpression(
            QQmlEngine.contextForObject(self.page),
            self.page,
            "flickable.contentHeight > flickable.height",
        )
        self.assertTrue(overflow.evaluate()[0])
        self.assertEqual(self.window.height(), self.window.property("fittedHeight"))
        self.click("completionFinishButton")
        self.assertFalse(self.window.isVisible())

    def test_dependency_list_scrolls_selects_and_returns_to_environment(self):
        dependencies = "\n".join(f"libsample-{index}.so.6()(64bit)" for index in range(80))
        package = Package(
            Path("sample.rpm"),
            "rpm",
            "Sample",
            "1.2.31-1.fc45",
            "x86_64",
            dependencies=dependencies,
            distro_hint="fedora-45",
            distro_hint_source="metadata",
        )
        self.build(package=package)
        QMetaObject.invokeMethod(self.window, "openEnvironment")
        QTest.qWait(800)
        original_size = self.window.size()
        self.click("dependenciesLink")
        dialog = self.find("dependenciesDialog")
        self.wait(lambda: dialog.property("opened"))
        text = self.find("dependenciesText", dialog)
        self.assertEqual(text.property("text"), dependencies)
        self.assertTrue(text.property("readOnly"))
        self.assertTrue(text.property("selectByMouse"))
        self.assertEqual(self.window.size(), original_size)
        scroll = self.find("dependenciesScroll", dialog)
        flickable = scroll.property("contentItem")
        self.assertGreater(flickable.property("contentHeight"), flickable.height())
        text.forceActiveFocus()
        QTest.keyClick(text.window(), Qt.Key_End, Qt.ControlModifier)
        QTest.qWait(150)
        self.assertGreater(flickable.property("contentY"), 0)
        QTest.keyClick(text.window(), Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(text.property("selectedText"), dependencies)
        self.click("dependenciesCloseButton", dialog)
        self.wait(lambda: not dialog.property("visible"))
        self.assertEqual(self.page.objectName(), "environmentPage")
        self.assertEqual(self.window.size(), original_size)
        self.click("dependenciesLink")
        self.wait(lambda: dialog.property("opened"))
        self.assertEqual(text.property("cursorPosition"), 0)
        QTest.keyClick(text.window(), Qt.Key_Escape)
        QTest.qWait(650)
        self.assertFalse(dialog.property("visible"))

    def test_empty_dependencies_and_deb_alternatives_are_preserved(self):
        for dependencies in ("", "libc6 (>= 2.38), libfoo | libbar, app-data (= 1.2.31)"):
            with self.subTest(dependencies=dependencies):
                package = Package(
                    Path("sample.deb"),
                    "deb",
                    "Sample",
                    "1.2.31",
                    "amd64",
                    dependencies=dependencies,
                )
                self.build(package=package)
                QMetaObject.invokeMethod(self.window, "openEnvironment")
                QTest.qWait(800)
                self.click("dependenciesLink")
                dialog = self.find("dependenciesDialog")
                self.wait(lambda dialog=dialog: dialog.property("opened"))
                self.assertEqual(
                    self.find("dependenciesText", dialog).property("text"), dependencies
                )
                self.assertEqual(
                    self.find("dependenciesScroll", dialog).property("visible"),
                    bool(dependencies),
                )
                if not dependencies:
                    self.assertIn(
                        "The package declares no dependencies.",
                        self.find("dependenciesDescription", dialog).property("text"),
                    )
                self.click("dependenciesCloseButton", dialog)
                self.click("environmentCancelButton")
                self.assertFalse(self.window.isVisible())
                self.tearDown()

    def test_information_windows_are_separate_movable_dialogs(self):
        self.build()
        QMetaObject.invokeMethod(self.window, "openEnvironment")
        QTest.qWait(800)
        owner_position = self.window.position()
        for button, name in (
            ("suggestionInfoButton", "suggestionDialog"),
            ("dependenciesLink", "dependenciesDialog"),
            ("aboutButton", "aboutDialog"),
        ):
            with self.subTest(name=name):
                self.click(button)
                dialog = self.find(name, self.window)
                self.assertIsInstance(dialog, QQuickWindow)
                self.assertEqual(dialog.transientParent(), self.window)
                self.assertEqual(dialog.modality(), Qt.WindowModal)
                self.assertTrue(dialog.flags() & Qt.WindowTitleHint)
                self.assertFalse(dialog.flags() & Qt.FramelessWindowHint)
                self.assertFalse(dialog.flags() & Qt.WindowMaximizeButtonHint)
                position = dialog.position()
                dialog.setPosition(position.x() + 30, position.y() + 20)
                QTest.qWait(100)
                self.assertNotEqual(dialog.position(), position)
                self.assertEqual(self.window.position(), owner_position)
                QTest.keyClick(dialog, Qt.Key_Escape)
                self.wait(lambda dialog=dialog: not dialog.isVisible())
        self.assertTrue(self.window.isVisible())

    def test_protected_modes_selection_and_return(self):
        self.build()
        self.navigate()
        original = self.window.size()
        self.assertTrue(self.find("standardCard").property("checked"))
        self.click("advancedMode")
        self.assertTrue(self.find("advancedCard").property("checked"))
        self.assertFalse(self.find("standardCard").property("checked"))
        self.assertEqual(self.window.size(), original)
        self.click("methodContinueButton")
        self.assertEqual(self.bridge.selectedMode, "advanced")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.click("aboutButton")
        self.close_dialog(self.find("aboutDialog", self.window))
        self.window.showMaximized()
        QTest.qWait(100)
        self.assertNotEqual(self.window.visibility(), QQuickWindow.Maximized)
        self.click("advancedBackButton")
        self.assertEqual(self.page.objectName(), "methodPage")
        self.assertTrue(self.find("advancedCard").property("checked"))
        self.click("methodContinueButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.click("advancedCancelButton")
        self.assertFalse(self.window.isVisible())

    def test_advanced_manual_selection_survives_back_and_does_not_change_standard(self):
        self.build("DEB")
        self.navigate()
        self.click("advancedMode")
        self.click("methodContinueButton")
        self.click("manualBaseButton")
        combo = self.find("baseProfileCombo")
        combo.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_End)
        QTest.qWait(200)
        self.assertEqual(self.bridge.configuration.environment.id, "debian-12")
        self.click("advancedContinueButton")
        self.assertEqual(self.page.objectName(), "environmentPage")
        self.assertEqual(self.bridge.environmentTitle, "Debian 12")
        self.assertTrue(self.find("manualBaseDescription").property("visible"))
        self.assertFalse(self.find("suggestionInfoButton").property("visible"))
        self.click("environmentBackButton")
        self.assertEqual(self.page.objectName(), "advancedPage")
        self.assertFalse(self.bridge.configuration.useSuggested)
        self.click("advancedBackButton")
        self.click("standardMode")
        self.click("methodContinueButton")
        self.assertEqual(self.bridge.environmentTitle, "Ubuntu 26.04")
        self.assertFalse(self.bridge.environmentManuallySelected)
        self.assertTrue(self.find("suggestionInfoButton").property("visible"))

    def test_advanced_versions_do_not_repeat_resolved_aliases_from_the_catalog(self):
        from multipak.models import Environment

        self.build("DEB")
        suggested = Environment("ubuntu-latest", "Ubuntu 26.04", "deb", "ubuntu:latest", "26.04")
        profiles = (
            suggested,
            Environment("ubuntu-26.10", "Ubuntu 26.10", "deb", "ubuntu:26.10"),
            Environment("ubuntu-26.04", "Ubuntu 26.04", "deb", "ubuntu:26.04"),
            Environment("debian-stable", "Debian 13", "deb", "debian:stable", "13"),
            Environment("debian-13", "Debian 13", "deb", "debian:13"),
        )
        self.bridge.configure(profiles, (), suggested)
        self.bridge.dataChanged.emit()
        self.navigate()
        self.click("advancedMode")
        self.click("methodContinueButton")
        combo = self.find("baseProfileCombo")
        self.assertEqual(combo.property("count"), 3)
        self.assertEqual(
            [row["title"] for row in self.bridge.configuration.profileOptions],
            ["Ubuntu 26.04", "Ubuntu 26.10", "Debian 13"],
        )
        self.click("manualBaseButton")
        combo.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key_End)
        QTest.qWait(200)
        self.click("advancedContinueButton")
        self.assertEqual(self.bridge.environmentTitle, "Debian 13")
        self.assertEqual(self.bridge._selection.environment.image, "debian:13")

    def test_long_name_is_escaped_and_keeps_navigation_visible(self):
        self.build(name="An application with a very long name < & > and several additional words")
        self.assertIn("&lt; &amp; &gt;", self.bridge.applicationNameHtml)
        self.navigate()
        self.assertLessEqual(self.window.width(), 700)
        self.assertFalse(self.window.grabWindow().isNull())
        self.click("methodCancelButton")
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Exercise the rendered language settings using isolated preferences and no package changes."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
os.environ.pop("MULTIPAK_LANGUAGE", None)

import main  # noqa: E402, F401
from PySide6.QtCore import QEvent, QMetaObject, QObject  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from multipak.kirigami.languages import Languages  # noqa: E402
from multipak.kirigami.main import create_preview  # noqa: E402
from multipak.kirigami.manager import Manager, create_manager  # noqa: E402
from multipak.localization import LanguageSettings, current_language, tr  # noqa: E402
from multipak.store import Store  # noqa: E402


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Breeze")
        cls.temporary = tempfile.TemporaryDirectory(prefix="multipak-languages-")
        cls.directory = Path(cls.temporary.name)
        cls.service = Languages(cls.app, LanguageSettings(cls.directory / "config"))
        cls.app._multipak_languages = cls.service

    def setUp(self):
        self.service.choose(2)
        self.service.save(False)
        self.engine, self.controller, self.window = create_manager(
            self.app, Manager(Store(self.directory / "data", self.directory / "applications"))
        )
        QTest.qWait(300)

    def tearDown(self):
        self.window.close()
        self.engine.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def find(self, name):
        self.current_page = self.window.property("currentPage")
        item = self.current_page.findChild(QObject, name)
        self.assertIsNotNone(item, name)
        return item

    def open_languages(self):
        self.controller.openLanguages()
        QTest.qWait(250)
        self.assertEqual(self.find("languageHeading").property("text"), "Language")

    def test_save_retranslates_actual_qml_and_persists(self):
        self.open_languages()
        combo = self.find("languageCombo")
        combo.setProperty("currentIndex", 1)
        self.service.choose(1)
        QMetaObject.invokeMethod(self.find("languageSaveButton"), "clicked")
        QTest.qWait(100)
        self.assertEqual(self.find("languageHeading").property("text"), "Idioma")
        self.assertEqual(self.find("languageSaveButton").property("text"), "Salvar")
        self.assertEqual(self.service.settings.load(), "pt_BR")
        self.assertEqual(self.service.status, "Preferência de idioma salva.")
        self.service.choose(2)
        self.service.save(False)
        QTest.qWait(100)
        self.assertEqual(self.find("languageHeading").property("text"), "Language")
        self.assertEqual(self.find("languageSaveButton").property("text"), "Save")
        self.assertEqual(tr("Install"), "Install")

    def test_discard_and_preview_do_not_save_preferences(self):
        self.open_languages()
        self.service.choose(1)
        self.service.reset()
        self.assertEqual(self.service.selectedIndex, 2)
        self.service.choose(1)
        self.service.save(True)
        self.assertEqual(current_language(), "pt_BR")
        self.assertEqual(self.service.settings.load(), "en")

    def test_failed_save_leaves_active_language_unchanged(self):
        self.open_languages()
        self.service.choose(1)
        with patch.object(self.service.settings, "save", side_effect=PermissionError("read only")):
            self.service.save(False)
        self.assertEqual(current_language(), "en")
        self.assertTrue(self.service.hasError)
        self.assertIn("Could not save", self.find("languageStatus").property("text"))
        self.assertEqual(self.service.settings.load(), "en")
        self.assertEqual(self.find("languageHeading").property("text"), "Language")

    def test_system_resolution_and_installer_unknown_version(self):
        with patch.dict(os.environ, {"LANGUAGE": "pt_BR"}):
            self.service.choose(0)
            self.service.save(False)
            self.assertEqual(current_language(), "pt_BR")
        with patch.dict(os.environ, {"LANGUAGE": "de_DE"}):
            self.service.save(False)
            self.assertEqual(current_language(), "en")
        engine, controller, window = create_preview(
            self.app, "Test App", "Not provided", "APPIMAGE"
        )
        QTest.qWait(200)
        self.assertEqual(controller.versionHtml, "Version not provided")
        self.service.choose(1)
        self.service.save(False)
        self.assertEqual(controller.versionHtml, "Versão não informada")
        window.close()
        engine.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()

"""Expose the bundled language preference to QML and refresh open windows."""

import os

from PySide6.QtCore import Property, QObject, QTranslator, Signal, Slot

from multipak.localization import (
    SUPPORTED_LANGUAGES,
    LanguageSettings,
    activate_language,
    current_language,
    tr,
    trn,
)


class CatalogTranslator(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        # Python and QML share one catalog. Context can be added with pgettext later.
        return tr(source) if n < 0 else trn(source, source, n).replace("%n", str(n))


class Languages(QObject):
    changed = Signal()
    languageChanged = Signal()

    def __init__(self, app, settings=None):
        super().__init__(app)
        self.app = app
        self.settings = settings or LanguageSettings()
        self.selection = os.environ.get("MULTIPAK_LANGUAGE", self.settings.load())
        if self.selection not in SUPPORTED_LANGUAGES:
            self.selection = "system"
        self.draft = self.selection
        self._status = ""
        self._error = False
        activate_language(self.selection)
        self.translator = CatalogTranslator(self)
        app.installTranslator(self.translator)

    @Property("QVariantList", notify=changed)
    def options(self):
        return [
            {"value": "system", "label": tr("System language")},
            {"value": "pt_BR", "label": "Português (Brasil)"},
            {"value": "en", "label": "English"},
        ]

    @Property(int, notify=changed)
    def selectedIndex(self):
        return SUPPORTED_LANGUAGES.index(self.draft)

    @Property(str, notify=changed)
    def effectiveLanguage(self):
        return current_language()

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def hasError(self):
        return self._error

    @Slot()
    def reset(self):
        self.draft = self.selection
        self._status, self._error = "", False
        self.changed.emit()

    @Slot(int)
    def choose(self, index):
        if 0 <= index < len(SUPPORTED_LANGUAGES):
            self.draft = SUPPORTED_LANGUAGES[index]
            self._status, self._error = "", False
            self.changed.emit()

    @Slot(bool)
    def save(self, preview=False):
        try:
            if not preview:
                self.settings.save(self.draft)
        except OSError as error:
            self._status = tr("Could not save the language preference: {0}").format(error)
            self._error = True
            self.changed.emit()
            return
        self.selection = self.draft
        activate_language(self.selection)
        self._status = (
            tr("This is a preview. The preference was not saved.")
            if preview
            else tr("Language preference saved.")
        )
        self._error = False
        self.languageChanged.emit()
        self.changed.emit()


def configure_languages(app):
    if not hasattr(app, "_multipak_languages"):
        app._multipak_languages = Languages(app)
    return app._multipak_languages


class LanguageBinding(QObject):
    """Disconnect language refreshes automatically when an engine is destroyed."""

    def __init__(self, app, engine, controller, manager):
        super().__init__(engine)
        self.app = app
        self.engine = engine
        self.controller = controller
        self.manager = manager

    @Slot()
    def refresh(self):
        from .identity import configure_application

        configure_application(self.app, manager=self.manager)
        self.engine.retranslate()
        for name in ("changed", "rowsChanged", "dataChanged"):
            if hasattr(self.controller, name):
                getattr(self.controller, name).emit()


def bind_languages(app, engine, controller, *, manager=False):
    languages = configure_languages(app)
    engine.rootContext().setContextProperty("languages", languages)
    binding = LanguageBinding(app, engine, controller, manager)
    engine._language_binding = binding
    languages.languageChanged.connect(binding.refresh)
    return languages

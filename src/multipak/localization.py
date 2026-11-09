"""Bundled gettext catalogs and per-user language preferences."""

import gettext
import json
import locale
import os
from pathlib import Path

SUPPORTED_LANGUAGES = ("system", "pt_BR", "en")
CATALOG_DIRECTORY = Path(__file__).with_name("locales")


def resolve_language(selection="system", system_languages=None):
    if selection in ("pt_BR", "en"):
        return selection
    if system_languages is None:
        environment = os.environ
        value = environment.get("LANGUAGE") or next(
            (environment[key] for key in ("LC_ALL", "LC_MESSAGES", "LANG") if environment.get(key)),
            locale.getlocale()[0] or "en",
        )
        system_languages = value.split(":")
    for language in system_languages:
        base = language.replace("-", "_").split(".")[0].split("_")[0].lower()
        if base == "pt":
            return "pt_BR"
        if base == "en":
            return "en"
    return "en"


class LanguageSettings:
    def __init__(self, config_home=None):
        directory = Path(config_home or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = directory / "multipak/preferences.json"

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")).get("language", "system")
            return value if value in SUPPORTED_LANGUAGES else "system"
        except (OSError, ValueError, AttributeError, TypeError):
            return "system"

    def save(self, selection):
        if selection not in SUPPORTED_LANGUAGES:
            raise ValueError("Unsupported language selection.")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data["language"] = selection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)


_catalog = gettext.NullTranslations()
_language = "en"


def activate_language(selection, system_languages=None):
    global _catalog, _language
    language = resolve_language(selection, system_languages)
    catalog = gettext.NullTranslations()
    if language != "en":
        # Only catalogs bundled with this release are loaded.
        catalog = gettext.translation("multipak", CATALOG_DIRECTORY, [language], fallback=True)
    _catalog, _language = catalog, language
    return language


def current_language():
    return _language


def mark(message):
    """Mark a source constant for translation when it is displayed."""
    return message


def tr(message):
    return _catalog.gettext(message)


def trn(singular, plural, count):
    return _catalog.ngettext(singular, plural, count)


activate_language(os.environ.get("MULTIPAK_LANGUAGE", LanguageSettings().load()))

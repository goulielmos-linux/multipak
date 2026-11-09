"""Check real catalogs and preferences without changing the user's configuration."""

import gettext
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from multipak.localization import (
    CATALOG_DIRECTORY,
    LanguageSettings,
    activate_language,
    current_language,
    resolve_language,
    tr,
)


@pytest.fixture(autouse=True)
def restore_language():
    previous = current_language()
    yield
    activate_language(previous)


@pytest.mark.parametrize(
    "selection,session,expected",
    [
        ("en", ["pt_BR.UTF-8"], "en"),
        ("pt_BR", ["en_US.UTF-8"], "pt_BR"),
        ("system", ["pt_PT.UTF-8"], "pt_BR"),
        ("system", ["en-GB"], "en"),
        ("system", ["de_DE", "pt_BR"], "pt_BR"),
        ("system", ["de_DE"], "en"),
        ("system", ["C.UTF-8"], "en"),
        ("system", [], "en"),
    ],
)
def test_language_resolution(selection, session, expected):
    assert resolve_language(selection, session) == expected


def test_session_environment_precedence(monkeypatch):
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    assert resolve_language() == "pt_BR"
    monkeypatch.setenv("LC_MESSAGES", "en_GB.UTF-8")
    assert resolve_language() == "en"
    monkeypatch.setenv("LC_ALL", "pt_PT.UTF-8")
    assert resolve_language() == "pt_BR"
    monkeypatch.setenv("LANGUAGE", "fr:en:pt")
    assert resolve_language() == "en"


def test_preference_round_trip_preserves_other_settings(tmp_path):
    settings = LanguageSettings(tmp_path)
    assert settings.load() == "system"
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text('{"other": true}')
    for value in ("en", "pt_BR", "system"):
        settings.save(value)
        assert settings.load() == value
        assert json.loads(settings.path.read_text()) == {"other": True, "language": value}
        assert not settings.path.with_suffix(".tmp").exists()
    with pytest.raises(ValueError):
        settings.save("../other")
    assert settings.load() == "system"


@pytest.mark.parametrize("data", ["{", "null", "[]", '{"language": "unknown"}'])
def test_invalid_preferences_fall_back_to_system(tmp_path, data):
    settings = LanguageSettings(tmp_path)
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text(data)
    assert settings.load() == "system"
    settings.save("en")
    assert settings.load() == "en"


def test_bundled_catalog_and_english_fallback():
    activate_language("pt_BR")
    assert tr("System language") == "Idioma do sistema"
    assert tr("Language preference saved.") == "Preferência de idioma salva."
    assert tr("Uncatalogued diagnostic") == "Uncatalogued diagnostic"
    activate_language("en")
    assert tr("System language") == "System language"
    activate_language("system", ["ja_JP"])
    assert tr("Language preference saved.") == "Language preference saved."


def test_saved_language_used_by_new_process(tmp_path):
    settings = LanguageSettings(tmp_path)
    settings.save("en")
    environment = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path), LANG="pt_BR.UTF-8")
    environment.pop("MULTIPAK_LANGUAGE", None)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    output = subprocess.check_output(
        [sys.executable, "-c", "from multipak.localization import tr; print(tr('Save'))"],
        env=environment,
        text=True,
    )
    assert output.strip() == "Save"


def test_all_source_messages_and_placeholders_are_in_shipped_catalog():
    compiler_path = Path(__file__).parents[1] / "scripts/translations.py"
    spec = importlib.util.spec_from_file_location("catalog_compiler", compiler_path)
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    sources = compiler.source_messages()
    entries = compiler.read_catalog(CATALOG_DIRECTORY / "pt_BR/LC_MESSAGES/multipak.po")
    catalog = gettext.translation("multipak", CATALOG_DIRECTORY, ["pt_BR"])
    assert sources <= entries.keys()
    for source in sources:
        assert entries[source]
        assert compiler.placeholders(source) == compiler.placeholders(entries[source])
        assert catalog.gettext(source) == entries[source]


def test_compiler_rejects_changed_placeholders(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "catalog_compiler", Path(__file__).parents[1] / "scripts/translations.py"
    )
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    path = tmp_path / "invalid.po"
    path.write_text('msgid "Container %1"\nmsgstr "Contêiner %2"\n')
    with pytest.raises(ValueError, match="placeholders"):
        compiler.compile_catalog(path, {"Container %1"})


@pytest.mark.parametrize(
    "language,expected", [("en", "English Name"), ("pt_BR", "Nome em português")]
)
def test_appimage_metadata_uses_selected_language_without_localizing_stored_version(
    tmp_path, monkeypatch, language, expected
):
    from multipak import appimage

    class Archive:
        architecture = "x86_64"
        kind = 2

        def names(self):
            return ["demo.desktop"]

        def read(self, name):
            return (
                "[Desktop Entry]\nType=Application\nName=Default Name\n"
                "Name[en]=English Name\nName[pt_BR]=Nome em português\nExec=AppRun\n"
            ).encode()

    monkeypatch.setattr(appimage, "Archive", lambda *args: Archive())
    activate_language(language)
    package = appimage.inspect_appimage(tmp_path / "demo.AppImage", None)
    assert package.name == expected
    assert package.version == "Not provided"

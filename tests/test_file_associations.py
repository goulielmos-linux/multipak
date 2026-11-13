import configparser
import json
import os
import shutil
import subprocess
import sys
from xml.etree import ElementTree

import pytest
from conftest import make_arch

from multipak.file_associations import APPIMAGE_MIME, FORMATS, FileAssociations
from multipak.integration import DESKTOP_ID


def read_config(path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if path.exists():
        config.read(path)
    return config


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    result = FileAssociations(
        config_home=tmp_path / "config", data_home=tmp_path / "data", refresher=lambda: None
    )
    result.reader = lambda mime: (
        read_config(result.path)
        .get("Default Applications", mime, fallback="other.desktop;")
        .split(";")[0]
    )
    return result


def selection(**overrides):
    return {key: overrides.get(key, True) for key in FORMATS}


def test_selection_registers_all_formats_and_keeps_other_associations(settings):
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text(
        "[Default Applications]\napplication/pdf=viewer.desktop;\n"
        "[Added Associations]\ntext/plain=editor.desktop;\n"
    )
    settings.apply(selection())
    config = read_config(settings.path)
    assert config["Default Applications"]["application/pdf"] == "viewer.desktop;"
    assert config["Added Associations"]["text/plain"] == "editor.desktop;"
    for _, mimes in FORMATS.values():
        for mime in mimes:
            assert config["Default Applications"][mime] == DESKTOP_ID + ";"
    assert settings.load() == selection()
    xml = ElementTree.fromstring(settings.xml_path.read_bytes())
    mime = xml.find("{*}mime-type")
    assert mime.attrib["type"] == APPIMAGE_MIME
    assert not mime.findall("{*}sub-class-of")
    assert {m.attrib["value"] for m in mime.findall(".//{*}match")} >= {"AI\\001", "AI\\002"}


def test_deselect_restores_prior_handler_and_removes_only_our_association(settings):
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text(
        "[Default Applications]\napplication/vnd.appimage=previous.desktop;\n"
        "[Added Associations]\napplication/vnd.appimage=previous.desktop;another.desktop;\n"
    )
    settings.apply(selection())
    settings.apply(selection(appimage=False))
    config = read_config(settings.path)
    assert config["Default Applications"]["application/vnd.appimage"] == "previous.desktop;"
    assert (
        config["Added Associations"]["application/vnd.appimage"]
        == "previous.desktop;another.desktop;"
    )
    assert DESKTOP_ID in config["Removed Associations"]["application/vnd.appimage"]
    assert not settings.xml_path.exists()
    assert settings.load() == selection(appimage=False)


def test_deselect_removes_old_untracked_multipak_defaults(settings):
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text("[Default Applications]\napplication/x-rpm=" + DESKTOP_ID + ";\n")
    settings.apply(selection(rpm=False))
    config = read_config(settings.path)
    assert not config.has_option("Default Applications", "application/x-rpm")
    assert config["Removed Associations"]["application/x-rpm"] == DESKTOP_ID + ";"


def test_deselect_does_not_replace_an_external_change(settings):
    settings.apply(selection())
    config = read_config(settings.path)
    config["Default Applications"]["application/x-rpm"] = "new-choice.desktop;"
    with settings.path.open("w") as stream:
        config.write(stream)
    settings.apply(selection(rpm=False))
    assert (
        read_config(settings.path)["Default Applications"]["application/x-rpm"]
        == "new-choice.desktop;"
    )


def test_repeated_save_keeps_the_original_default_for_restore(settings):
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text("[Default Applications]\napplication/x-rpm=previous.desktop;\n")
    settings.apply(selection())
    settings.apply(selection())
    settings.apply(selection(rpm=False))
    assert (
        read_config(settings.path)["Default Applications"]["application/x-rpm"]
        == "previous.desktop;"
    )


def test_desktop_specific_preferences_take_precedence(tmp_path):
    specific = tmp_path / "kde-mimeapps.list"
    specific.write_text("[Default Applications]\napplication/pdf=viewer.desktop;\n")
    settings = FileAssociations(
        config_home=tmp_path, data_home=tmp_path / "data", refresher=lambda: None
    )
    settings.apply(selection())
    assert settings.path == specific
    assert not (tmp_path / "mimeapps.list").exists()
    assert read_config(specific)["Default Applications"]["application/pdf"] == "viewer.desktop;"


def test_refresh_failure_restores_preferences_and_mime_data(settings):
    settings.apply(selection())
    paths = (settings.path, settings.xml_path, settings.arch_xml_path, settings.state_path)
    snapshot = {path: path.read_bytes() for path in paths}
    settings.refresher = lambda: (_ for _ in ()).throw(RuntimeError("Cache failure"))
    with pytest.raises(RuntimeError, match="Cache failure"):
        settings.apply(selection(appimage=False))
    assert {path: path.read_bytes() for path in paths} == snapshot


def test_first_save_failure_leaves_no_claimed_associations(settings):
    settings.refresher = lambda: (_ for _ in ()).throw(RuntimeError("Cache failure"))
    with pytest.raises(RuntimeError):
        settings.apply(selection())
    assert not any(
        path.exists()
        for path in (settings.path, settings.state_path, settings.xml_path, settings.arch_xml_path)
    )


def test_global_custom_type_gets_executable_handling_when_disabled(settings, tmp_path, monkeypatch):
    system = tmp_path / "system-data/mime/application"
    system.mkdir(parents=True)
    (system / "x-multipak-appimage.xml").touch()
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system-data"))
    settings.apply(selection(appimage=False))
    xml = ElementTree.fromstring(settings.xml_path.read_bytes())
    assert xml.find(".//{*}sub-class-of").attrib["type"] == "application/x-executable"
    settings.apply(selection())
    assert not ElementTree.fromstring(settings.xml_path.read_bytes()).findall(".//{*}sub-class-of")


def test_saved_state_contains_association_backups_and_handler_identity(settings):
    settings.apply(selection())
    state = json.loads(settings.state_path.read_text())
    assert set(state) == {"before", "selected", "desktop_id"}
    assert state["desktop_id"] == DESKTOP_ID
    assert state["selected"] == selection()
    assert set(state["before"]) == {mime for _, mimes in FORMATS.values() for mime in mimes}


def test_disabled_choice_survives_a_stale_desktop_cache(settings):
    settings.apply(selection(appimage=False))
    settings.reader = lambda mime: DESKTOP_ID
    assert settings.load()["appimage"] is False


def test_explicit_deb_default_survives_a_stale_desktop_query(settings):
    settings.apply(selection())
    settings.reader = lambda mime: "other.desktop"
    assert settings.load()["deb"] is True


def test_external_deb_default_takes_precedence_over_saved_choice_and_alias(settings):
    settings.apply(selection())
    config = read_config(settings.path)
    config["Default Applications"]["application/vnd.debian.binary-package"] = "other.desktop;"
    with settings.path.open("w") as stream:
        config.write(stream)
    settings.reader = lambda mime: DESKTOP_ID
    assert settings.load()["deb"] is False


def test_explicit_external_default_can_enable_a_previously_disabled_format(settings):
    settings.apply(selection(rpm=False))
    config = read_config(settings.path)
    config["Default Applications"]["application/x-rpm"] = DESKTOP_ID + ";"
    with settings.path.open("w") as stream:
        config.write(stream)
    settings.reader = lambda mime: DESKTOP_ID
    assert settings.load()["rpm"] is True


@pytest.mark.parametrize("value", [{}, {"not-a-format": True}, selection(rpm="yes")])
def test_invalid_selection_does_not_write_files(settings, value):
    with pytest.raises(ValueError):
        settings.apply(value)
    assert not settings.path.exists()


def test_first_flatpak_start_registers_all_formats(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text("[Default Applications]\napplication/pdf=viewer.desktop;\n")
    assert settings.initialize_defaults() is True
    assert settings.load() == selection()
    config = read_config(settings.path)
    assert config["Default Applications"]["application/pdf"] == "viewer.desktop;"
    for _, mimes in FORMATS.values():
        for mime in mimes:
            assert config["Default Applications"][mime] == DESKTOP_ID + ";"
    assert json.loads(settings.state_path.read_text())["desktop_id"] == DESKTOP_ID


def test_flatpak_start_preserves_later_user_choices(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.initialize_defaults()
    chosen = selection(rpm=False, appimage=False)
    settings.apply(chosen)
    assert settings.initialize_defaults() is False
    assert settings.load() == chosen


def test_flatpak_defaults_replace_unregistered_legacy_preferences(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.state_path.parent.mkdir(parents=True)
    settings.state_path.write_text(json.dumps({"selected": dict.fromkeys(FORMATS, False)}))
    assert settings.initialize_defaults() is True
    assert settings.load() == selection()


def test_failed_first_registration_can_be_retried(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.refresher = lambda: (_ for _ in ()).throw(RuntimeError("Cache failure"))
    with pytest.raises(RuntimeError):
        settings.initialize_defaults()
    assert not settings.state_path.exists()
    settings.refresher = lambda: None
    assert settings.initialize_defaults() is True
    assert settings.load() == selection()


def test_native_start_does_not_register_defaults(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: False)
    assert settings.initialize_defaults() is False
    assert not settings.state_path.exists()
    assert not settings.path.exists()


def test_arch_selection_registers_specific_package_patterns(settings):
    settings.apply(selection())
    xml = ElementTree.fromstring(settings.arch_xml_path.read_bytes())
    assert xml.find("{*}mime-type").attrib["type"] == "application/x-archlinux-package"
    assert {entry.attrib["pattern"] for entry in xml.findall(".//{*}glob")} == {
        "*.pkg.tar",
        "*.pkg.tar.zst",
        "*.pkg.tar.xz",
        "*.pkg.tar.gz",
        "*.pkg.tar.bz2",
    }
    settings.apply(selection(arch=False))
    assert not settings.arch_xml_path.exists()
    assert settings.load()["arch"] is False


@pytest.mark.skipif(not shutil.which("update-mime-database"), reason="shared-mime-info is required")
def test_qt_recognizes_arch_packages_without_claiming_ordinary_archives(settings, tmp_path):
    settings.apply(selection())
    subprocess.run(
        ["update-mime-database", str(settings.data / "mime")], check=True, capture_output=True
    )
    packages = []
    for compression in ("", "zst", "xz", "gz", "bz2"):
        suffix = "." + compression if compression else ""
        packages.append(make_arch(tmp_path / ("fixture.pkg.tar" + suffix), compression=compression))
    ordinary = tmp_path / "ordinary.tar.zst"
    ordinary.write_bytes(packages[1].read_bytes())
    query = (
        "import sys; from PySide6.QtCore import QCoreApplication,QMimeDatabase; "
        "app=QCoreApplication([]); db=QMimeDatabase(); "
        "print('\\n'.join(db.mimeTypeForFile(p).name() for p in sys.argv[1:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", query, *map(str, packages), str(ordinary)],
        env=os.environ
        | {
            "XDG_DATA_HOME": str(settings.data),
            "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stdout.splitlines() == [
        *(["application/x-archlinux-package"] * len(packages)),
        "application/x-zstd-compressed-tar",
    ]


def test_first_start_recovers_from_invalid_saved_preferences(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.state_path.parent.mkdir(parents=True)
    settings.state_path.write_text("{incomplete")
    assert settings.initialize_defaults()
    assert settings.load() == selection()


def test_legacy_migration_preserves_other_associations_and_saved_preferences(settings, monkeypatch):
    from multipak.file_associations import LEGACY_INSTALLER

    monkeypatch.setattr("multipak.host.is_flatpak", lambda: True)
    settings.state_path.parent.mkdir(parents=True)
    original = (
        "# Keep this comment\n"
        "[Default Applications]\n"
        f"application/x-rpm={LEGACY_INSTALLER};rpm-viewer.desktop;\n"
        f"text/plain={LEGACY_INSTALLER};editor.desktop;\n"
        "[Added Associations]\n"
        f"application/x-rpm=rpm-viewer.desktop;{LEGACY_INSTALLER};{DESKTOP_ID};\n"
        "[Removed Associations]\n"
        f"application/x-rpm={LEGACY_INSTALLER};\n"
    )
    settings.path.write_text(original)
    settings.state_path.write_text(
        json.dumps(
            {
                "desktop_id": LEGACY_INSTALLER,
                "before": {"application/x-rpm": f"{LEGACY_INSTALLER};rpm-viewer.desktop"},
                "selected": {"rpm": True, "deb": False},
            }
        )
    )
    settings.migrate_legacy(removed=True)
    expected = original.replace(
        f"application/x-rpm={LEGACY_INSTALLER};rpm-viewer.desktop;",
        f"application/x-rpm={DESKTOP_ID};rpm-viewer.desktop;",
    ).replace(
        f"application/x-rpm=rpm-viewer.desktop;{LEGACY_INSTALLER};{DESKTOP_ID};",
        f"application/x-rpm=rpm-viewer.desktop;{DESKTOP_ID};",
    )
    assert settings.path.read_text() == expected
    assert json.loads(settings.state_path.read_text()) == {
        "desktop_id": DESKTOP_ID,
        "before": {"application/x-rpm": f"{DESKTOP_ID};rpm-viewer.desktop"},
        "selected": {"rpm": True, "deb": False},
    }
    assert not settings.initialize_defaults()
    settings.apply(selection(rpm=False, deb=False))
    assert settings.load()["rpm"] is False


def test_legacy_migration_rolls_back_associations_if_refresh_fails(settings, monkeypatch):
    from multipak.file_associations import LEGACY_INSTALLER

    monkeypatch.setattr("multipak.host.is_flatpak", lambda: True)
    settings.path.parent.mkdir(parents=True)
    before = f"[Default Applications]\napplication/x-rpm={LEGACY_INSTALLER};\n"
    settings.path.write_text(before)

    def fail_refresh():
        raise RuntimeError("fixture refresh failed")

    settings.refresher = fail_refresh
    with pytest.raises(RuntimeError, match="fixture refresh failed"):
        settings.migrate_legacy(removed=True)
    assert settings.path.read_text() == before
    assert not settings.state_path.exists()


def test_android_default_is_added_without_changing_existing_format_choices(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.apply(selection(rpm=False, appimage=False, apk=False))
    saved = json.loads(settings.state_path.read_text())
    saved["selected"].pop("apk")  # A choice saved before Android support.
    settings.state_path.write_text(json.dumps(saved))
    assert settings.initialize_defaults() is True
    assert settings.load() == selection(rpm=False, appimage=False)
    assert settings.initialize_defaults() is False


def test_apkm_mime_is_registered_and_an_android_opt_out_is_kept(settings, monkeypatch):
    monkeypatch.setattr("multipak.file_associations.host.is_flatpak", lambda: True)
    settings.apply(selection(apk=False))
    assert settings.initialize_defaults() is False
    assert settings.load() == selection(apk=False)
    assert not settings.android_xml_path.exists()
    settings.apply(selection())
    xml = ElementTree.fromstring(settings.android_xml_path.read_bytes())
    assert xml.find("{*}mime-type").attrib["type"] == "application/vnd.apkm"
    assert xml.find(".//{*}glob").attrib["pattern"] == "*.apkm"
    assert (
        read_config(settings.path)["Default Applications"]["application/vnd.apkm"]
        == DESKTOP_ID + ";"
    )

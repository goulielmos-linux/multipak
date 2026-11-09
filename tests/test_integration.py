import configparser

from multipak.integration import (
    DESKTOP_ID,
    MIME_TYPES,
    merge_system_defaults,
    register_user_defaults,
)


def test_user_install_registers_rpm_and_deb_handlers(monkeypatch):
    calls = []
    monkeypatch.setattr("multipak.integration.shutil.which", lambda _: "/usr/bin/xdg-mime")
    monkeypatch.setattr(
        "multipak.integration.subprocess.run", lambda argv, **kw: calls.append(argv)
    )
    register_user_defaults()
    assert calls == [["/usr/bin/xdg-mime", "default", DESKTOP_ID, mime] for mime in MIME_TYPES]


def test_image_defaults_preserve_unrelated_associations(tmp_path):
    path = tmp_path / "mimeapps.list"
    path.write_text(
        "[Default Applications]\napplication/pdf=viewer.desktop;\n"
        "[Added Associations]\ntext/plain=editor.desktop;\n"
    )
    merge_system_defaults(path)
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    assert config["Default Applications"]["application/pdf"] == "viewer.desktop;"
    assert config["Added Associations"]["text/plain"] == "editor.desktop;"
    assert all(config["Default Applications"][mime] == DESKTOP_ID + ";" for mime in MIME_TYPES)


def test_desktop_entry_passes_the_opened_file_to_the_app():
    from pathlib import Path

    path = Path(__file__).parents[1] / "data/io.github.multipak.installer.desktop"
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    assert config["Desktop Entry"]["Exec"] == "multipak-installer %f"
    assert config["Desktop Entry"]["Icon"] == "io.github.multipak.installer"
    assert all(mime in config["Desktop Entry"]["MimeType"] for mime in MIME_TYPES)


def test_qt6_helper_supports_legacy_xdg_mime_without_modifying_system_files(tmp_path, monkeypatch):
    import os

    for helper_name in ("qtpaths6", "qtpaths-qt6"):
        tools = tmp_path / helper_name
        tools.mkdir()
        helper = tools / helper_name
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$MULTIPAK_TEST_CONFIG"\n')
        helper.chmod(0o700)
        xdg = tools / "xdg-mime"
        # Reproduce the legacy helper lookup. Fail if the compatibility name does
        # not resolve, rather than falling through and hiding the original error.
        xdg.write_text(
            "#!/bin/sh\n"
            "config=$(qtpaths --writable-path ConfigLocation) || exit 127\n"
            'printf "%s=%s\\n" "$3" "$2" >> "$config/associations"\n'
        )
        xdg.chmod(0o700)
        config = tmp_path / (helper_name + "-config")
        config.mkdir()
        monkeypatch.setenv("PATH", str(tools))
        monkeypatch.setenv("MULTIPAK_TEST_CONFIG", str(config))
        previous_path = os.environ["PATH"]
        register_user_defaults()
        assert (config / "associations").read_text().splitlines() == [
            f"{mime}={DESKTOP_ID}" for mime in MIME_TYPES
        ]
        assert os.environ["PATH"] == previous_path
        assert not (tools / "qtpaths").exists()
        # The original helper remains untouched and keeps its executable mode.
        assert helper.read_text().startswith("#!/bin/sh")
        assert helper.stat().st_mode & 0o100


def test_compatibility_directory_is_removed_even_when_association_fails(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    import pytest

    helper = tmp_path / "qtpaths6"
    helper.touch()
    monkeypatch.setattr(
        "multipak.integration.shutil.which",
        lambda name: {"xdg-mime": "/usr/bin/xdg-mime", "qtpaths6": str(helper)}.get(name),
    )
    directories = []

    def fail(argv, *, check, env):
        directory = Path(env["PATH"].split(":")[0])
        assert (directory / "qtpaths").resolve() == helper
        directories.append(directory)
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr("multipak.integration.subprocess.run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        register_user_defaults()
    assert all(not directory.exists() for directory in directories)

import configparser
import os
import shlex
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import make_appimage

from multipak import appimage, host
from multipak.engine import Engine
from multipak.metadata import inspect_package, package_kind
from multipak.models import Transaction
from multipak.process import Cancelled, Runner
from multipak.store import Store


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    result = Engine(Store(), threading.Event())
    result.preflight = lambda: pytest.fail("AppImage must not need Podman or Distrobox")
    return result


@pytest.fixture
def image(tmp_path):
    if not any(shutil.which(name) for name in ("7zz", "7z", "bsdtar")):
        pytest.skip("An ISO9660 reader is required")
    return make_appimage(tmp_path / "renamed; $(never execute).rpm")


def type2_image(tmp_path):
    if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
        pytest.skip("squashfs-tools are required for real type-2 tests")
    source = tmp_path / "AppDir-source"
    source.mkdir()
    (source / "demo.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Type Two\nExec=AppRun %U\nIcon=demo\n"
    )
    (source / "demo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (source / "AppRun").write_text('#!/bin/sh\nprintf "TYPE_TWO_OK:%s\\n" "$*"\n')
    (source / "AppRun").chmod(0o755)
    payload = tmp_path / "payload.squashfs"
    subprocess.run(
        [
            shutil.which("mksquashfs"),
            str(source),
            str(payload),
            "-noappend",
            "-comp",
            "gzip",
            "-processors",
            "1",
            "-mem",
            "64M",
        ],
        check=True,
        capture_output=True,
    )
    header = bytearray(4096)
    header[:16] = b"\x7fELF\x02\x01\x01\x00AI\x02\x00\x00\x00\x00\x00"
    struct.pack_into("<H", header, 18, 62)
    struct.pack_into("<Q", header, 40, 512)
    struct.pack_into("<HH", header, 58, 64, 1)
    path = tmp_path / "type2.AppImage"
    path.write_bytes(header + payload.read_bytes())
    return path


def test_real_type1_metadata_is_read_without_executing_input(image, engine):
    assert not os.access(image, os.X_OK)
    package = inspect_package(image, engine.runner)
    assert package_kind(image) == "appimage"
    assert (package.name, package.version, package.architecture) == (
        "Test Image",
        "2.4.1",
        "x86_64",
    )
    assert package.appimage_type == 1
    assert package.icon_extension == ".svg"
    assert b"<svg" in package.icon_data


def test_real_type1_install_launch_and_remove_preserves_original(image, engine, tmp_path):
    before = image.read_bytes()
    tx = engine.install_appimage(inspect_package(image, engine.runner))
    managed = engine.store.appimage_directory(tx.id)
    assert managed == Path(os.environ["XDG_DATA_HOME"]) / "multipak/appimages" / tx.id
    assert tx.status == "installed" and tx.appimage_mode == "extracted"
    assert (managed / "application.AppImage").read_bytes() == before
    assert (managed / "icon.svg").is_file()
    desktop = engine.store.launcher(tx, tx.launchers[0])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(desktop)
    assert "appimage_launcher.py" in parser["Desktop Entry"]["Exec"]
    assert shlex.split(parser["Desktop Entry"]["Exec"])[-2:] == ["--example", "%U"]
    launcher = Path(appimage.__file__).with_name("appimage_launcher.py")
    execution = subprocess.run(
        [sys.executable, str(launcher), tx.id, "--example", "one two"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert execution.stdout.strip() == "APPIMAGE_TEST_OK:--example one two"
    personal = tmp_path / "personal-preferences"
    personal.write_text("preserve me")
    sibling = managed.parent / ("f" * 32)
    sibling.mkdir()
    (sibling / "another-app").write_text("preserve me too")
    engine.cleanup(tx)
    assert not managed.exists() and not desktop.exists()
    assert engine.store.transactions() == []
    assert image.read_bytes() == before
    assert personal.read_text() == "preserve me"
    assert (sibling / "another-app").is_file()


def test_type2_is_extracted_without_running_its_runtime(tmp_path, engine):
    path = type2_image(tmp_path)
    package = inspect_package(path, engine.runner)
    assert package.name == "Type Two" and package.appimage_type == 2
    assert appimage.elf_info(path)[2] == 4096
    calls = []
    run = engine.runner.run

    def tracked(argv, **kwargs):
        calls.append(argv)
        return run(argv, **kwargs)

    engine.runner.run = tracked
    tx = engine.install_appimage(package)
    assert tx.appimage_mode == "extracted"
    assert (engine.store.appimage_directory(tx.id) / "AppDir/AppRun").is_file()
    assert not any(
        "--appimage-mount" in command or "AppImage" in str(command[0]) for command in calls
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(appimage.__file__).with_name("appimage_launcher.py")),
            tx.id,
            "hello",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "TYPE_TWO_OK:hello"


def test_retry_rejects_changed_copy_and_records_failure(image, engine):
    tx = engine.install_appimage(inspect_package(image, engine.runner))
    (engine.store.appimage_directory(tx.id) / "application.AppImage").write_bytes(b"changed")
    with pytest.raises(ValueError, match="has changed"):
        engine.retry(tx)
    assert engine.store.transactions()[0].status == "failed"


def test_cancellation_can_clean_partial_copy(image, engine):
    package = inspect_package(image, engine.runner)
    engine.cancelled.set()
    with pytest.raises(Cancelled):
        engine.install_appimage(package)
    tx = engine.tx
    engine.cancelled.clear()
    engine.cleanup(tx)
    assert engine.store.transactions() == [] and image.exists()


def test_cleanup_refuses_symlink_to_unrelated_directory(image, engine, tmp_path):
    tx = engine.install_appimage(inspect_package(image, engine.runner))
    managed = engine.store.appimage_directory(tx.id)
    shutil.rmtree(managed)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "personal").write_text("preserved")
    managed.symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        engine.cleanup(tx)
    assert (unrelated / "personal").read_text() == "preserved"
    assert tx.status == "cleanup_failed"


def test_desktop_actions_and_literal_arguments_survive_integration(image, engine):
    make_appimage(
        image,
        desktop=(
            "[Desktop Entry]\nType=Application\nName=Example\nExec=AppRun %U\nActions=New;\n"
            '[Desktop Action New]\nName=New window\nExec=env DEMO=1 AppRun --new-window "a b" %U\n'
        ),
    )
    tx = engine.install_appimage(inspect_package(image, engine.runner))
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(engine.store.launcher(tx, tx.launchers[0]))
    assert parser["Desktop Entry"]["Actions"] == "New;"
    command = shlex.split(parser["Desktop Action New"]["Exec"])
    assert command[:2] == ["env", "DEMO=1"]
    assert command[-3:] == ["--new-window", "a b", "%U"]


@pytest.mark.parametrize("name", ["../outside", "/absolute", "folder/../../outside", "bad\nname"])
def test_archive_member_paths_cannot_escape(name):
    with pytest.raises(ValueError, match="Invalid internal"):
        appimage._member(name)


def test_missing_reader_reports_what_is_needed(image, monkeypatch):
    monkeypatch.setattr(appimage.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="7-Zip or bsdtar"):
        inspect_package(image, Runner(threading.Event(), lambda _: None))


def test_bad_elf_table_and_architecture_are_rejected(image):
    raw = bytearray(image.read_bytes())
    raw[10] = 2
    image.write_bytes(raw)
    with pytest.raises(ValueError, match="locate"):
        appimage.elf_info(image)
    struct.pack_into("<H", raw, 18, 183)
    image.write_bytes(raw)
    with pytest.raises(ValueError, match="incompatible"):
        appimage.elf_info(image)


def test_binary_reader_keeps_data_separate_from_diagnostics():
    runner = Runner(threading.Event(), lambda _: None)
    output = runner.read_bytes(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('diagnostic'); "
            "sys.stdout.buffer.write(bytes(range(256)))",
        ],
        limit=256,
    )
    assert output == bytes(range(256))


def test_binary_reader_stops_oversized_stream():
    runner = Runner(threading.Event(), lambda _: None)
    with pytest.raises(ValueError, match="read limit"):
        runner.read_bytes([sys.executable, "-c", "print('x'*10000)"], limit=1024)


@pytest.mark.parametrize("member", ["-dangerous", "./-dangerous", "../escape", "/etc/shadow"])
def test_rejects_internal_paths_that_could_be_options_or_escape(member):
    with pytest.raises(ValueError):
        appimage._member(member)


def test_migrate_legacy_appimage_shortcut_preserves_actions_and_other_files(store):
    tx = Transaction("a" * 32, "", "", "Demo", "1", "appimage", "hash", method="appimage")
    name = f"multipak-{tx.id}-appimage.desktop"
    unreadable = f"multipak-{tx.id}-invalid.desktop"
    tx.launchers = [unreadable, name, "external.desktop"]
    store.save(tx)
    invalid_shortcut = store.launcher(tx, unreadable)
    invalid_shortcut.write_bytes(b"\xff\xfe")
    shortcut = store.launcher(tx, name)
    legacy = host.LEGACY_APP_ID
    prefix = 'Exec="/usr/bin/flatpak" "run" "--command=multipak-appimage" '
    main = f'{prefix}"{legacy}" "--" "{tx.id}" "{legacy}" %U'
    action = f"Exec=/usr/bin/flatpak run --command=multipak-appimage {legacy} -- {tx.id}"
    original = f"[Desktop Entry]\r\n{main}\r\n[Desktop Action New]\r\n{action}\r\n"
    original += f"[Other Section]\r\n{main}\r\n"
    shortcut.write_bytes(original.encode())
    other = store.applications / "external.desktop"
    other.write_text(original)
    appimage.migrate_legacy_launchers(store)
    expected = original.replace(f'{prefix}"{legacy}"', f'{prefix}"{host.APP_ID}"', 1).replace(
        action, action.replace(legacy, host.APP_ID, 1), 1
    )
    assert shortcut.read_bytes() == expected.encode()
    assert other.read_bytes() == original.encode()
    assert invalid_shortcut.read_bytes() == b"\xff\xfe"
    before = shortcut.stat()
    appimage.migrate_legacy_launchers(store)
    assert shortcut.stat().st_ino == before.st_ino
    assert shortcut.read_bytes() == expected.encode()

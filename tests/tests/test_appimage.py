import configparser
import os
import shlex
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_appimage

from multipak import appimage
from multipak.engine import Engine
from multipak.metadata import inspect_package, package_kind
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


@pytest.mark.parametrize("direct", [True, False])
def test_real_type2_reader_and_fuse_fallback(tmp_path, engine, direct):
    path = type2_image(tmp_path)
    package = inspect_package(path, engine.runner)
    assert package.name == "Type Two" and package.appimage_type == 2
    assert package.version == "Not provided"
    assert appimage.elf_info(path)[2] == 4096
    with patch.object(appimage, "fuse_works", return_value=direct) as probe:
        tx = engine.install_appimage(package)
    managed = engine.store.appimage_directory(tx.id)
    probe.assert_called_once_with(managed / "application.AppImage", engine.cancelled)
    assert tx.appimage_mode == ("direct" if direct else "extracted")
    assert (managed / "AppDir").exists() is (not direct)
    if direct:
        with patch.object(appimage.os, "execv") as execute:
            appimage.launch(tx.id, ["one two"])
        execute.assert_called_once_with(
            str(managed / "application.AppImage"),
            [str(managed / "application.AppImage"), "one two"],
        )
    else:
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


def test_unknown_mount_error_can_retry_without_silent_extraction(tmp_path, engine):
    package = inspect_package(type2_image(tmp_path), engine.runner)
    with patch.object(appimage, "fuse_works", side_effect=RuntimeError("Unknown mount error")):
        with pytest.raises(RuntimeError, match="Unknown mount error"):
            engine.install_appimage(package)
    tx = engine.tx
    assert tx.status == "failed" and not tx.launchers
    assert not (engine.store.appimage_directory(tx.id) / "AppDir").exists()
    with patch.object(appimage, "fuse_works", return_value=False):
        assert engine.retry(tx).status == "installed"


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


def test_missing_fuse_does_not_invoke_runtime(image):
    with (
        patch.object(appimage.os, "access", return_value=False),
        patch.object(appimage.subprocess, "Popen") as execute,
    ):
        assert appimage.fuse_works(image, threading.Event()) is False
    execute.assert_not_called()


@pytest.mark.parametrize(
    "message, fallback",
    [
        ("dlopen(): error loading libfuse.so.2", True),
        ("fuse: device not found", True),
        ("unexpected runtime error", False),
    ],
)
def test_fuse_probe_recognizes_only_fuse_errors(tmp_path, message, fallback):
    runtime = tmp_path / "test-runtime"
    runtime.write_text("#!/bin/sh\nprintf '%s\\n' '" + message + "'\nexit 1\n")
    runtime.chmod(0o755)
    with patch.object(appimage.os, "access", return_value=True):
        if fallback:
            assert appimage.fuse_works(runtime, threading.Event()) is False
        else:
            with pytest.raises(RuntimeError, match="verify the AppImage mount"):
                appimage.fuse_works(runtime, threading.Event())


def test_fuse_probe_honors_cancellation_and_stops_runtime(tmp_path):
    runtime = tmp_path / "test-runtime"
    runtime.write_text("#!/bin/sh\nsleep 20\n")
    runtime.chmod(0o755)
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        with patch.object(appimage.os, "access", return_value=True):
            with pytest.raises(Cancelled):
                appimage.fuse_works(runtime, cancelled)
    finally:
        timer.cancel()

import base64
import io
import json
import os
import shlex
import struct
import tarfile
import threading
from pathlib import Path

import pytest

os.environ.setdefault("MULTIPAK_LANGUAGE", "en")

from multipak.engine import Engine  # noqa: E402
from multipak.guest import MARKER  # noqa: E402
from multipak.process import CommandError  # noqa: E402
from multipak.store import Store  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    return Store(tmp_path / "data", tmp_path / "applications")


def ar_member(name: str, content: bytes) -> bytes:
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n"
    return header.encode() + content + (b"\n" if len(content) % 2 else b"")


def make_deb(path: Path, control: str | None = None, compression="gz") -> Path:
    control = control or (
        "Package: demo-app\nVersion: 1.2\nArchitecture: all\n"
        "Depends: libc6 (>= 2.34), libgtk-3-0\nDescription: Test fixture\n"
    )
    memory = io.BytesIO()
    mode = "w" if compression in ("", "zst") else f"w:{compression}"
    with tarfile.open(fileobj=memory, mode=mode) as archive:
        info = tarfile.TarInfo("./control")
        raw = control.encode()
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    data = memory.getvalue()
    if compression == "zst":
        try:
            from compression import zstd
        except ImportError:
            import zstandard

            data = zstandard.ZstdCompressor().compress(data)
        else:
            data = zstd.compress(data)
    suffix = f".{compression}" if compression else ""
    path.write_bytes(
        b"!<arch>\n"
        + ar_member("debian-binary", b"2.0\n")
        + ar_member("control.tar" + suffix, data)
    )
    return path


@pytest.fixture
def deb(tmp_path):
    return make_deb(tmp_path / "demo app; $(never execute).deb")


def make_arch(path: Path, control=None, compression="zst") -> Path:
    control = (
        control
        if control is not None
        else (
            "# Generated package metadata\npkgname = demo-app\npkgver = 1:2.0-3\n"
            "arch = any\nurl = \ndepend = glibc>=2.38\ndepend = gtk3\n"
            "optdepend = optional-helper: optional feature\nxdata = pkgtype=pkg\n"
        )
    )
    memory = io.BytesIO()
    mode = "w" if compression in ("", "zst") else f"w:{compression}"
    with tarfile.open(fileobj=memory, mode=mode) as archive:
        for name, raw in (
            (".PKGINFO", control.encode()),
            (".INSTALL", b"exit 77; touch /must-never-run"),
            ("usr/bin/demo-app", b"fake payload"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    data = memory.getvalue()
    if compression == "zst":
        try:
            from compression import zstd
        except ImportError:
            import zstandard

            data = zstandard.ZstdCompressor().compress(data)
        else:
            data = zstd.compress(data)
    path.write_bytes(data)
    return path


@pytest.fixture
def arch(tmp_path):
    return make_arch(tmp_path / "app with spaces; $(never execute).pkg.tar.zst")


def make_appimage(path: Path, *, desktop=None, apprun=None):
    """Build a small real ISO9660 type-1 image, without a runnable embedded runtime."""
    desktop = desktop or (
        "[Desktop Entry]\nType=Application\nName=Test Image\nX-AppImage-Version=2.4.1\n"
        "Exec=AppRun --example %U\nIcon=demo\nTerminal=false\n"
    )
    files = {
        "demo.desktop": desktop.encode(),
        "demo.svg": b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        b'<rect width="32" height="32" fill="blue"/></svg>',
        "AppRun": apprun or b'#!/bin/sh\nprintf "APPIMAGE_TEST_OK:%s\\n" "$*"\n',
    }
    block = 2048
    image = bytearray(block * 40)

    def both(value, width):
        return value.to_bytes(width, "little") + value.to_bytes(width, "big")

    def record(name, sector, size, directory=False):
        result = bytearray(33 + len(name) + (0 if len(name) % 2 else 1))
        result[0] = len(result)
        result[2:10] = both(sector, 4)
        result[10:18] = both(size, 4)
        result[18:25] = bytes((126, 1, 1, 0, 0, 0, 0))
        result[25] = 2 if directory else 0
        result[28:32] = both(1, 2)
        result[32] = len(name)
        result[33 : 33 + len(name)] = name
        return result

    primary = bytearray(block)
    primary[:7] = b"\x01CD001\x01"
    primary[40:72] = b"MULTIPAK_TEST".ljust(32, b" ")
    primary[80:88] = both(40, 4)
    primary[120:124] = both(1, 2)
    primary[124:128] = both(1, 2)
    primary[128:132] = both(block, 2)
    primary[132:140] = both(10, 4)
    primary[140:144] = (18).to_bytes(4, "little")
    primary[148:152] = (19).to_bytes(4, "big")
    primary[156:190] = record(b"\x00", 20, block, True)
    primary[881] = 1
    image[16 * block : 17 * block] = primary
    image[17 * block : 17 * block + 7] = b"\xffCD001\x01"
    image[18 * block : 18 * block + 10] = b"\x01\x00" + struct.pack("<IH", 20, 1) + b"\0\0"
    image[19 * block : 19 * block + 10] = b"\x01\x00" + struct.pack(">IH", 20, 1) + b"\0\0"
    records = record(b"\x00", 20, block, True) + record(b"\x01", 20, block, True)
    for sector, (name, raw) in enumerate(files.items(), 21):
        records += record(name.encode(), sector, len(raw))
        image[sector * block : sector * block + len(raw)] = raw
    image[20 * block : 20 * block + len(records)] = records
    image[:16] = b"\x7fELF\x02\x01\x01\x00AI\x01\x00\x00\x00\x00\x00"
    image[18:20] = struct.pack("<H", 62)
    path.write_bytes(image)
    return path


class FakeRunner:
    """An in-memory Podman/Distrobox daemon; never installs anything on the host."""

    def __init__(self):
        self.calls = []
        self.container = None
        self.image_exists = False
        self.fail_download = False
        self.fail_remove = False
        self.fail_image_remove = False
        self.create_failure_after_pull = False
        self.package_present = False
        self.busy = False
        self.fail_temp_cleanup = False
        self.release = 'ID=debian\nVERSION_ID="13"\n'

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ["podman", "run"] and argv[-1] == "/usr/lib/os-release":
            return self.release
        if argv[:2] == ["podman", "info"]:
            return json.dumps({"host": {"security": {"rootless": True}}})
        if argv[:2] == ["podman", "images"]:
            rows = [{"Id": "image123", "Names": ["docker.io/library/debian:stable"]}]
            return json.dumps(rows if self.image_exists else [])
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([{"Names": [self.container["Name"]]}] if self.container else [])
        if argv[:2] == ["podman", "inspect"]:
            assert self.container is not None
            return json.dumps([self.container])
        if argv[:2] == ["podman", "pull"]:
            if not self.image_exists:
                self.image_exists = True
                if callback := _kwargs.get("on_output"):
                    callback("Copying blob abcdef123456 [----] 1.0MiB / 2.0MiB\r")
                    callback("Writing manifest to image destination\r")
            return ""
        if argv[:2] == ["distrobox", "create"]:
            self.image_exists = True
            if self.create_failure_after_pull:
                raise CommandError(argv, 1, "network is unreachable")
            name = argv[argv.index("--name") + 1]
            flags = shlex.split(argv[argv.index("--additional-flags") + 1])
            labels = dict(flag.removeprefix("--label=").split("=", 1) for flag in flags)
            labels["manager"] = "distrobox"
            self.container = {
                "Name": name,
                "Id": "container123",
                "Image": "sha256:image123",
                "Config": {"Labels": labels},
            }
        if "apt-get" in argv and "install" in argv and self.fail_download:
            raise CommandError(argv, 100, "Could not resolve repository.example")
        if argv[:2] == ["podman", "cp"] and argv[2].endswith(":/usr/lib/os-release"):
            Path(argv[3]).write_text(self.release)
        if "rm" in argv and argv[:2] == ["distrobox", "enter"]:
            assert argv[argv.index("--") + 1 :][:4] == ["sudo", "-n", "rm", "-f"]
            if self.fail_temp_cleanup:
                raise CommandError(argv, 1, "Permission denied")
        if argv[:2] == ["distrobox", "enter"]:
            command = argv[argv.index("--") + 1 :]
            if command[0] == "stat":
                return "1000:41c0"
            if command == ["id", "-u"]:
                return "1000"
        if "python3" in argv and any(arg.endswith("/guest.py") for arg in argv):
            if "--state" in argv:
                return MARKER + json.dumps({"present": self.package_present})
            if "--busy" in argv:
                return MARKER + json.dumps({"busy": self.busy})
            if "--remove" in argv:
                self.package_present = False
                return MARKER + json.dumps({"present": False})
            if "--run" in argv:
                if (
                    ("apt-get" in argv and "install" in argv)
                    or ("dnf" in argv and "--downloadonly" not in argv)
                    or ("pacman" in argv and "-U" in argv)
                ):
                    self.package_present = True
                return ""
            content = "[Desktop Entry]\nType=Application\nName=Demo\nExec=/usr/bin/demo %U\n"
            return MARKER + json.dumps(
                {
                    "desktops": [
                        {
                            "name": "demo.desktop",
                            "content": base64.b64encode(content.encode()).decode(),
                        }
                    ]
                }
            )
        if argv[:2] == ["podman", "rm"]:
            if self.fail_remove:
                raise CommandError(argv, 125, "container busy")
            self.container = None
        if argv[:3] == ["podman", "image", "rm"]:
            if self.fail_image_remove:
                raise CommandError(argv, 125, "image is in use by another container")
            self.image_exists = False
        return ""


@pytest.fixture
def fake_engine(store, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    progress = []
    engine = Engine(store, threading.Event(), lambda p, label: progress.append((p, label)))
    engine.runner = runner
    engine.test_progress = progress
    return engine

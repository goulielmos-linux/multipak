import importlib.util
import os
import stat
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from multipak import android_instances

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"
HELPER = "io.github.goulielmos_linux.multipak.controller"


@pytest.fixture
def files(monkeypatch, tmp_path):
    """multipak_files with a fake Android /data where the helper runs as the test user."""
    context = ModuleType("multipak_instance")
    context.current = lambda: SimpleNamespace(
        id="androida",
        label="Android 13",
        data=tmp_path / "data",
        user_state=tmp_path / "prefs",
        command=lambda *a: " ".join(
            ["/usr/local/bin/multipak-waydroid", "--instance", "androida", *a]
        ),
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_outbox", "multipak_desktop", "multipak_files"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    monkeypatch.setattr(sys.modules["multipak_outbox"], "helper_uid", lambda _data: os.getuid())
    monkeypatch.setenv("LC_ALL", "pt_BR.UTF-8")
    module.download = tmp_path / "data/media/0/Download"
    module.download.mkdir(parents=True)
    module.sent = tmp_path / f"data/data/{HELPER}/files/sent"
    module.sent.mkdir(parents=True)
    return module


@pytest.mark.parametrize(
    "name", ["foto.jpg", ".bashrc", "relatório final (1).pdf", "日本語.txt", "a" * 255]
)
def test_plain_file_names_pass(files, name):
    assert files.check_name(name) == name


@pytest.mark.parametrize(
    "name", ["", ".", "..", "a/b", "../x", "a\nb", "a\0b", "\x1b[31m", "\udcff", "a" * 256, 7]
)
def test_unsafe_file_names_are_refused(files, name):
    with pytest.raises(ValueError):
        files.check_name(name)


def test_a_taken_name_gets_a_number_and_stays_valid(files):
    assert files.free_name("foto.jpg", set()) == "foto.jpg"
    assert files.free_name("foto.jpg", {"foto.jpg", "foto (1).jpg"}) == "foto (2).jpg"
    assert files.free_name(".bashrc", {".bashrc"}) == ".bashrc (1)"
    long = "a" * 251 + ".txt"
    assert len(files.numbered(long, 12).encode()) <= 255 and files.numbered(long, 12).endswith(
        " (12).txt"
    )


class Run:
    """Stands in for subprocess.run: reads the pipe as Android's shell would."""

    def __init__(self, code=0):
        self.code, self.command, self.data = code, None, b""

    def __call__(self, command, stdin, **_):
        self.command = command
        while chunk := os.read(stdin, 65536):
            self.data += chunk
        return SimpleNamespace(
            returncode=self.code, stdout="", stderr="" if not self.code else "cat: No space left"
        )


def feed(data):
    reading, writing = os.pipe()
    os.write(writing, data)
    os.close(writing)
    return reading


def test_android_writes_the_pipe_to_a_free_name_in_download_as_its_shell_user(files):
    (files.download / "foto.jpg").write_bytes(b"old")
    run, pipe = Run(), feed(b"imagem")
    try:
        assert (
            files.send(pipe, "foto.jpg", 6, ["lxc-attach", "-n", "androida"], run) == "foto (1).jpg"
        )
    finally:
        os.close(pipe)
    assert run.data == b"imagem" and (files.download / "foto.jpg").read_bytes() == b"old"
    shell = run.command.index("--")
    assert run.command[:shell] == ["lxc-attach", "-n", "androida", "-u", "2000", "-g", "1023"]
    assert run.command[shell + 1 : shell + 3] == ["/system/bin/sh", "-c"]
    # Name and size are arguments, never part of the script.
    assert run.command[shell + 3] == files.SCRIPT
    assert run.command[shell + 4 :] == ["sh", "/mnt/user/0/emulated/0/Download/foto (1).jpg", "6"]
    assert "set -C" in files.SCRIPT and 'rm -f "$1"' in files.SCRIPT and '-eq "$2"' in files.SCRIPT


def test_the_sent_file_is_listed_with_a_fixed_command(files):
    assert files.scan_command("foto (1).jpg") == (
        "/system/bin/content", "call", "--uri", "content://media/", "--method", "scan_file",
        "--arg", "/storage/emulated/0/Download/foto (1).jpg",
    )  # fmt: skip
    with pytest.raises(ValueError):
        files.scan_command("../x")
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    body = source[source.index("def send_file(") :].split("\ndef ", 1)[0]
    assert "multipak_files.scan_command(chosen)" in body and "timeout=30" in body


def test_a_failed_or_cancelled_send_is_an_error(files):
    pipe = feed(b"abc")
    try:
        with pytest.raises(RuntimeError, match="whole file.*No space"):
            files.send(pipe, "a.txt", 10, ["lxc-attach"], Run(code=1))
    finally:
        os.close(pipe)


def test_only_a_readable_pipe_reaches_android(files, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("x")
    reading, writing = os.pipe()
    opened = [os.open(secret, os.O_RDONLY), os.open(secret, os.O_PATH), writing]
    try:
        for descriptor in opened:
            with pytest.raises(ValueError, match="pipe"):
                files.send(descriptor, "a.txt", 1, ["lxc-attach"], Run())
        with pytest.raises(ValueError):
            files.send(reading, "../a.txt", 1, ["lxc-attach"], Run())
        with pytest.raises(ValueError):
            files.send(reading, "a.txt", -1, ["lxc-attach"], Run())
    finally:
        for descriptor in [*opened, reading]:
            os.close(descriptor)


def sent(files, name, data=b"data"):
    path = files.sent / name
    path.write_bytes(data)
    return path


def test_files_from_android_are_checked_opened_and_deleted(files, tmp_path):
    sent(files, "200-b-relatório.pdf", b"pdf")
    sent(files, "100-a-foto.jpg", b"jpg")
    outside = tmp_path / "private"
    outside.write_bytes(b"secret")
    (files.sent / "300-c-link.txt").symlink_to(outside)
    os.link(sent(files, "400-d-dois.txt"), tmp_path / "second-link")
    sent(files, "500-e-a\nb.txt")
    sent(files, "solto.txt")
    found = files.take()
    try:
        assert [name for name, _ in found] == ["foto.jpg", "relatório.pdf"]
        assert [os.read(descriptor, 10) for _, descriptor in found] == [b"jpg", b"pdf"]
    finally:
        for _, descriptor in found:
            os.close(descriptor)
    assert not list(files.sent.iterdir()) and outside.read_bytes() == b"secret"


def test_a_folder_replaced_by_a_link_gives_nothing(files, tmp_path):
    files.sent.rmdir()
    (tmp_path / "elsewhere").mkdir()
    files.sent.symlink_to(tmp_path / "elsewhere")
    (tmp_path / "elsewhere/1-a-x.txt").write_text("x")
    assert files.take() == []
    assert (tmp_path / "elsewhere/1-a-x.txt").exists()


def test_saving_never_replaces_or_follows_an_existing_file(files, tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "foto.jpg").write_text("old")
    (folder / "foto (1).jpg").symlink_to(tmp_path / "target")
    path = tmp_path / "incoming"
    path.write_bytes(b"new")
    with open(path, "rb") as incoming:
        assert files.save("foto.jpg", incoming.fileno(), str(folder)) == "foto (2).jpg"
    assert (folder / "foto (2).jpg").read_bytes() == b"new" and (
        folder / "foto.jpg"
    ).read_text() == "old"
    assert not (tmp_path / "target").exists()


def test_saving_keeps_space_free(files, tmp_path, monkeypatch):
    monkeypatch.setattr(files, "RESERVE", 1 << 62)
    path = tmp_path / "incoming"
    path.write_bytes(b"new")
    with open(path, "rb") as incoming, pytest.raises(OSError):
        files.save("a.txt", incoming.fileno(), str(tmp_path / "Downloads"))
    assert not list((tmp_path / "Downloads").iterdir())


@pytest.fixture
def dbus(monkeypatch):
    module = ModuleType("dbus")

    class DBusException(Exception):
        def get_dbus_message(self):
            return str(self)

    module.DBusException = DBusException
    module.UInt64 = module.UInt32 = int

    class UnixFd:  # Like dbus-python: keeps its own copy from the start.
        def __init__(self, descriptor):
            self.descriptor = os.dup(descriptor)

        def take(self):
            descriptor, self.descriptor = self.descriptor, -1
            return descriptor

        def __del__(self):
            if self.descriptor >= 0:
                os.close(self.descriptor)

    module.types = SimpleNamespace(UnixFd=UnixFd)
    module.SessionBus = lambda: (_ for _ in ()).throw(DBusException("no session bus"))
    monkeypatch.setitem(sys.modules, "dbus", module)
    return module


class Service:
    """The root service: reads the pipe in another thread, as Android does."""

    def __init__(self, fail=False):
        self.fail, self.data = fail, b""

    def SendFile(self, handle, name, size, timeout):
        descriptor = handle.take()
        try:
            if self.fail:
                raise RuntimeError("Android did not receive the whole file")
            while chunk := os.read(descriptor, 7):
                self.data += chunk
            return name
        finally:
            os.close(descriptor)


def test_the_command_line_sends_a_file_with_progress_lines(files, dbus, tmp_path, capsys):
    path = tmp_path / "vídeo.mp4"
    path.write_bytes(b"x" * 100)
    service = Service()
    assert files.send_file(service, str(path), lines=True) == "vídeo.mp4"
    assert service.data == b"x" * 100
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "START 100 vídeo.mp4" and out[-1] == "DONE vídeo.mp4"
    assert "PROGRESS 100 100" in out


def test_a_refused_send_and_a_folder_are_reported(files, dbus, tmp_path, capsys):
    path = tmp_path / "a.txt"
    path.write_bytes(b"abc")
    with pytest.raises(RuntimeError, match="whole file"):
        files.send_file(Service(fail=True), str(path), lines=True)
    assert capsys.readouterr().out.splitlines()[-1] == "ERROR failed a.txt"
    with pytest.raises(IsADirectoryError, match="é uma pasta"):
        files.send_file(Service(), str(tmp_path), lines=True)


def test_cancelling_stops_writing_so_android_gets_a_short_file(files, dbus, tmp_path):
    path = tmp_path / "grande.bin"
    path.write_bytes(b"x" * (4 * files.CHUNK))
    started = threading.Event()

    class Slow(Service):
        def SendFile(self, handle, name, size, timeout):
            descriptor = handle.take()
            try:
                self.data += os.read(descriptor, files.CHUNK)
                started.set()
                progress.cancelled = True
                while chunk := os.read(descriptor, files.CHUNK):
                    self.data += chunk
                raise RuntimeError("Android did not receive the whole file")
            finally:
                os.close(descriptor)

    progress = None
    original = files._Progress

    def keep(lines):
        nonlocal progress
        progress = original(lines)
        return progress

    files._Progress = keep
    service = Slow()
    with pytest.raises(RuntimeError):
        files.send_file(service, str(path), lines=True)
    assert started.is_set() and len(service.data) < path.stat().st_size


def test_a_send_that_does_not_start_is_not_announced_as_sending(files, dbus, monkeypatch):
    shown = []
    monkeypatch.setattr(
        files, "notify", lambda summary, body, replaces=0: shown.append((summary, body))
    )
    tools = ModuleType("tools")
    tools.helpers = ModuleType("tools.helpers")
    tools.helpers.ipc = SimpleNamespace(
        DBusContainerService=lambda: SimpleNamespace(GetSession=lambda: {"state": "STOPPED"})
    )
    for name, module in (
        ("tools", tools),
        ("tools.helpers", tools.helpers),
        ("tools.helpers.ipc", tools.helpers.ipc),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    glib = ModuleType("dbus.mainloop.glib")
    glib.DBusGMainLoop = lambda set_as_default: None
    mainloop = ModuleType("dbus.mainloop")
    mainloop.glib = glib
    monkeypatch.setattr(dbus, "mainloop", mainloop, raising=False)
    monkeypatch.setitem(sys.modules, "dbus.mainloop", mainloop)
    monkeypatch.setitem(sys.modules, "dbus.mainloop.glib", glib)
    with pytest.raises(SystemExit):
        files.run(SimpleNamespace(FILE=["/tmp/x"], progress=False))
    assert shown == [("Não enviado para Android 13", files.TEXTS["pt"]["off"])]


def test_menus_send_to_this_environment_only(files, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    old = tmp_path / "share/nautilus/scripts/Send to Android/Antigo"
    old.parent.mkdir(parents=True)
    old.write_text("exec /usr/local/bin/multipak-waydroid --instance androida send -- $X\n")
    other = old.with_name("Outro")
    other.write_text("exec /usr/local/bin/multipak-waydroid --instance androidb send -- $X\n")
    files.register(tmp_path / "share")
    menu = tmp_path / "share/kio/servicemenus/multipak-waydroid.androida.send.desktop"
    script = tmp_path / "share/nautilus/scripts/Enviar para o Android/Android 13"
    text = menu.read_text()
    assert "X-KDE-Submenu=Send to Android\nX-KDE-Submenu[pt]=Enviar para o Android\n" in text
    icon = tmp_path / "share/icons/hicolor/scalable/apps/multipak-android-symbolic.svg"
    assert "Icon=multipak-android-symbolic\n" in text and "ColorScheme-Text" in icon.read_text()
    assert not icon.stat().st_mode & stat.S_IXUSR
    assert "Name=Android 13\n" in text and "MimeType=application/octet-stream;\n" in text
    assert "Exec=/usr/local/bin/multipak-waydroid --instance androida send %F\n" in text
    assert "--instance androida send -- $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" in script.read_text()
    assert "set -f" in script.read_text()
    for path in (menu, script):
        assert path.stat().st_mode & stat.S_IXUSR
    assert not old.exists() and other.exists()


def test_the_root_service_offers_send_and_take_only_to_the_owner():
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    for method in ("SendFile", "TakeFiles"):
        body = source[source.index(f"def {method}(") :].split("\n    @", 1)[0]
        assert "self._authorize(sender, conn)" in body
    assert "in_signature='hst'" in source and "out_signature='a(sh)'" in source
    assert 'async_callbacks=("reply", "error")' in source
    assert "multipak_folders" not in source and "ShareFolders" not in source
    arguments = (VENDOR / "tools/helpers/arguments.py").read_text()
    assert 'add_parser("send"' in arguments and 'add_parser("folders"' not in arguments
    assert not (VENDOR / "multipak_folders.py").exists()


def test_the_helper_shares_only_content_into_its_private_folder():
    root = Path(__file__).resolve().parents[1] / "android/controller"
    manifest = (root / "AndroidManifest.xml").read_text()
    activity = manifest[manifest.index('android:name=".ShareActivity"') :].split("</activity>")[0]
    assert 'android:label="Linux"' in activity and "android.intent.action.SEND_MULTIPLE" in activity
    java = (
        root / "src/io/github/goulielmos_linux/multipak/controller/ShareActivity.java"
    ).read_text()
    assert "SCHEME_CONTENT.equals(uri.getScheme())" in java
    assert (
        'new File(context.getFilesDir(), "sent")' in java and ".part" in java and "renameTo" in java
    )


class Runner:
    def __init__(self, lines, error=None):
        self.lines, self.error, self.argv = lines, error, None

    def run(self, argv, timeout, on_output):
        self.argv = argv
        text = "".join(line + "\n" for line in self.lines)
        on_output(text[:7])
        on_output(text[7:])
        if self.error:
            raise self.error


def test_the_manager_follows_the_progress_and_translates_errors():
    from multipak.process import CommandError

    seen = []
    runner = Runner(["START 10 a.txt", "PROGRESS 5 10", "PROGRESS 10 10", "DONE a.txt"])
    android_instances.send_files(runner, "androida", ["/home/u/a.txt"], lambda *a: seen.append(a))
    assert runner.argv == [
        android_instances.CLI,
        "--instance",
        "androida",
        "send",
        "--progress",
        "--",
        "/home/u/a.txt",
    ]
    assert seen == [(0, 10, "a.txt"), (5, 10, "a.txt"), (10, 10, "a.txt")]
    error = CommandError([android_instances.CLI], 1, "")
    with pytest.raises(RuntimeError, match="Android is off"):
        android_instances.send_files(Runner(["ERROR off"], error), "androida", ["/a"])
    with pytest.raises(RuntimeError, match="Fotos is a folder"):
        android_instances.send_files(Runner(["ERROR folder Fotos"], error), "androida", ["/a"])
    with pytest.raises(RuntimeError, match="Could not send b.txt"):
        android_instances.send_files(
            Runner(["START 3 b.txt", "ERROR failed b.txt"], error), "androida", ["/a"]
        )
    with pytest.raises(ValueError):
        android_instances.send_files(Runner([]), "androida", [])

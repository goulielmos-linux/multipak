import importlib.util
import json
import os
import queue
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/waydroid"
HELPER = "io.github.goulielmos_linux.multipak.controller"
MUSIC = "multipak-waydroid.androida.org.example.music"


@pytest.fixture
def volume(monkeypatch, tmp_path):
    context = ModuleType("multipak_instance")
    context.current = lambda: SimpleNamespace(
        id="androida",
        user_state=tmp_path,
        exchange=tmp_path / "exchange",
        container_bus="id.waydro.Container.androida",
        desktop_glob="multipak-waydroid.androida.*.desktop",
        desktop_name=lambda package: f"multipak-waydroid.androida.{package}.desktop",
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_outbox", "multipak_media", "multipak_volume"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def loop(monkeypatch):
    """A fake GLib: timers and watches are recorded, or run at once when "now" is set."""
    record = SimpleNamespace(timers=[], watches=[], now=False)

    def timeout_add(_delay, function):
        if record.now:
            function()
        else:
            record.timers.append(function)
        return len(record.timers) or 1

    glib = SimpleNamespace(
        SOURCE_REMOVE=False, SOURCE_CONTINUE=True, IO_IN=1, IO_HUP=16, timeout_add=timeout_add,
        io_add_watch=lambda *arguments: record.watches.append(arguments),
    )
    gi, repository = ModuleType("gi"), ModuleType("gi.repository")
    gi.repository, repository.GLib = repository, glib
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    return record


@pytest.fixture
def processes(volume, monkeypatch):
    """Records the programs the session would start instead of starting them."""
    started = []

    class Process:
        def __init__(self, command, **_options):
            self.command, self.ended = list(command), False
            self.stdout = SimpleNamespace(fileno=lambda: 7)
            started.append(self)

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        def wait(self):
            return 0

    monkeypatch.setattr(volume.subprocess, "Popen", Process)
    return started


def listing(*streams):
    return json.dumps([
        {"mute": mute, "volume": {name: {"value": value} for name in channels},
         "properties": {"application.id": identity}}
        for identity, value, mute, channels in streams
    ])


def test_only_the_helpers_own_apk_becomes_the_class_path(volume):
    new = f"/data/app/~~Qx3_-aB==/{HELPER}-9Zk_-w==/base.apk"
    assert volume.apk_path(f"package:{new}\n") == new
    old = f"/data/app/{HELPER}-1/base.apk"
    assert volume.apk_path("package:" + old) == old
    for output in (
        "",
        new,
        f"package:{new}\npackage:{new}",
        f"package:{new} --set-var X=1",
        f"package:/data/app/../local/tmp/{HELPER}-1/base.apk",
        f"package:/data/app/~~a==/../{HELPER}-1/base.apk",
        "package:/data/app/~~a==/org.example.other-b==/base.apk",
        f"package:/data/app/~~a==/{HELPER}-b==/split.apk",
        f"package:/data/local/tmp/{HELPER}-1/base.apk",
    ):
        with pytest.raises(ValueError):
            volume.apk_path(output)


def test_what_the_helper_says_is_checked(volume):
    assert volume.check_playing("playing") == []
    assert volume.check_playing("playing org.b.c org.a.b org.a.b") == ["org.a.b", "org.b.c"]
    crowd = " ".join(f"org.example.app{number}" for number in range(volume.MAX_APPS + 1))
    for line in (
        "ready", "Playing org.a.b", "playing ", "playing  org.a.b", "playing org.a.b;reboot",
        "playing ../../etc", "playing org.a.b\tx.y", "playing android", "playing " + crowd,
    ):
        with pytest.raises(ValueError):
            volume.check_playing(line)


def test_a_volume_is_one_fixed_line(volume):
    assert volume.command("org.example.music", 40) == b"volume org.example.music 40\n"
    assert volume.command("org.example.music", 0) == b"volume org.example.music 0\n"
    for package, percent in (
        ("org.example.music\nvolume org.example.other 0", 40), ("org.example.music 0", 40),
        ("music", 40), (None, 40), ("org.example.music", -1), ("org.example.music", 101),
        ("org.example.music", True), ("org.example.music", 40.0), ("org.example.music", "40"),
    ):
        with pytest.raises(ValueError):
            volume.command(package, percent)


def test_the_root_service_runs_only_the_fixed_helper(volume):
    assert volume.PATH_COMMAND == ("package", "path", HELPER)
    assert volume.COMMAND == ("/system/bin/app_process", "/", HELPER + ".Volume")
    root = (VENDOR / "tools/actions/container_manager.py").read_text()
    for name, body in (
        ("WatchVolumes", "multipak_volume.apk_path(self._cmd(*multipak_volume.PATH_COMMAND))"),
        ("PlayingApps", "return self.args.volumes.apps"),
        ("AppVolume", "self.args.volumes.set(str(package), int(percent))"),
    ):
        method = root[root.index(f"def {name}(") :].split("\n    @", 1)[0]
        assert "self._authorize(sender, conn)" in method
        assert body in method
    assert '"--set-var", "CLASSPATH=" + path, "--", *multipak_volume.COMMAND]' in root
    assert "args.volumes.stop()" in root
    # When Android is ready, and again when an update gives the helper app a new file.
    session = (VENDOR / "tools/services/user_manager.py").read_text()
    assert session.count("multipak_volume.allow(tools.helpers.ipc.DBusContainerService())") == 2
    assert "if self.args.volumes.runs(command):" in root
    manager = (VENDOR / "tools/actions/session_manager.py").read_text()
    assert "args.app_volumes = multipak_volume.listen(session)" in manager
    # The class is in the app's file but is no part of the app Android starts.
    source = ROOT / "android/controller/src" / HELPER.replace(".", "/") / "Volume.java"
    assert "public static void main(" in source.read_text()
    assert "Volume" not in (ROOT / "android/controller/AndroidManifest.xml").read_text()


SPEAKS = """
import sys
print("ready", flush=True)
print("playing org.example.music", flush=True)
for line in sys.stdin:
    _, package, percent = line.split()
    print("playing heard." + package + ".at" + percent, flush=True)
"""

MISBEHAVES = """
import sys
print("playing org.example.music", flush=True)
sys.stdin.readline()
print("playing ../../etc/passwd", flush=True)
print("playing org.example.music org.example.radio", flush=True)
sys.stdin.readline()
print("x" * 100000, flush=True)
sys.stdin.read()
"""


def test_the_helper_names_checked_apps_and_is_given_fixed_lines(volume, loop):
    loop.now = True
    told = queue.Queue()
    helper = volume.Helper([sys.executable, "-c", SPEAKS], lambda: told.put(None))
    told.get(timeout=10)
    assert helper.apps == ["org.example.music"]
    helper.set("org.example.radio", 40)
    told.get(timeout=10)
    assert helper.apps == ["heard.org.example.radio.at40"]
    with pytest.raises(ValueError):
        helper.set("org.example.radio\nvolume org.example.music 0", 40)
    assert helper.runs([sys.executable, "-c", SPEAKS])
    assert not helper.runs([sys.executable, "-c", MISBEHAVES])  # An update moved the app.
    helper.stop()  # Closing its input is what ends it.
    told.get(timeout=10)
    assert helper.apps == []
    assert helper.process.wait(timeout=10) == 0
    assert not helper.runs([sys.executable, "-c", SPEAKS])
    helper.set("org.example.radio", 40)  # Gone: nothing to write to, and no error.


def test_a_helper_that_misbehaves_is_dropped(volume, loop):
    loop.now = True
    told = queue.Queue()
    helper = volume.Helper([sys.executable, "-c", MISBEHAVES], lambda: told.put(None))
    told.get(timeout=10)
    assert helper.apps == ["org.example.music"]
    helper.set("org.example.music", 1)
    told.get(timeout=10)  # The invalid line changed nothing; the next one did.
    assert helper.apps == ["org.example.music", "org.example.radio"]
    helper.set("org.example.music", 2)
    told.get(timeout=10)  # A line that never ends: the helper is cut off on both sides.
    assert helper.apps == []
    helper.process.wait(timeout=10)


def test_signals_are_spaced_out(volume, loop):
    notified = []
    helper = volume.Helper([sys.executable, "-c", "pass"], lambda: notified.append(1))
    helper.process.wait(timeout=10)
    glib = SimpleNamespace(timeout_add=lambda *timer: loop.timers.append(timer))
    for apps in (["org.a.b"], ["org.a.b"], [], ["org.c.d"]):
        helper._show(apps, glib, print)
    assert loop.timers == [(volume.DELAY_MS, print)]  # One signal for the three changes.


def test_each_app_with_a_shortcut_gets_a_named_silent_stream(volume, processes, tmp_path):
    pytest.importorskip("gi")
    applications = tmp_path / "applications"
    applications.mkdir()
    (applications / (MUSIC + ".desktop")).write_text(
        "[Desktop Entry]\nType=Application\nName=Música — Android\n"
    )
    icons = tmp_path / volume.ICONS
    icons.mkdir(parents=True)
    crashed = icons / "multipak-waydroid.androida.org.example.old.png"
    other = icons / "multipak-waydroid.androidb.org.example.old.png"
    crashed.symlink_to("/nowhere")
    other.symlink_to("/nowhere")
    sent = []
    mixer = volume.Mixer(tmp_path, lambda package, percent: sent.append((package, percent)))
    assert not crashed.is_symlink() and other.is_symlink()

    # Parts of Android have no shortcut; "android" is not even a package ID.
    mixer.show(["org.example.music", "com.android.systemui", "android"])
    assert [process.command for process in processes] == [[
        "paplay", "--raw", "--rate=8000", "--channels=1", "--format=s16le", "--latency-msec=500",
        "--client-name=Música — Android", "--stream-name=Android",
        "--property=application.id=" + MUSIC, "--property=application.icon_name=" + MUSIC,
        "/dev/zero",
    ]]
    icon = icons / (MUSIC + ".png")
    assert os.readlink(icon) == str(tmp_path / "exchange/icons/org.example.music.png")

    # The user moves the app's slider; other streams, even with an identity, are not ours.
    both = ("front-left", "front-right")
    mixer.read(listing((MUSIC, 26214, False, ["mono"]), ("org.kde.elisa", 6554, False, both)))
    mixer.read(listing((MUSIC, 26214, False, ["mono"])))
    assert sent == [("org.example.music", 40)]
    mixer.read(listing((MUSIC, 26214, True, ["mono"])))
    mixer.read(listing((MUSIC, 98304, False, both)))
    assert sent[1:] == [("org.example.music", 0), ("org.example.music", 100)]

    mixer.show([])
    assert processes[0].ended and not icon.is_symlink()
    # It plays again: the stream comes back, and Android still has the volume it was given.
    mixer.show(["org.example.music"])
    mixer.read(listing((MUSIC, 65536, False, ["mono"])))
    assert len(processes) == 2 and len(sent) == 3
    # A stream that ended on its own (the sound server restarted) is started again.
    processes[1].ended = True
    mixer.show(["org.example.music"])
    assert len(processes) == 3
    mixer.close()
    assert processes[2].ended and not icon.is_symlink()


def test_the_session_passes_volumes_to_the_root_service(
    volume, processes, loop, monkeypatch, tmp_path
):
    record = SimpleNamespace(receivers=[], volumes=[], playing=["org.example.music"])
    dbus = ModuleType("dbus")
    dbus.SystemBus = lambda: SimpleNamespace(
        add_signal_receiver=lambda function, **match: record.receivers.append((function, match))
    )

    class Manager:
        def PlayingApps(self, reply_handler, error_handler):
            reply_handler(record.playing)

        def AppVolume(self, package, percent, reply_handler, error_handler):
            record.volumes.append((package, percent))

    ipc = ModuleType("tools.helpers.ipc")
    ipc.DBusContainerService = Manager
    tools, helpers = ModuleType("tools"), ModuleType("tools.helpers")
    tools.helpers, helpers.ipc = helpers, ipc
    for name, module in {
        "dbus": dbus, "tools": tools, "tools.helpers": helpers, "tools.helpers.ipc": ipc,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        sys.modules["multipak_media"], "describe", lambda _folder, package: ("Music", MUSIC)
    )
    monkeypatch.setattr(volume.shutil, "which", lambda _program: None)
    with pytest.raises(RuntimeError):
        volume.listen({"xdg_data_home": str(tmp_path)})
    monkeypatch.setattr(volume.shutil, "which", lambda program: "/usr/bin/" + program)
    runs = []

    def run(command, **options):
        runs.append((command, options["env"]["LC_ALL"]))
        return SimpleNamespace(stdout=listing((MUSIC, 13107, False, ["mono"])))

    monkeypatch.setattr(volume.subprocess, "run", run)

    close = volume.listen({"xdg_data_home": str(tmp_path)})
    (playing, match), = record.receivers
    assert match == {
        "signal_name": "AppsPlaying", "dbus_interface": "id.waydro.ContainerManager",
        "bus_name": "id.waydro.Container.androida", "path": "/ContainerManager",
    }
    playing()
    monitor, stream = processes
    assert monitor.command == ["pactl", "subscribe"] and stream.command[0] == "paplay"
    # The new stream is read once without waiting for the monitor.
    loop.timers.pop()()
    assert runs == [(volume.LIST, "C.UTF-8")] and record.volumes == [("org.example.music", 20)]
    close()
    assert monitor.ended and stream.ended

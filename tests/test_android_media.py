import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


@pytest.fixture
def media(monkeypatch, tmp_path):
    context = ModuleType("multipak_instance")
    context.current = lambda: SimpleNamespace(
        id="androida",
        user_state=tmp_path,
        desktop_name=lambda package: f"multipak-waydroid.androida.{package}.desktop",
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_media"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def bus(monkeypatch):
    """A fake dbus-python and container service that record what the session does."""
    record = SimpleNamespace(names=[], signals=[], calls=[], state={})
    dbus = ModuleType("dbus")
    dbus.ObjectPath = dbus.String = str
    dbus.Int64 = int
    dbus.Array = lambda value, signature=None: list(value)
    dbus.Dictionary = lambda value, signature=None: dict(value)
    dbus.SessionBus = lambda: "session"
    service = ModuleType("dbus.service")

    class Object:
        def __init__(self, connection, path):
            record.path, record.player = path, self

    class BusName:
        def __init__(self, name, connection):
            record.names.append(name)

        def __del__(self):
            record.names.append("released")

    def signal(*_args, **_kwargs):
        def decorate(function):
            def emit(self, *args):
                record.signals.append(args)

            return emit

        return decorate

    service.Object, service.BusName, service.signal = Object, BusName, signal
    service.method = lambda *_a, **_k: lambda function: function
    dbus.service = service

    class Manager:
        def MediaState(self, reply_handler, error_handler):
            reply_handler(record.state)

        def MediaCommand(self, key, reply_handler, error_handler):
            record.calls.append(key)

    ipc = ModuleType("tools.helpers.ipc")
    ipc.DBusContainerService = Manager
    tools, helpers = ModuleType("tools"), ModuleType("tools.helpers")
    tools.helpers, helpers.ipc = helpers, ipc
    for name, module in {
        "dbus": dbus, "dbus.service": service, "tools": tools,
        "tools.helpers": helpers, "tools.helpers.ipc": ipc,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return record


def test_media_commands_are_fixed(media):
    assert media.LISTENER_COMMAND == (
        "notification", "allow_listener",
        "io.github.goulielmos_linux.multipak.controller/io.github.goulielmos_linux.multipak.controller.MediaListener",
    )
    assert set(media.KEYS) == {"play-pause", "play", "pause", "stop", "next", "previous"}
    assert media.INPUT == "/system/bin/input"
    assert all(
        command == ("keyevent", "KEYCODE_MEDIA_" + key.upper().replace("-", "_"))
        for key, command in media.KEYS.items()
    )
    root = (VENDOR / "tools/actions/container_manager.py").read_text()
    for name, body in (
        ("AllowMediaListener", "self._cmd(*multipak_media.LISTENER_COMMAND)"),
        ("MediaState", "return multipak_outbox.media()"),
        ("MediaCommand", "command = multipak_media.KEYS.get(str(key))"),
    ):
        method = root[root.index(f"def {name}(") :].split("\n    @", 1)[0]
        assert "self._authorize(sender, conn)" in method
        assert body in method
    assert "self._cmd(*command, program=multipak_media.INPUT)" in root
    session = (VENDOR / "tools/services/user_manager.py").read_text()
    assert "multipak_media.allow(tools.helpers.ipc.DBusContainerService())" in session
    manager = (VENDOR / "tools/actions/session_manager.py").read_text()
    assert "multipak_links.listen(media)" in manager


def test_the_player_shows_only_while_something_plays(media, bus, tmp_path):
    applications = tmp_path / "applications"
    applications.mkdir()
    refresh = media.listen({"xdg_data_home": str(tmp_path)})
    assert bus.path == "/org/mpris/MediaPlayer2"
    bus.state = {"package": "org.example.music", "state": "playing", "title": "Canção", "artist": "Banda"}
    refresh()
    assert bus.names == ["org.mpris.MediaPlayer2.multipak_androida"]
    changed = {interface: values for interface, values, _ in bus.signals}
    player = changed[media.PLAYER]
    assert player["PlaybackStatus"] == "Playing"
    assert player["Metadata"]["xesam:title"] == "Canção"
    assert player["Metadata"]["xesam:artist"] == ["Banda"]
    assert changed[media.ROOT]["Identity"] == "org.example.music"  # No shortcut: the package.

    assert "xesam:artist" not in media.properties(
        {"state": "playing", "title": "x", "artist": "<unknown>"}, ("x", ""), 1
    )[media.PLAYER]["Metadata"]

    bus.signals.clear()
    bus.state = dict(bus.state, state="paused")
    refresh()
    assert bus.signals == [(media.PLAYER, {"PlaybackStatus": "Paused"}, [])]
    assert bus.names == ["org.mpris.MediaPlayer2.multipak_androida"]

    bus.state = {}
    refresh()
    assert bus.names[-1] == "released"


def test_player_buttons_send_media_keys(media, bus, tmp_path):
    media.listen({"xdg_data_home": str(tmp_path)})
    player = bus.player
    for method in (player.PlayPause, player.Play, player.Pause, player.Stop, player.Next, player.Previous):
        method()
    assert bus.calls == ["play-pause", "play", "pause", "stop", "next", "previous"]
    assert player.GetAll(media.PLAYER)["CanGoNext"] is True
    assert player.Get(media.ROOT, "CanRaise") is False

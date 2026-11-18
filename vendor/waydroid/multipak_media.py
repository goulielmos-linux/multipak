# SPDX-License-Identifier: GPL-3.0-or-later
"""Android media in the desktop's media controls (MPRIS).

The helper app leaves the media state in the outbox (checked by multipak_outbox); buttons and
media keys go back as fixed key events."""

import logging
from pathlib import Path

from multipak_input import MAPPER as HELPER
from multipak_instance import current as instance

# Fixed commands run by the root service; a caller only picks a key by name.
LISTENER = HELPER + "/" + HELPER + ".MediaListener"
LISTENER_COMMAND = ("notification", "allow_listener", LISTENER)
# "cmd media_session dispatch" fails on Android 11; "input" works on every version.
INPUT = "/system/bin/input"
KEYS = {
    "play-pause": ("keyevent", "KEYCODE_MEDIA_PLAY_PAUSE"),
    "play": ("keyevent", "KEYCODE_MEDIA_PLAY"),
    "pause": ("keyevent", "KEYCODE_MEDIA_PAUSE"),
    "stop": ("keyevent", "KEYCODE_MEDIA_STOP"),
    "next": ("keyevent", "KEYCODE_MEDIA_NEXT"),
    "previous": ("keyevent", "KEYCODE_MEDIA_PREVIOUS"),
}
STATUS = {"playing": "Playing", "paused": "Paused", "stopped": "Stopped"}
UNKNOWN = "<unknown>"  # What Android's media store gives a file without that tag.
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPERTIES = "org.freedesktop.DBus.Properties"


def bus_name():
    return "org.mpris.MediaPlayer2.multipak_" + instance().id


def allow(manager):
    """Let the helper see the media sessions; called when Android is ready."""
    manager.AllowMediaListener()


def describe(applications, package):
    """(name, desktop id) of an Android app from the shortcut the session wrote."""
    name, entry = package, ""
    try:
        from gi.repository import GLib

        desktop = instance().desktop_name(package)
        keys = GLib.KeyFile()
        keys.load_from_file(str(Path(applications) / desktop), GLib.KeyFileFlags.NONE)
        name = keys.get_locale_string("Desktop Entry", "Name", None) or package
        entry = desktop.removesuffix(".desktop")
    except Exception:  # No shortcut (a system app) or an unreadable one.
        pass
    return name, entry


def properties(state, details, track):
    """MPRIS properties for a checked media state (plain strings from the root service)."""
    import dbus

    name, entry = details
    metadata = {"mpris:trackid": dbus.ObjectPath(f"/io/github/multipak/track/{track}")}
    if state.get("title") not in (None, "", UNKNOWN):
        metadata["xesam:title"] = dbus.String(state["title"])
    if state.get("artist") not in (None, "", UNKNOWN):
        metadata["xesam:artist"] = dbus.Array([dbus.String(state["artist"])], signature="s")
    return {
        ROOT: {
            "CanQuit": False,
            "CanRaise": False,
            "HasTrackList": False,
            "Identity": dbus.String(name),
            "DesktopEntry": dbus.String(entry),
            "SupportedUriSchemes": dbus.Array([], signature="s"),
            "SupportedMimeTypes": dbus.Array([], signature="s"),
        },
        PLAYER: {
            "PlaybackStatus": STATUS.get(state.get("state"), "Stopped"),
            "Metadata": dbus.Dictionary(metadata, signature="sv"),
            "Rate": 1.0,
            "MinimumRate": 1.0,
            "MaximumRate": 1.0,
            "Volume": 1.0,
            "Position": dbus.Int64(0),
            "CanGoNext": True,
            "CanGoPrevious": True,
            "CanPlay": True,
            "CanPause": True,
            "CanSeek": False,
            "CanControl": True,
        },
    }


def listen(session):
    """Publish the Android media in the session; returns refresh(), to call after the
    outbox was read."""
    import dbus
    import dbus.service
    import tools.helpers.ipc

    applications = Path(session["xdg_data_home"]) / "applications"

    class Player(dbus.service.Object):
        def __init__(self):
            super().__init__(dbus.SessionBus(), PATH)
            self.state, self.track, self.name = {}, 0, None
            self.values = properties({}, ("Android", ""), 0)

        def show(self, state):
            state = {str(key): str(value) for key, value in state.items()}
            if state == self.state:
                return
            if (state.get("title"), state.get("artist"), state.get("package")) != (
                self.state.get("title"), self.state.get("artist"), self.state.get("package")
            ):
                self.track += 1
            self.state = state
            details = describe(applications, state["package"]) if state else ("Android", "")
            values = properties(state, details, self.track)
            changed = {
                interface: {k: v for k, v in group.items() if self.values[interface][k] != v}
                for interface, group in values.items()
            }
            self.values = values
            # The desktop shows a player only while something plays or is paused.
            if state.get("state") in ("playing", "paused"):
                if self.name is None:
                    self.name = dbus.service.BusName(bus_name(), dbus.SessionBus())
                for interface, group in changed.items():
                    if group:
                        self.PropertiesChanged(interface, group, [])
            else:
                self.name = None  # Releases the name.

        def press(self, key):
            tools.helpers.ipc.DBusContainerService().MediaCommand(
                key,
                reply_handler=lambda: None,
                error_handler=lambda error: logging.warning(
                    "Could not send the Android media key: %s", error
                ),
            )

        @dbus.service.method(PROPERTIES, in_signature="ss", out_signature="v")
        def Get(self, interface, name):
            return self.values[str(interface)][str(name)]

        @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.values.get(str(interface), {})

        @dbus.service.method(PROPERTIES, in_signature="ssv", out_signature="")
        def Set(self, interface, name, value):
            pass  # Nothing is writable; volume stays with the Linux mixer.

        @dbus.service.signal(PROPERTIES, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        @dbus.service.method(ROOT, in_signature="", out_signature="")
        def Raise(self):
            pass

        @dbus.service.method(ROOT, in_signature="", out_signature="")
        def Quit(self):
            pass

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def PlayPause(self):
            self.press("play-pause")

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def Play(self):
            self.press("play")

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def Pause(self):
            self.press("pause")

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def Stop(self):
            self.press("stop")

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def Next(self):
            self.press("next")

        @dbus.service.method(PLAYER, in_signature="", out_signature="")
        def Previous(self):
            self.press("previous")

        @dbus.service.method(PLAYER, in_signature="x", out_signature="")
        def Seek(self, offset):
            pass

        @dbus.service.method(PLAYER, in_signature="ox", out_signature="")
        def SetPosition(self, track, position):
            pass

        @dbus.service.method(PLAYER, in_signature="s", out_signature="")
        def OpenUri(self, uri):
            pass

    player = Player()

    def refresh():
        tools.helpers.ipc.DBusContainerService().MediaState(
            reply_handler=player.show,
            error_handler=lambda error: logging.warning(
                "Could not read the Android media state: %s", error
            ),
        )

    return refresh

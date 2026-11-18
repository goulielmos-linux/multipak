# SPDX-License-Identifier: GPL-3.0-or-later
"""A volume control for each Android app in the desktop's mixer.

Android plays as one stream ("Waydroid"). The session adds a silent stream for each app that
plays; its volume sets that app's volume inside Android, through the helper's Volume class
started by root, whose output is not trusted."""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from contextlib import suppress
from pathlib import Path

from multipak_input import MAPPER as HELPER
from multipak_instance import current as instance
from multipak_outbox import PACKAGE

# Fixed commands run by the root service; the caller picks only an app and a number.
PATH_COMMAND = ("package", "path", HELPER)
COMMAND = ("/system/bin/app_process", "/", HELPER + ".Volume")
# Where Android keeps an installed app: /data/app/~~<random>==/<package>-<random>==/base.apk.
APK = re.compile(
    r"package:(/data/app/(?:~~[A-Za-z0-9_-]{1,64}==/)?"
    + re.escape(HELPER)
    + r"-[A-Za-z0-9_-]{1,64}(?:==)?/base\.apk)"
)
MAX_LINE = 4096
MAX_APPS = 32
DELAY_MS = 250  # Signals are sent at most this often.
ICONS = "icons/hicolor/96x96/apps"  # Below the user's data folder.
STREAM = ("paplay", "--raw", "--rate=8000", "--channels=1", "--format=s16le", "--latency-msec=500")
LIST = ("pactl", "--format=json", "list", "sink-inputs")
C = {"LC_ALL": "C.UTF-8"}  # Plain numbers and event names from pactl.


def apk_path(output):
    """The installed helper's APK from "cmd package path", or raise ValueError."""
    match = APK.fullmatch(output.strip())
    if not match:
        raise ValueError("The MultiPak helper is not installed")
    return match.group(1)


def check_playing(line):
    """The apps a "playing [package]..." line of the helper names, or raise ValueError."""
    words = line.split(" ")
    if words[0] != "playing" or len(words) > MAX_APPS + 1:
        raise ValueError("Unexpected line")
    if not all(PACKAGE.fullmatch(word) for word in words[1:]):
        raise ValueError("Invalid Android package ID")
    return sorted(set(words[1:]))


def command(package, percent):
    """The line that gives an app its volume, or raise ValueError."""
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        raise ValueError("Invalid Android package ID")
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("Invalid volume")
    return f"volume {package} {percent}\n".encode("ascii")


class Helper:
    """Root service: the helper process and the apps it last said were playing."""

    def __init__(self, arguments, notify):
        from gi.repository import GLib

        self.arguments, self.apps, self._pending = arguments, [], False
        self.process = subprocess.Popen(
            arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        # A frozen Android reads nothing: a full pipe must not hold the service.
        os.set_blocking(self.process.stdin.fileno(), False)

        def fire():
            self._pending = False
            notify()
            return GLib.SOURCE_REMOVE

        def read():
            while True:
                line = self.process.stdout.readline(MAX_LINE + 1)
                if not line.endswith(b"\n"):
                    break  # Android stopped the helper, or the line never ends.
                try:
                    apps = check_playing(line[:-1].decode("ascii"))
                except ValueError:
                    continue  # "ready", or something Android made up.
                self._show(apps, GLib, fire)
            self.stop()
            self.process.stdout.close()  # A helper stuck writing gets an error and leaves.
            self._show([], GLib, fire)
            self.process.wait()

        threading.Thread(target=read, daemon=True).start()

    def _show(self, apps, GLib, fire):
        if apps != self.apps:
            self.apps = apps
            if not self._pending:
                self._pending = True
                GLib.timeout_add(DELAY_MS, fire)

    def runs(self, arguments):
        """Whether the helper is alive and is this one: an update moves the app's file."""
        return self.process.poll() is None and arguments == self.arguments

    def set(self, package, percent):
        line = command(package, percent)
        with suppress(BlockingIOError, BrokenPipeError, ValueError):
            os.write(self.process.stdin.fileno(), line)

    def stop(self):
        # The helper leaves when its input closes; killing lxc-attach would leave it behind.
        with suppress(OSError):
            self.process.stdin.close()


def allow(manager):
    """Start the helper; called when Android is ready and when the app that holds it
    changes, because Android finishes an update after the installer returns."""
    manager.WatchVolumes()


def stream_command(name, entry):
    """The silent stream the mixer shows for an app: its name, its icon and, as the
    identity the desktop remembers the volume by, its shortcut."""
    return [
        *STREAM, "--client-name=" + name, "--stream-name=Android",
        "--property=application.id=" + entry, "--property=application.icon_name=" + entry,
        "/dev/zero",
    ]


def volumes(listing):
    """{stream identity: 0-100} from pactl's list of streams; a muted stream is at 0."""
    found = {}
    for stream in json.loads(listing):
        identity = stream.get("properties", {}).get("application.id")
        channels = [channel["value"] for channel in stream.get("volume", {}).values()]
        if identity and channels:
            percent = 0 if stream.get("mute") else round(sum(channels) / len(channels) * 100 / 65536)
            found[identity] = max(0, min(100, percent))
    return found


class Mixer:
    """Session: the silent streams of the apps that play, and what Android was told."""

    def __init__(self, data_home, send):
        self.applications = Path(data_home) / "applications"
        self.icons = Path(data_home) / ICONS
        self.send = send
        self.streams, self.sent = {}, {}
        # Icons of a session that crashed.
        for icon in self.icons.glob(instance().desktop_glob.removesuffix("desktop") + "png"):
            icon.unlink()

    def show(self, packages):
        """Keep one stream for each app that plays and has a shortcut."""
        from multipak_media import describe

        packages = {str(package) for package in packages}
        for package, (process, _icon) in list(self.streams.items()):
            if package not in packages or process.poll() is not None:
                self.hide(package)
        for package in sorted(packages - set(self.streams)):
            name, entry = describe(self.applications, package)
            if not entry:
                continue  # A part of Android itself: it follows the volume of them all.
            # The mixer takes icons only by name, from the user's icon folders.
            icon = self.icons / (entry + ".png")
            with suppress(OSError):
                self.icons.mkdir(parents=True, exist_ok=True)
                icon.unlink(missing_ok=True)
                icon.symlink_to(instance().exchange / "icons" / (package + ".png"))
                os.utime(self.icons.parents[1])  # Icon caches compare against this folder.
            process = subprocess.Popen(
                stream_command(name, entry), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.streams[package] = (process, icon)

    def hide(self, package):
        process, icon = self.streams.pop(package)
        process.terminate()
        process.wait()
        icon.unlink(missing_ok=True)

    def read(self, listing):
        """Tell Android the volumes the user changed. A new stream comes with the volume
        the desktop remembers for it, which is sent the same way."""
        found = volumes(listing)
        for package, (_process, icon) in self.streams.items():
            percent = found.get(icon.stem)
            if percent is not None and percent != self.sent.get(package, 100):
                self.sent[package] = percent
                self.send(package, percent)

    def close(self):
        for package in list(self.streams):
            self.hide(package)


def listen(session):
    """Follow the apps that play and the volumes the user gives them; returns close().
    Needs paplay and pactl, the PulseAudio tools that PipeWire answers too."""
    import dbus
    import tools.helpers.ipc
    from gi.repository import GLib

    if not (shutil.which("paplay") and shutil.which("pactl")):
        raise RuntimeError("paplay and pactl are not installed")
    environment = {**os.environ, **C}
    state = {"monitor": None, "timer": 0}

    def failed(error):
        logging.warning("Could not follow the volume of the Android apps: %s", error)

    def send(package, percent):
        tools.helpers.ipc.DBusContainerService().AppVolume(
            package, percent, reply_handler=lambda: None, error_handler=failed
        )

    mixer = Mixer(session["xdg_data_home"], send)

    def refresh():
        state["timer"] = 0
        try:
            mixer.read(subprocess.run(
                LIST, capture_output=True, check=True, timeout=5, env=environment
            ).stdout)
        except Exception as error:
            failed(error)
        return GLib.SOURCE_REMOVE

    def changed(descriptor, _condition, monitor):
        data = os.read(descriptor, 65536)
        if not data:
            monitor.wait()
            if state["monitor"] is monitor:
                state["monitor"] = None  # The sound server left; show() starts another.
            return GLib.SOURCE_REMOVE
        if b"sink-input" in data and mixer.streams and not state["timer"]:
            state["timer"] = GLib.timeout_add(100, refresh)
        return GLib.SOURCE_CONTINUE

    def show(packages):
        if state["monitor"] is None:
            monitor = state["monitor"] = subprocess.Popen(
                ("pactl", "subscribe"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=environment,
            )
            GLib.io_add_watch(
                monitor.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP, changed, monitor
            )
        mixer.show(packages)
        # A new stream may be there before the monitor is: read once without being told.
        if mixer.streams and not state["timer"]:
            state["timer"] = GLib.timeout_add(500, refresh)

    def playing():
        tools.helpers.ipc.DBusContainerService().PlayingApps(
            reply_handler=show, error_handler=failed
        )

    dbus.SystemBus().add_signal_receiver(
        playing,
        signal_name="AppsPlaying",
        dbus_interface="id.waydro.ContainerManager",
        bus_name=instance().container_bus,
        path="/ContainerManager",
    )

    def close():
        mixer.close()
        if state["monitor"] is not None:
            state["monitor"].terminate()
            state["monitor"] = None

    return close

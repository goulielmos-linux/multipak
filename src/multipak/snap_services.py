"""Which desktop services a snap expects, and whether this computer has them.

Missing ones are reported before installing. GSettings falls back to a file of its own
when the dconf service is missing, so an application's settings are kept."""

from dataclasses import dataclass

from multipak.localization import mark, tr

from . import host


@dataclass(frozen=True)
class Service:
    interfaces: tuple[str, ...]
    bus: str  # "session", "system" or "path"
    # Any one of these is enough, or a name that starts with one of them.
    names: tuple[str, ...]
    feature: str  # Marked for translation; shown in the warning.


SERVICES = (
    Service(
        ("desktop",),
        "session",
        ("org.freedesktop.portal.Desktop",),
        mark("file chooser and other desktop integration"),
    ),
    Service(
        ("desktop-legacy", "unity7"),
        "session",
        ("org.kde.StatusNotifierWatcher",),
        mark("icon in the system tray"),
    ),
    Service(
        ("password-manager-service",),
        "session",
        ("org.freedesktop.secrets", "org.kde.kwalletd5", "org.kde.kwalletd6"),
        mark("saved passwords"),
    ),
    Service(
        ("calendar-service",),
        "session",
        ("org.gnome.evolution.dataserver.Calendar",),
        mark("calendars (GNOME's evolution-data-server)"),
    ),
    Service(
        ("contacts-service",),
        "session",
        ("org.gnome.evolution.dataserver.AddressBook",),
        mark("contacts (GNOME's evolution-data-server)"),
    ),
    Service(
        ("accounts-service",),
        "session",
        ("org.gnome.OnlineAccounts",),
        mark("GNOME online accounts"),
    ),
    Service(
        ("online-accounts-service",),
        "session",
        ("com.ubuntu.OnlineAccounts.Manager",),
        mark("Ubuntu online accounts"),
    ),
    Service(
        ("screen-inhibit-control",),
        "session",
        (
            "org.freedesktop.ScreenSaver",
            "org.freedesktop.PowerManagement",
            "org.gnome.SessionManager",
            "org.freedesktop.portal.Desktop",
        ),
        mark("keeping the screen on"),
    ),
    Service(
        ("network-manager", "network-manager-observe"),
        "system",
        ("org.freedesktop.NetworkManager",),
        mark("network status"),
    ),
    Service(("upower-observe",), "system", ("org.freedesktop.UPower",), mark("battery status")),
    Service(("bluez",), "system", ("org.bluez",), mark("Bluetooth")),
    Service(
        ("avahi-observe", "avahi-control"),
        "system",
        ("org.freedesktop.Avahi",),
        mark("finding devices on the local network"),
    ),
    Service(
        ("udisks2",), "system", ("org.freedesktop.UDisks2",), mark("disks and removable media")
    ),
    Service(
        ("modem-manager",), "system", ("org.freedesktop.ModemManager1",), mark("mobile modems")
    ),
    Service(
        ("location-observe", "location-control"),
        "system",
        ("com.ubuntu.location.Service",),
        mark("location (Ubuntu's location service)"),
    ),
    Service(
        ("audio-playback", "audio-record", "pulseaudio"),
        "path",
        ("$XDG_RUNTIME_DIR/pulse/native",),
        mark("sound"),
    ),
    Service(("cups", "cups-control"), "path", ("/run/cups/cups.sock",), mark("printing")),
)
DCONF = "ca.desrt.dconf"

PROBE = r"""
printf 'SESSION\n'; busctl --user list --no-legend --no-pager 2>/dev/null || echo FAILED
printf 'SYSTEM\n'; busctl --system list --no-legend --no-pager 2>/dev/null || echo FAILED
printf 'PATHS\n'
for path in "$@"; do [ -e "$path" ] && printf '%s\n' "$path"; done
"""


@dataclass(frozen=True)
class HostState:
    """Bus names (running or started on demand) and paths that exist; None: unknown."""

    session: frozenset | None
    system: frozenset | None
    paths: frozenset


def _expand(path, runtime_directory):
    return path.replace("$XDG_RUNTIME_DIR", runtime_directory)


def probe(runtime_directory):
    """One host call: the session and system bus names and the services' paths."""
    paths = [
        _expand(name, runtime_directory)
        for service in SERVICES
        if service.bus == "path"
        for name in service.names
    ]
    try:
        result = host.run(
            ["sh", "-c", PROBE, "multipak-services", *paths],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, ValueError):
        return HostState(None, None, frozenset())
    sections, current = {"SESSION": [], "SYSTEM": [], "PATHS": []}, None
    for line in result.stdout.splitlines():
        if line in sections:
            current = line
        elif current is not None:
            sections[current].append(line)

    def names(lines):
        if not lines or "FAILED" in lines:
            return None
        # Unique connection names (":1.42") say nothing about services.
        return frozenset(
            line.split()[0] for line in lines if line.strip() and not line.startswith(":")
        )

    return HostState(
        names(sections["SESSION"]), names(sections["SYSTEM"]), frozenset(sections["PATHS"])
    )


def _present(service, state, runtime_directory):
    if service.bus == "path":
        return any(_expand(name, runtime_directory) in state.paths for name in service.names)
    available = state.session if service.bus == "session" else state.system
    if available is None:
        return True  # Unknown: never warn without knowing.
    # Versioned names count too, such as org.gnome.evolution.dataserver.Calendar8.
    return any(item.startswith(name) for name in service.names for item in available)


def missing_features(interfaces, state, runtime_directory):
    """Features of the snap whose service this computer lacks, translated."""
    result = []
    for service in SERVICES:
        if set(service.interfaces) & set(interfaces) and not _present(
            service, state, runtime_directory
        ):
            feature = tr(service.feature)
            if feature not in result:
                result.append(feature)
    return result


def needs_settings_file(interfaces, state):
    """Whether GSettings must keep the app's settings in a file: dconf is missing."""
    if "gsettings" not in interfaces or state.session is None:
        return False
    return DCONF not in state.session

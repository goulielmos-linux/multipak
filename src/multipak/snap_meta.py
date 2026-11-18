"""Read and check meta/snap.yaml, following snapd's own rules where they matter."""

import ipaddress
import posixpath
import re
from dataclasses import dataclass, field

from multipak.localization import tr

from .snap_yaml import YamlError, loads

NAME = re.compile(r"[a-z0-9](?:-?[a-z0-9])*")
APP_NAME = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9])*")
# snapd's appContentWhitelist: what command, stop-command and the like may contain.
COMMAND = re.compile(r"[A-Za-z0-9/. _#:$-]+")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
REVISION = re.compile(r"[0-9]{1,12}")
CONFINEMENTS = ("strict", "devmode", "classic")
SNAP_TYPES = ("app", "base", "os", "kernel", "gadget", "snapd")
ARCHITECTURE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# The kernel's dpkg names for the architectures MultiPak runs on.
ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64"}

# Interfaces that only make sense for system services or that give a snap control of
# the host. Snaps that plug them are refused before anything is downloaded.
# Not here: system-files (system_files below), system-packages-doc (shown read-only
# by snap_sandbox) and mount-control, which only allows the snap to ask for mounts
# later with snapctl mount; MultiPak makes none, and says so when asked.
SYSTEM_INTERFACES = frozenset(
    {
        "block-devices",
        "docker-support",
        "firewall-control",
        "greengrass-support",
        "kernel-module-control",
        "kubernetes-support",
        "kvm",
        "lxd-support",
        "microceph-support",
        "microstack-support",
        "multipass-support",
        "network-control",
        "network-setup-control",
        "snapd-control",
        "system-trace",
    }
)
# snapd's layoutRejectionList: places a layout may never replace.
LAYOUT_REJECTED = (
    "/dev",
    "/proc",
    "/sys",
    "/media",
    "/run",
    "/var/run",
    "/tmp",
    "/var/lib/snapd",
    "/lib/firmware",
    "/usr/lib/firmware",
    "/lib/modules",
    "/usr/lib/modules",
    "/var/snap",
    "/home",
    "/boot",
    "/lost+found",
)


SHOWN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}")


@dataclass(frozen=True)
class ContentPlug:
    name: str
    content: str
    target: str
    provider: str


@dataclass(frozen=True)
class Layout:
    path: str
    kind: str  # "bind", "bind-file", "symlink" or "tmpfs"
    source: str = ""


@dataclass(frozen=True)
class App:
    name: str
    command: str
    command_chain: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    desktop: str = ""
    plugs: tuple[str, ...] = ()
    # For background services: how snapd would run them.
    daemon: str = ""
    install_mode: str = "enable"
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    # "system" (snapd runs it as root) or "user" (in each user's session).
    scope: str = "system"
    timer: str = ""
    # (name, listen-stream, socket-mode) of each socket that starts the service.
    sockets: tuple[tuple[str, str, int], ...] = ()
    # What MultiPak does not do for a service: activation by D-Bus, or a timer or a
    # socket it cannot translate.
    unsupported: tuple[str, ...] = ()
    # How snapd has systemd start, stop and restart it; timeouts in seconds.
    start_timeout: float | None = None
    stop_timeout: float | None = None
    restart_condition: str = "on-failure"
    restart_delay: float | None = None
    stop_mode: str = ""
    watchdog_timeout: float | None = None
    stop_command: str = ""
    post_stop_command: str = ""
    reload_command: str = ""
    # D-Bus names on the session bus that start a user service when asked for.
    bus_names: tuple[str, ...] = ()


# snapd's system usernames MultiPak provides, with the IDs snapd gives them
# (osutil/user.go). Others, such as snap_microk8s, are reserved for one snap.
SYSTEM_USERS = {"snap_daemon": 584788, "_daemon_": 584792}

# The kinds of services MultiPak runs, as the user's systemd services; dbus only for
# user services, on the session bus.
SUPPORTED_DAEMONS = ("simple", "oneshot", "forking", "notify", "dbus")
RESTART_CONDITIONS = (
    "never",
    "on-success",
    "on-failure",
    "on-abnormal",
    "on-abort",
    "on-watchdog",
    "always",
)
STOP_MODES = tuple(
    f"{signal}{every}"
    for signal in ("sigterm", "sighup", "sigusr1", "sigusr2", "sigint")
    for every in ("", "-all")
)
BUS_NAME = re.compile(r"[A-Za-z_-][A-Za-z0-9_-]*(\.[A-Za-z_-][A-Za-z0-9_-]*)+")
DURATION_PART = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|s|m|h)")
DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}


def duration(value, label):
    """A duration as snapd reads it (Go's, such as 30s or 1m30s), in seconds."""
    if value is None:
        return None
    text = str(value)
    position, total = 0, 0.0
    while position < len(text):
        match = DURATION_PART.match(text, position)
        if not match:
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
        total += float(match[1]) * DURATION_UNITS[match[2]]
        position = match.end()
    if not text or total <= 0 or total > 24 * 3600:
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
    return total


DAEMONS = ("simple", "forking", "oneshot", "notify", "dbus")
# Where a socket of each kind of service may be, as snapd allows.
SOCKET_DIRECTORIES = {
    "system": ("$SNAP_DATA", "$SNAP_COMMON"),
    "user": ("$SNAP_USER_DATA", "$SNAP_USER_COMMON", "$XDG_RUNTIME_DIR"),
}
SOCKET_PORT = re.compile(r"(?:(?:\[([0-9a-fA-F:.]+)\]|([0-9.]+)):)?([0-9]{1,5})")


def _socket_mode(value):
    """socket-mode as YAML 1.1 reads it, as snapd does: 0660 is octal, 438 decimal."""
    if value is None:
        return 0o666
    text = str(value)
    if re.fullmatch(r"0o?[0-7]{1,4}", text):
        mode = int(text.replace("o", ""), 8)
    elif text.isdigit():
        mode = int(text)
    else:
        raise ValueError(text)
    if not 0 <= mode <= 0o777:
        raise ValueError(text)
    return mode


def socket_address(listen, scope):
    """("unix", path with its variable) or ("tcp", host, port) for a listen-stream, or
    ValueError for what MultiPak does not provide: ports below 1024, abstract sockets
    and folders the service cannot write to."""
    if not isinstance(listen, str) or not listen:
        raise ValueError(listen)
    if listen.startswith("$"):
        variable, _slash, rest = listen.partition("/")
        if variable not in SOCKET_DIRECTORIES[scope] or not rest or ".." in rest.split("/"):
            raise ValueError(listen)
        return ("unix", listen)
    match = SOCKET_PORT.fullmatch(listen)
    if not match or not 1024 <= int(match[3]) <= 65535:
        raise ValueError(listen)
    return ("tcp", match[1] or match[2] or "", int(match[3]))


def reaches_network(address):
    """Whether other computers can connect to a socket_address: a port alone listens
    on every address (IPv4 too), and so does any address that is not loopback."""
    if address[0] != "tcp":
        return False
    try:
        return not ipaddress.ip_address(address[1]).is_loopback
    except ValueError:
        return True  # A port alone, or an address that is no loopback.


def service_sockets(info):
    """(service, listen-stream, socket_address) of each socket a service MultiPak runs
    listens on."""
    result = []
    for name in runnable_services(info):
        app = info.app(name)
        for _socket, listen, _mode in app.sockets:
            try:
                result.append((name, listen, socket_address(listen, app.scope)))
            except ValueError:
                continue
    return result


def network_warning(address):
    """The sentence for a tcp socket_address that other computers on the network reach."""
    if not address[1]:
        return tr(
            "This application accepts connections from other computers on the network (port {0})."
        ).format(address[2])
    return tr(
        "This application accepts connections from other computers on the network "
        "(address {0}, port {1})."
    ).format(address[1], address[2])


def network_ports(info):
    """One sentence for each port other computers on the network can connect to."""
    return list(
        dict.fromkeys(
            network_warning(address)
            for _name, _listen, address in service_sockets(info)
            if reaches_network(address)
        )
    )


@dataclass(frozen=True)
class Hook:
    """Settings snap.yaml gives a hook; the script itself is meta/hooks/<name>."""

    name: str
    command_chain: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Component:
    """An optional part declared in snap.yaml, published separately (snapd 2.67+)."""

    name: str
    kind: str = "standard"  # "kernel-modules" components extend a kernel: never here.
    summary: str = ""


# The hooks snapd runs when installing and removing an application, in order.
INSTALL_HOOKS = ("install", "default-configure", "configure")
REMOVE_HOOKS = ("remove",)


@dataclass(frozen=True)
class SnapInfo:
    name: str
    version: str
    summary: str
    base: str
    confinement: str
    kind: str
    architectures: tuple[str, ...]
    apps: tuple[App, ...]
    environment: tuple[tuple[str, str], ...] = ()
    content: tuple[ContentPlug, ...] = ()
    layouts: tuple[Layout, ...] = ()
    # Content slots, for providers: content id -> (source paths, uses "source" form).
    slots: tuple[tuple[str, tuple[str, ...], bool], ...] = ()
    services: tuple[str, ...] = ()
    system_plugs: tuple[str, ...] = ()
    # Content the snap uses without saying which snap provides it: nothing to download.
    unprovided: tuple[str, ...] = ()
    # Optional parts published separately (snapd 2.67 and later): not installed.
    components: tuple[str, ...] = ()
    component_details: tuple[Component, ...] = ()
    # System accounts the snap's services switch to, such as snap_daemon.
    system_usernames: tuple[str, ...] = ()
    # Names the snap's dbus slots own on the session bus.
    session_bus_names: tuple[str, ...] = ()
    hooks: tuple[Hook, ...] = ()
    raw_plugs: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def classic(self):
        return self.confinement == "classic"

    def app(self, name):
        return next((app for app in self.apps if app.name == name), None)

    def hook(self, name):
        """The hook's settings; a hook snap.yaml does not mention has none."""
        return next((hook for hook in self.hooks if hook.name == name), Hook(name))


def _text(value, label, *, required=False, limit=4096):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
    return value


def _mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
    return value


def _environment(value, label):
    result = []
    for key, item in _mapping(value, label).items():
        if not ENV_NAME.fullmatch(key) or not isinstance(item, (str, type(None))):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
        result.append((key, item or ""))
    return tuple(result)


def _plug_interface(name, value):
    if isinstance(value, dict):
        return value.get("interface") or name
    return name


def _relative(path, label):
    """A command or command-chain entry, relative to $SNAP and without traversal."""
    if not isinstance(path, str) or not COMMAND.fullmatch(path) or ".." in path.split("/"):
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format(label))
    return path.lstrip("/")


def _clean_absolute(path):
    return (
        isinstance(path, str)
        and path.startswith("/")
        and "\0" not in path
        and all(part not in ("", ".", "..") for part in path[1:].split("/"))
    )


def parse(text):
    try:
        data = loads(text)
    except YamlError as error:
        raise ValueError(tr("The snap metadata could not be read: {0}").format(error)) from error
    if not isinstance(data, dict):
        raise ValueError(tr("The snap metadata could not be read: {0}").format("not a mapping"))
    name = _text(data.get("name"), "name", required=True, limit=40)
    if not NAME.fullmatch(name) or not re.search("[a-z]", name):
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format("name"))
    version = _text(data.get("version"), "version", limit=32) or tr("Not provided")
    kind = _text(data.get("type"), "type", limit=16) or "app"
    if kind not in SNAP_TYPES:
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format("type"))
    confinement = _text(data.get("confinement"), "confinement", limit=16) or "strict"
    if confinement not in CONFINEMENTS:
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format("confinement"))
    base = _text(data.get("base"), "base", limit=40)
    if not base and kind == "app":
        base = "core"  # snapd's default for application snaps without a base.
    if base and base != "bare" and not NAME.fullmatch(base):
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format("base"))
    architectures = data.get("architectures") or ["all"]
    # Shown on screen when the snap does not fit this computer: plain names only.
    if not isinstance(architectures, list) or not all(
        isinstance(a, str) and ARCHITECTURE.fullmatch(a) for a in architectures
    ):
        raise ValueError(tr("The snap metadata has an invalid {0} field.").format("architectures"))

    raw_plugs = _mapping(data.get("plugs"), "plugs")
    content, system_plugs, unprovided = [], set(), []
    for plug, value in raw_plugs.items():
        interface = _plug_interface(plug, value)
        if interface in SYSTEM_INTERFACES:
            system_plugs.add(interface)
        if interface != "content" or not isinstance(value, dict):
            continue
        target = _text(value.get("target"), "target")
        provider = _text(value.get("default-provider"), "default-provider", limit=80)
        provider = provider.partition(":")[0]
        identifier = _text(value.get("content"), "content") or plug
        if not target:
            continue  # Nothing would be mounted.
        if not provider or not NAME.fullmatch(provider):
            # snapd would wait for the user to connect a provider; MultiPak cannot know one.
            # Shown on screen: only plain names, never markup from the package.
            shown = identifier if SHOWN_NAME.fullmatch(identifier) else plug
            if SHOWN_NAME.fullmatch(shown) and shown not in unprovided:
                unprovided.append(shown)
            continue
        content.append(ContentPlug(plug, identifier, target, provider))

    slots, session_names = [], {}
    for slot, value in _mapping(data.get("slots"), "slots").items():
        if (
            isinstance(value, dict)
            and value.get("interface") == "dbus"
            and value.get("bus") == "session"
            and isinstance(value.get("name"), str)
            and BUS_NAME.fullmatch(value["name"])
        ):
            session_names[str(slot)] = value["name"]
        if not isinstance(value, dict) or value.get("interface") != "content":
            continue
        source = value.get("source")
        uses_source = isinstance(source, dict)
        paths = (source or {}).get("read") if uses_source else value.get("read")
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            continue
        slots.append((_text(value.get("content"), "content") or slot, tuple(paths), uses_source))

    apps, services = [], []
    for app_name, value in _mapping(data.get("apps"), "apps").items():
        if not isinstance(app_name, str) or not APP_NAME.fullmatch(app_name):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("apps"))
        value = _mapping(value, "apps")
        if value.get("daemon"):
            services.append(app_name)
        plugs = value.get("plugs") or []
        if not isinstance(plugs, list) or not all(isinstance(p, str) for p in plugs):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("plugs"))
        for plug in plugs:
            interface = _plug_interface(plug, raw_plugs.get(plug))
            if interface in SYSTEM_INTERFACES:
                system_plugs.add(interface)
        command = _text(value.get("command"), "command", required=True)
        if not command or not COMMAND.fullmatch(command):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("command"))
        _relative(command.split(" ")[0], "command")
        chain = value.get("command-chain") or []
        if not isinstance(chain, list):
            raise ValueError(
                tr("The snap metadata has an invalid {0} field.").format("command-chain")
            )
        daemon = _text(value.get("daemon"), "daemon")
        if daemon and daemon not in DAEMONS:
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("daemon"))
        install_mode = _text(value.get("install-mode"), "install-mode") or "enable"
        if install_mode not in ("enable", "disable"):
            raise ValueError(
                tr("The snap metadata has an invalid {0} field.").format("install-mode")
            )
        order = {}
        for relation in ("before", "after"):
            names = value.get(relation) or []
            if not isinstance(names, list) or not all(
                isinstance(name, str) and APP_NAME.fullmatch(name) for name in names
            ):
                raise ValueError(tr("The snap metadata has an invalid {0} field.").format(relation))
            order[relation] = tuple(names)
        unsupported = []
        scope = value.get("daemon-scope") or "system"
        if scope not in ("system", "user"):
            raise ValueError(
                tr("The snap metadata has an invalid {0} field.").format("daemon-scope")
            )
        timer = value.get("timer") or ""
        sockets = []
        if daemon:
            from .snap_timer import calendar

            if daemon not in SUPPORTED_DAEMONS or (daemon == "dbus" and scope != "user"):
                # The system bus takes services only from the administrator.
                unsupported.append(daemon)
            bus_names = []
            bus_name = value.get("bus-name")
            if daemon == "dbus":
                if isinstance(bus_name, str) and BUS_NAME.fullmatch(bus_name):
                    bus_names.append(bus_name)
                elif "dbus" not in unsupported:
                    unsupported.append("dbus")
            activates_on = value.get("activates-on") or []
            if activates_on:
                if (
                    scope != "user"
                    or not isinstance(activates_on, list)
                    or any(str(slot) not in session_names for slot in activates_on)
                ):
                    unsupported.append("activates-on")
                else:
                    bus_names += [session_names[str(slot)] for slot in activates_on]
            if timer:
                try:
                    calendar(timer)
                except ValueError:
                    unsupported.append("timer")
            for socket_name, socket in _mapping(value.get("sockets"), "sockets").items():
                socket = _mapping(socket, "sockets")
                listen = socket.get("listen-stream")
                try:
                    socket_address(listen, scope)
                    mode = _socket_mode(socket.get("socket-mode"))
                except ValueError:
                    unsupported.append("sockets")
                    continue
                sockets.append((str(socket_name), listen, mode))
            if sockets and daemon == "forking":
                # The sockets are for the process that forks, not for the daemon.
                unsupported.append("sockets")
                sockets = []
        options = {
            "start_timeout": duration(value.get("start-timeout"), "start-timeout"),
            "stop_timeout": duration(value.get("stop-timeout"), "stop-timeout"),
            "restart_delay": duration(value.get("restart-delay"), "restart-delay"),
            "watchdog_timeout": duration(value.get("watchdog-timeout"), "watchdog-timeout"),
            "restart_condition": value.get("restart-condition") or "on-failure",
            "stop_mode": value.get("stop-mode") or "",
        }
        if options["restart_condition"] not in RESTART_CONDITIONS:
            raise ValueError(
                tr("The snap metadata has an invalid {0} field.").format("restart-condition")
            )
        if options["stop_mode"] and options["stop_mode"] not in STOP_MODES:
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("stop-mode"))
        for key in ("stop-command", "post-stop-command", "reload-command"):
            text = _text(value.get(key), key)
            if text and not COMMAND.fullmatch(text):
                raise ValueError(tr("The snap metadata has an invalid {0} field.").format(key))
            if text:
                _relative(text.split(" ")[0], key)
            options[key.replace("-", "_")] = text
        apps.append(
            App(
                app_name,
                command,
                tuple(_relative(item, "command-chain") for item in chain),
                _environment(value.get("environment"), "environment"),
                _text(value.get("desktop"), "desktop"),
                tuple(plugs),
                daemon,
                install_mode,
                order["before"],
                order["after"],
                scope,
                str(timer),
                tuple(sockets),
                tuple(dict.fromkeys(unsupported)),
                bus_names=tuple(bus_names) if daemon else (),
                **options,
            )
        )

    for app in apps:
        if any(name not in services for name in (*app.before, *app.after)):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("after"))

    hooks = []
    for hook_name, value in _mapping(data.get("hooks"), "hooks").items():
        if not isinstance(hook_name, str) or not APP_NAME.fullmatch(hook_name):
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("hooks"))
        value = _mapping(value, "hooks")
        chain = value.get("command-chain") or []
        if not isinstance(chain, list):
            raise ValueError(
                tr("The snap metadata has an invalid {0} field.").format("command-chain")
            )
        hooks.append(
            Hook(
                hook_name,
                tuple(_relative(item, "command-chain") for item in chain),
                _environment(value.get("environment"), "environment"),
            )
        )

    layouts = []
    for path, value in _mapping(data.get("layout"), "layout").items():
        value = _mapping(value, "layout")
        kinds = [key for key in ("bind", "bind-file", "symlink", "type") if key in value]
        if len(kinds) != 1:
            raise ValueError(tr("The snap metadata has an invalid {0} field.").format("layout"))
        layout_kind = kinds[0]
        source = _text(value.get(layout_kind), "layout")
        if layout_kind == "type":
            if source != "tmpfs":
                raise ValueError(tr("The snap metadata has an invalid {0} field.").format("layout"))
            layout_kind, source = "tmpfs", ""
        layouts.append(Layout(_text(path, "layout"), layout_kind, source))

    return SnapInfo(
        name=name,
        version=version,
        summary=_text(data.get("summary"), "summary", limit=512),
        base=base,
        confinement=confinement,
        kind=kind,
        architectures=tuple(architectures),
        apps=tuple(apps),
        environment=_environment(data.get("environment"), "environment"),
        content=tuple(content),
        layouts=tuple(layouts),
        slots=tuple(slots),
        services=tuple(services),
        system_plugs=tuple(sorted(system_plugs)),
        unprovided=tuple(unprovided),
        components=_shown_names(_mapping(data.get("components"), "components")),
        component_details=_components(_mapping(data.get("components"), "components")),
        # Every name, also those not fit to show: each one must be provided or refused.
        system_usernames=tuple(
            sorted(
                str(key)[:80] for key in _mapping(data.get("system-usernames"), "system-usernames")
            )
        ),
        session_bus_names=tuple(sorted(set(session_names.values()))),
        hooks=tuple(hooks),
        raw_plugs=raw_plugs,
    )


def _components(mapping):
    result = []
    for name in _shown_names(mapping):
        value = mapping.get(name)
        value = value if isinstance(value, dict) else {}
        kind = value.get("type") if isinstance(value.get("type"), str) else "standard"
        summary = value.get("summary") if isinstance(value.get("summary"), str) else ""
        # Shown on screen as plain text; long or odd summaries are cut.
        summary = "".join(c for c in summary[:200] if c.isprintable())
        result.append(Component(name, kind, summary))
    return tuple(result)


def _shown_names(mapping):
    """Names from the package that may appear on screen: plain names only."""
    return tuple(sorted(str(key) for key in mapping if SHOWN_NAME.fullmatch(str(key))))


def base_title(base):
    """The system a base snap provides, as people know it."""
    versions = {"core": "16.04", "core18": "18.04", "core20": "20.04", "core22": "22.04"}
    if base in versions:
        return "Ubuntu " + versions[base]
    match = re.fullmatch(r"core([2-9][0-9])", base or "")
    if match:
        return f"Ubuntu {match[1]}.04"
    if base == "bare":
        return tr("an empty base")
    return base


def expand(value, variables):
    """snapd's expansion: $NAME and ${NAME}; unknown names become empty."""
    return re.sub(
        r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
        lambda match: variables.get(match[1] or match[2], ""),
        value,
    )


def check_layouts(info, variables):
    """Layouts with expanded paths, refusing the ones snapd itself would refuse."""
    allowed = [variables["SNAP"], variables["SNAP_DATA"], variables["SNAP_COMMON"]]
    result, seen = [], []
    for layout in info.layouts:
        path = expand(layout.path, variables)
        if not _clean_absolute(path) or path == "/":
            raise ValueError(tr("The snap layout {0} is not valid.").format(layout.path))
        if any(path == item or path.startswith(item + "/") for item in LAYOUT_REJECTED):
            raise ValueError(tr("The snap layout {0} is not valid.").format(layout.path))
        if any(path.startswith(item + "/") for item in seen):
            raise ValueError(tr("The snap layout {0} is not valid.").format(layout.path))
        source = expand(layout.source, variables) if layout.source else ""
        if layout.kind != "tmpfs" and not (
            _clean_absolute(source)
            and any(source == item or source.startswith(item + "/") for item in allowed)
        ):
            raise ValueError(tr("The snap layout {0} is not valid.").format(layout.path))
        seen.append(path)
        result.append(Layout(path, layout.kind, source))
    return tuple(result)


# Where a strictly confined snap sees the computer's own files under snapd, and the
# paths a system-files plug may name there are shown from the computer. Elsewhere the
# snap sees its base or folders shown already, exactly as with snapd.
SYSTEM_FILE_HOST = ("/etc", "/var/log", "/usr/src", "/var/lib/extrausers")
# Folders the snap can already change under MultiPak, whatever it declares.
WRITABLE_FOLDERS = ("/run", "/mnt", "/media", "/var/tmp", "/tmp", "/home")
# snapd's ValidateNoAppArmorRegexp: characters with a meaning in AppArmor rules.
APPARMOR_RESERVED = set('?*[]{}^"\x00')


def _valid_system_file(path):
    """snapd's rules for system-files paths (interfaces/builtin/common_files.go):
    absolute and already clean, not ending in "/" or "@", without "~", $HOME or
    AppArmor's reserved characters."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        return False
    if path.endswith(("/", "@")) or "~" in path or "$HOME" in path:
        return False
    if APPARMOR_RESERVED & set(path) or not path.isprintable():
        return False
    return posixpath.normpath(path) == path and not path.startswith("//")


def system_files(info):
    """((path, wants writing), ...) of the snap's system-files plugs, and the paths
    snapd would refuse."""
    granted, invalid = [], []
    plugs = [
        (name, value)
        for name, value in info.raw_plugs.items()
        if isinstance(value, dict) and _plug_interface(name, value) == "system-files"
    ]
    if not plugs and "system-files" in interfaces(info):
        invalid.append("system-files")  # Used without saying which files.
    for name, value in plugs:
        # As snapd: "read" and "write" are lists of strings, and one of them is needed.
        present = [key for key in ("read", "write") if key in value]
        if not present:
            invalid.append(str(name)[:80])
        for key in present:
            paths = value[key]
            if not isinstance(paths, list):
                invalid.append(f"{name}.{key}"[:80])
                continue
            for path in paths:
                if not _valid_system_file(path):
                    invalid.append(str(path)[:80])
                else:
                    granted.append((path, key == "write"))
    return tuple(sorted(set(granted))), tuple(sorted(set(invalid)))


def within(path, folders):
    return any(path == folder or path.startswith(folder + "/") for folder in folders)


def read_only_requests(info):
    """System paths the snap asks to change that stay read-only under MultiPak."""
    granted, _invalid = system_files(info)
    return tuple(p for p, write in granted if write and not within(p, WRITABLE_FOLDERS))


def applications(info):
    """The snap's apps that are not background services: what MultiPak can open."""
    return tuple(app for app in info.apps if app.name not in info.services)


def runnable_services(info):
    """The snap's services MultiPak runs; the others are left stopped."""
    return tuple(name for name in info.services if not info.app(name).unsupported)


def idle_services(info):
    """The services MultiPak does not run, each with what it would need."""
    return tuple(
        f"{name} ({', '.join(info.app(name).unsupported)})"
        for name in info.services
        if info.app(name).unsupported
    )


def refusal(info, machine):
    """Why this snap cannot be installed by MultiPak, or an empty string."""
    if info.kind != "app":
        return tr("This snap is a system component ({0}), not an application.").format(info.kind)
    architecture = ARCHITECTURES.get(machine, machine)
    if "all" not in info.architectures and architecture not in info.architectures:
        return tr("This snap was built for {0} and cannot run on this computer ({1}).").format(
            ", ".join(info.architectures), architecture
        )
    if info.services and not applications(info) and not runnable_services(info):
        # Only services, none of which MultiPak can run: nothing would be left.
        return tr(
            "This snap only runs background services that MultiPak cannot run yet: {0}."
        ).format(", ".join(idle_services(info)))
    if info.system_plugs:
        return tr(
            "This snap needs system-level permissions ({0}) that MultiPak does not grant."
        ).format(", ".join(info.system_plugs))
    unknown = [name for name in info.system_usernames if name not in SYSTEM_USERS]
    if unknown:
        return tr(
            "This snap needs system user accounts ({0}) that MultiPak does not create."
        ).format(", ".join(unknown))
    invalid = system_files(info)[1]
    if invalid:
        return tr("This snap names system files that snapd itself would not accept: {0}").format(
            ", ".join(invalid[:5])
        )
    if not info.apps:
        return tr("This snap does not contain an application to open.")
    return ""


def limitations(info, installed=(), services=()):
    """What MultiPak cannot provide for an installable snap, one message each.

    installed: the optional components that are, or will be, installed; services: the
    features whose service this computer lacks (snap_services.missing_features)."""
    result = []
    absent = [name for name in info.components if name not in installed]
    if absent:
        result.append(
            tr(
                "These optional components are not installed: {0}. "
                "Features that depend on them may not work."
            ).format(", ".join(absent))
        )
    if runnable_services(info):
        # snapd runs them as system services, as root; MultiPak as the user's.
        result.append(
            tr(
                "These background services run with your user account, not as the "
                "administrator: {0}. They cannot use network ports below 1024."
            ).format(", ".join(runnable_services(info)))
        )
    result.extend(network_ports(info))
    if idle_services(info):
        result.append(
            tr(
                "These background services of the snap will not run: {0}. "
                "Features that depend on them may not work."
            ).format(", ".join(idle_services(info)))
        )
    if services:
        result.append(
            tr(
                "These features may not work, because this system does not have the "
                "service they use: {0}."
            ).format(", ".join(services))
        )
    read_only = read_only_requests(info)
    if read_only:
        result.append(
            tr(
                "The snap asks to change these system files, which MultiPak shows "
                "read-only: {0}. Features that write to them may not work."
            ).format(", ".join(read_only[:5]))
        )
    if info.unprovided:
        result.append(
            tr(
                "These dependencies could not be found automatically: {0}. "
                "The application may not work completely."
            ).format(", ".join(info.unprovided))
        )
    return result


def limitation_sections(info, installed=(), services=()):
    """The same warnings as limitations, short and grouped for the review page.

    Each one is a dict: title, text (one short sentence, may be empty), items (names
    shown in a list) and note (a small line under the list, may be empty)."""
    result = []

    def add(title, items=(), text="", note=""):
        result.append({"title": title, "text": text, "items": list(items), "note": note})

    absent = [name for name in info.components if name not in installed]
    if absent:
        add(
            tr("Optional components not installed"),
            absent,
            note=tr("Features that depend on them may not work."),
        )
    if runnable_services(info):
        add(
            tr("Background services"),
            runnable_services(info),
            note=tr("They run with your user account. Ports below 1024 are unavailable."),
        )
    if idle_services(info):
        add(
            tr("Background services that will not run"),
            idle_services(info),
            note=tr("Features that depend on them may not work."),
        )
    if services:
        add(
            tr("Services missing on this system"),
            services,
            note=tr("These features may not work."),
        )
    read_only = read_only_requests(info)
    if read_only:
        add(
            tr("Read-only system files"),
            read_only[:5],
            note=tr("The snap asks to change them. Features that write to them may not work."),
        )
    if info.unprovided:
        add(
            tr("Dependencies not found"),
            info.unprovided,
            note=tr("The application may not work completely."),
        )
    return result


def interfaces(info):
    """Interface names the snap's plugs use, including plugs named after their interface."""
    names = {_plug_interface(name, value) for name, value in info.raw_plugs.items()}
    for app in info.apps:
        for plug in app.plugs:
            names.add(_plug_interface(plug, info.raw_plugs.get(plug)))
    return frozenset(name for name in names if isinstance(name, str))


def plug_attributes(info, interface):
    """The attributes of each plug that uses interface."""
    return [
        value
        for name, value in info.raw_plugs.items()
        if isinstance(value, dict) and _plug_interface(name, value) == interface
    ]


def personal_directories(info, home):
    """Directories in home a personal-files plug may write to, which snapd creates."""
    result = []
    for value in plug_attributes(info, "personal-files"):
        writes = value.get("write") or []
        for path in writes if isinstance(writes, list) else []:
            if not isinstance(path, str) or not path.startswith("$HOME/"):
                continue
            relative = path[len("$HOME/") :]
            parts = [p for p in relative.split("/") if p]
            if not parts or any(p in (".", "..") for p in parts):
                continue
            # The parent of each writable path, as snapd's ensure-dir rule does.
            if len(parts) > 1:
                result.append(posixpath.join(home, *parts[:-1]))
    return sorted(set(result))

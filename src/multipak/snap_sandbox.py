"""Build the bubblewrap command that gives a snap the view snapd would give it.

The snap sees its base (or the host, if classic) as the root filesystem, plus the host
directories snapd shares. Nothing runs as root and no host file is changed."""

import os
import posixpath
from dataclasses import dataclass, field

from .snap_ctl import CHANNEL, TOOLS

# snap-confine's normal-mode bind list, without snapd's own state directories.
HOST_DIRECTORIES = ("/etc", "/home", "/run", "/var/tmp", "/mnt", "/media")
# Strictly confined snaps get a composed /etc, as Flatpak does: the base's, so that its
# programs find the configuration they were built with, with these entries from the
# computer, which describe the computer itself and must stay current. snapd instead
# shows the host's /etc and puts a few of the base's entries back (alternatives,
# nsswitch.conf, AppArmor, certificates and, from core26, the library cache); all of
# those stay the base's here.
ETC_FROM_HOST = (
    # Users, groups and this machine.
    "/etc/passwd",
    "/etc/group",
    "/etc/hostname",
    "/etc/machine-id",
    # Network names.
    "/etc/hosts",
    "/etc/host.conf",
    "/etc/resolv.conf",
    "/etc/gai.conf",
    # Time and language.
    "/etc/localtime",
    "/etc/timezone",
    "/etc/adjtime",
    "/etc/locale.conf",
    "/etc/default/locale",
    # The desktop: fonts, printers, system-wide settings and graphics drivers.
    "/etc/fonts",
    "/etc/cups",
    "/etc/papersize",
    "/etc/xdg",
    "/etc/dconf",
    "/etc/gtk-2.0",
    "/etc/gtk-3.0",
    "/etc/gtk-4.0",
    "/etc/vulkan",
    "/etc/OpenCL",
    "/etc/glvnd",
    "/etc/egl",
    "/etc/nvidia",
)
# On Debian and Ubuntu, snapd keeps the host's certificates, for custom authorities.
ETC_FROM_DEBIAN_HOST = ("/etc/ssl",)
# Interfaces that bring host directories into a strict snap, as snapd's mount rules do.
FONT_DIRECTORIES = ("/usr/share/fonts", "/usr/local/share/fonts")
FONT_CACHES = ("/var/cache/fontconfig",)
APPSTREAM_DIRECTORIES = (
    "/usr/share/metainfo",
    "/usr/share/appdata",
    "/usr/share/app-info",
    "/usr/share/swcatalog",
    "/var/cache/app-info",
    "/var/cache/swcatalog",
    "/var/lib/app-info",
    "/var/lib/swcatalog",
    "/var/lib/apt/lists",
)
DOCUMENTATION_DIRECTORIES = (
    "/usr/share/doc",
    "/usr/local/share/doc",
    "/usr/share/cups/doc-root",
    "/usr/share/gimp/2.0/help",
    "/usr/share/gtk-doc",
    "/usr/share/javascript",
    "/usr/share/libreoffice/help",
    "/usr/share/sphinx_rtd_theme",
    "/usr/share/xubuntu-docs",
    "/usr/share/man",
    "/usr/share/help",
    "/usr/share/info",
)
INTERFACE_DIRECTORIES = {
    "desktop": FONT_DIRECTORIES,
    "opengl": ("/usr/share/nvidia", "/usr/lib/wsl"),
    "appstream-metadata": APPSTREAM_DIRECTORIES,
    "system-packages-doc": DOCUMENTATION_DIRECTORIES,
    "system-observe": ("/boot",),
}
HOST_OPTIONAL = ("/lib/modules", "/usr/lib/modules", "/lib/firmware", "/usr/lib/firmware")
NVIDIA_GLOBS = (
    "libnvidia-*.so*",
    "libnvoptix.so*",
    "libEGL_nvidia.so*",
    "libGLESv1_CM_nvidia.so*",
    "libGLESv2_nvidia.so*",
    "libGLX_nvidia.so*",
    "libXvMCNVIDIA*.so*",
    "libcuda.so*",
    "libnvcuvid.so*",
    "libEGL.so*",
    "libGL.so*",
    "libOpenGL.so*",
    "libGLESv1_CM.so*",
    "libGLESv2.so*",
    "libGLX.so*",
    "libGLX_indirect.so*",
    "libGLdispatch.so*",
    "vdpau/libvdpau_nvidia.so*",
)
NVIDIA_DIRECTORIES = {
    "gl": ("/usr/lib64", "/usr/lib64/nvidia", "/usr/lib/x86_64-linux-gnu"),
    "gl32": ("/usr/lib", "/usr/lib/nvidia", "/usr/lib/i386-linux-gnu"),
}
LIBRARY_PATH = "/var/lib/snapd/lib/gl:/var/lib/snapd/lib/gl32"


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str  # "d" directory, "f" file, "l" symlink, "o" other
    target: str = ""


@dataclass
class Node:
    op: tuple = ()
    children: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Mount:
    """One snap made visible at /snap/<name>/<revision>."""

    name: str
    revision: str
    path: str  # Its extracted files.


def local_entries(path):
    try:
        with os.scandir(path) as items:
            result = []
            for item in items:
                if item.is_symlink():
                    result.append(Entry(item.name, "l", os.readlink(item.path)))
                elif item.is_dir(follow_symlinks=False):
                    result.append(Entry(item.name, "d"))
                elif item.is_file(follow_symlinks=False):
                    result.append(Entry(item.name, "f"))
                else:
                    result.append(Entry(item.name, "o"))
            return sorted(result, key=lambda entry: entry.name)
    except OSError:
        return None


class Plan:
    def __init__(self, host_entries):
        # host_entries(path) -> [Entry] or None: listings of host directories.
        self.root = Node(("tmpfs",))
        self.host_entries = host_entries
        self.host_requests = set()

    def _node(self, path, create=True):
        node = self.root
        for part in [p for p in path.split("/") if p]:
            if part not in node.children:
                if not create:
                    return None
                node.children[part] = Node()
            node = node.children[part]
        return node

    def set(self, path, *op):
        node = self._node(path)
        node.op = op
        # A new mount hides whatever was planned below it.
        node.children = {}

    def _source_entries(self, op, sub):
        """Entries of the directory sub inside a bind source, or None."""
        kind, source = op[0], op[1]
        path = posixpath.join(source, sub) if sub else source
        if kind == "local":
            return local_entries(path)
        self.host_requests.add(path)
        return self.host_entries(path)

    def _lookup(self, path):
        """(nearest planned ancestor path, its op, relative rest) for path."""
        node, current, best = self.root, "/", ("/", self.root.op, path.strip("/"))
        parts = [p for p in path.split("/") if p]
        for index, part in enumerate(parts):
            node = node.children.get(part)
            if node is None:
                break
            current = "/" + "/".join(parts[: index + 1])
            if node.op:
                best = (current, node.op, "/".join(parts[index + 1 :]))
        return best

    def _follow(self, path):
        """path with the first planned symlink among its parents followed, or None."""
        parts = [p for p in path.split("/") if p]
        node = self.root
        for index, part in enumerate(parts[:-1]):
            node = node.children.get(part)
            if node is None:
                return None
            if node.op and node.op[0] == "symlink":
                parent = "/" + "/".join(parts[:index])
                return posixpath.normpath(posixpath.join(parent, node.op[1], *parts[index + 1 :]))
        return None

    def resolve(self, path):
        """Where path really is in the bubble: bases such as core24 make /lib a link to
        usr/lib, so a mount at /lib/modules belongs at /usr/lib/modules, as with snapd."""
        for _ in range(40):
            followed = self._follow(path)
            if followed is None:
                return path
            path = followed
        raise ValueError(f"too many symbolic links in {path}")

    def mount(self, path, *op):
        """Plan a mount, first making sure its mount point can exist."""
        for _ in range(40):
            path = self.resolve(path)
            self.ensure(path)
            # Preparing the mount point can reveal links in a copied directory.
            if self._follow(path) is None:
                break
        self.set(path, *op)

    def ensure(self, path):
        """Make sure path exists, or can be created, where it will be mounted."""
        if posixpath.dirname(path) == "/":
            return  # The root is a writable tmpfs.
        ancestor, op, rest = self._lookup(path)
        if ancestor == path or not op or op[0] in ("tmpfs", "dir"):
            return  # Planned already, or inside a directory created for the bubble.
        if op[0] not in ("local", "host"):
            raise ValueError(f"cannot create a mount point below {ancestor}")
        parts = [p for p in rest.split("/") if p]
        existing = ""
        for index, part in enumerate(parts):
            entries = self._source_entries(op, existing)
            if entries is None:
                break
            match = next((e for e in entries if e.name == part), None)
            last = index == len(parts) - 1
            if match is None or (match.kind != "d" and not (last and match.kind == "f")):
                break
            existing = posixpath.join(existing, part)
        else:
            return  # The mount point already exists in the bound directory.
        self.mimic(posixpath.join(ancestor, existing) if existing else ancestor, op, existing)

    def replace(self, path, *op):
        """Plan a symlink or file where the parent must be writable."""
        parent = posixpath.dirname(path)
        if parent != "/":
            self.ensure(parent)
            ancestor, parent_op, rest = self._lookup(parent)
            if parent_op and parent_op[0] in ("local", "host"):
                self.mimic(parent, parent_op, rest)
        self.set(path, *op)

    def mimic(self, path, op, sub):
        """Replace a read-only directory by a writable copy of its entries."""
        entries = self._source_entries(op, sub)
        if entries is None:
            raise ValueError(f"cannot read {path} to prepare it")
        node = self._node(path)
        planned = node.children
        node.op, node.children = ("tmpfs",), {}
        source = posixpath.join(op[1], sub) if sub else op[1]
        for entry in entries:
            child = posixpath.join(path, entry.name)
            if entry.kind == "l":
                self.set(child, "symlink", entry.target)
            elif entry.kind in ("d", "f"):
                self.set(child, op[0], posixpath.join(source, entry.name), *op[2:])
            # Device nodes, sockets and pipes have no place in a snap or base.
        for name, child in planned.items():
            if name in node.children:
                _merge(node.children[name], child)
            else:
                node.children[name] = child

    def arguments(self):
        result = []

        def walk(path, node):
            op = node.op
            if path != "/" and op:
                result.extend(_bwrap(path, op))
            elif path != "/" and node.children:
                # A folder only there to hold mounts, such as /snap/<name>: made
                # explicitly, 0755 as with snapd. Left to bubblewrap, it would be
                # 0700, closed to the snap's system users (snap_daemon).
                result.extend(["--dir", path])
            for name in sorted(node.children):
                walk(posixpath.join(path, name), node.children[name])

        result.extend(["--tmpfs", "/"])
        walk("/", self.root)
        return result


def _merge(target, planned):
    """Keep mounts planned below a directory that has just been recreated."""
    if planned.op:
        target.op, target.children = planned.op, planned.children
        return
    for name, child in planned.children.items():
        if name in target.children:
            _merge(target.children[name], child)
        else:
            target.children[name] = child


def _bwrap(path, op):
    kind = op[0]
    if kind == "tmpfs":
        return ["--tmpfs", path]
    if kind == "dir":
        return ["--dir", path]
    if kind == "symlink":
        return ["--symlink", op[1], path]
    if kind == "proc":
        return ["--proc", path]
    if kind == "dev":
        return ["--dev-bind", op[1], path]
    if kind in ("local", "host"):
        writable = len(op) > 2 and op[2] == "rw"
        return ["--bind" if writable else "--ro-bind", op[1], path]
    raise ValueError(f"unknown mount {kind}")


def etc_from_host(debian_like):
    return (*ETC_FROM_HOST, *(ETC_FROM_DEBIAN_HOST if debian_like else ()))


def _host_entry(host_entries, path):
    parent, name = posixpath.split(path)
    return next((e for e in host_entries(parent) or () if e.name == name), None)


def _from_host(plan, host_entries, path):
    """Show a host file or directory at the same place, read-only."""
    entry = _host_entry(host_entries, path)
    if entry is None:
        return
    if entry.kind == "l":
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), entry.target))
        if target.startswith("/run/"):
            # Like resolv.conf on systemd: the link keeps following the live file.
            plan.replace(path, "symlink", entry.target)
            return
    if entry.kind in ("d", "f", "l"):
        # A link elsewhere is followed: the bound file is what it points to.
        plan.mount(path, "host", path)


def _compose_etc(plan, host_entries, host_exists):
    for path in etc_from_host(host_exists("/etc/debian_version")):
        if host_exists(path):
            _from_host(plan, host_entries, path)


def _system_file(plan, host_entries, path):
    """Show a path a system-files plug names as the computer has it, read-only.

    The computer's links are never followed: a link is shown as the link, so it
    resolves inside the snap's view, as it does with snapd, and cannot bring in a
    place the snap was not given. Links of the base do not move the path either."""
    if plan._lookup(path)[1][:1] == ("host",):
        return  # Shown from the computer already, links included.
    parts = path.strip("/").split("/")
    for index in range(1, len(parts)):
        current = "/" + "/".join(parts[: index + 1])
        entry = _host_entry(host_entries, current)
        if entry is None or entry.kind not in ("d", "f", "l"):
            return  # Missing on the computer, as it would be with snapd.
        if entry.kind == "l":
            plan.replace(current, "symlink", entry.target)
            return
        if index < len(parts) - 1 and entry.kind != "d":
            return
    try:
        plan.ensure(posixpath.dirname(path))
        for index in range(1, len(parts)):
            node = plan._node("/" + "/".join(parts[:index]), create=False)
            if node is not None and node.op[:1] == ("symlink",):
                return  # A link of the base in the way: never followed for this.
        # The mount point itself: when the base lacks it, its folder is recreated
        # with the original entries so that it can be made (a read-only one cannot).
        plan.ensure(path)
    except ValueError:
        return  # No place for it in the snap's view: not shown, as when missing.
    plan.set(path, "host", path)


def _interface_mounts(plan, request, host_entries, host_exists):
    interfaces = request.interfaces
    for interface, paths in INTERFACE_DIRECTORIES.items():
        if interface not in interfaces:
            continue
        for path in paths:
            if host_exists(path):
                _from_host(plan, host_entries, path)
    if "desktop" in interfaces and request.font_cache:
        for path in FONT_CACHES:
            if host_exists(path):
                _from_host(plan, host_entries, path)
    if request.private_shm:
        # snapd gives the snap its own /dev/shm; here one per run.
        plan.set("/dev/shm", "tmpfs")


@dataclass(frozen=True)
class Request:
    """Everything needed to start one app of an installed snap."""

    snap: Mount
    base: Mount | None
    classic: bool
    providers: tuple[Mount, ...]
    # (namespace path, local source directory) for each connected content plug.
    content: tuple[tuple[str, str], ...]
    layouts: tuple  # snap_meta.Layout with expanded paths
    data: str  # Local directory shown as /var/snap/<name>/<revision>.
    common: str  # Local directory shown as /var/snap/<name>/common.
    uid: int
    home: str
    environment: tuple[tuple[str, str], ...]
    argv: tuple[str, ...]
    workdir: str
    # MultiPak's snapctl and the directory it sends requests through.
    tools: str = ""
    channel: str = ""
    # Hooks run as root, as with snapd, inside a user namespace: on the system they
    # remain the user. Their home is root_home, shown as /root for strict snaps.
    as_root: bool = False
    root_home: str = ""
    # Interfaces the snap declares: under MultiPak they are all connected.
    interfaces: frozenset = frozenset()
    # Local directory shown as /snap/<name>/components, when components are installed.
    components: str = ""
    # The host's font caches suit only some systems: snapd skips Fedora's and Arch's.
    font_cache: bool = False
    private_shm: bool = False
    # Paths of system-files plugs to show from the computer (snap_meta.system_files).
    system_files: tuple[str, ...] = ()
    # (path, local file): the computer's users and groups with the snap's system
    # users added, shown in place of /etc/passwd and /etc/group.
    accounts: tuple[tuple[str, str], ...] = ()
    # Local directory shown as /tmp: the snap's own, shared by its apps, services
    # and hooks, as snapd does. Without it, a /tmp of this run alone.
    tmp: str = ""


def build(request, host_entries, host_exists):
    """The bubblewrap arguments for request.

    host_entries(path) lists a host directory (None when unreadable) and host_exists(path)
    tells whether a host path exists. Both come from one probe of the host.
    """
    plan = Plan(host_entries)
    if request.classic:
        for entry in host_entries("/") or ():
            path = "/" + entry.name
            if entry.name in ("proc", "dev", "snap"):
                continue
            if entry.kind == "l":
                plan.set(path, "symlink", entry.target)
            elif entry.kind == "d":
                plan.set(path, "host", path, "rw")
        plan.set("/dev", "dev", "/dev")
        plan.set("/proc", "proc")
    else:
        if request.base is not None:
            for entry in local_entries(request.base.path) or ():
                path = "/" + entry.name
                if entry.kind == "l":
                    plan.set(path, "symlink", entry.target)
                elif entry.kind == "d":
                    plan.set(path, "local", posixpath.join(request.base.path, entry.name))
        plan.set("/dev", "dev", "/dev")
        plan.set("/proc", "proc")
        plan.set("/sys", "host", "/sys")
        base_etc = request.base is not None and os.path.isdir(
            posixpath.join(request.base.path, "etc")
        )
        for path in HOST_DIRECTORIES:
            if path == "/etc" and base_etc:
                continue  # Composed below.
            if host_exists(path):
                plan.mount(path, "host", path, "ro" if path == "/etc" else "rw")
        if base_etc:
            _compose_etc(plan, host_entries, host_exists)
        _interface_mounts(plan, request, host_entries, host_exists)
        for path in request.system_files:
            _system_file(plan, host_entries, path)
        # Atomic systems keep homes in /var/home behind a /home symlink.
        real_home = posixpath.dirname(request.home.rstrip("/"))
        if real_home not in ("/home", "") and host_exists(real_home):
            plan.mount(real_home, "host", real_home, "rw")
        for path in HOST_OPTIONAL:
            if host_exists(path):
                plan.mount(path, "host", path)
        if request.tmp:
            plan.set("/tmp", "local", request.tmp, "rw")
        else:
            plan.set("/tmp", "tmpfs")
        if host_exists("/tmp/.X11-unix"):
            plan.set("/tmp/.X11-unix", "host", "/tmp/.X11-unix")
        plan.mount("/var/lib/snapd", "tmpfs")
        plan.set("/var/lib/snapd/hostfs", "host", "/")
        for directory, sources in _nvidia(host_entries, host_exists).items():
            for name, source in sources:
                plan.set(f"/var/lib/snapd/lib/{directory}/{name}", "host", source)
        name = request.snap.name
        plan.mount("/var/snap", "tmpfs")
        plan.set(f"/var/snap/{name}/{request.snap.revision}", "local", request.data, "rw")
        plan.set(f"/var/snap/{name}/common", "local", request.common, "rw")
        plan.set(f"/var/snap/{name}/current", "symlink", request.snap.revision)
        if not request.as_root and host_exists(f"/run/user/{request.uid}"):
            plan.set(f"/run/user/{request.uid}/snap.{name}", "dir")
        if request.as_root and request.root_home:
            plan.mount("/root/snap", "local", request.root_home, "rw")
        if request.tools:
            # Where snapd puts its own tools, snapctl among them.
            plan.mount("/usr/lib/snapd", "local", request.tools)
    plan.set("/snap", "tmpfs")
    if request.tools:
        plan.set(TOOLS, "local", request.tools)
    if request.channel:
        plan.set(CHANNEL, "local", request.channel, "rw")
    if request.components:
        plan.set(f"/snap/{request.snap.name}/components", "local", request.components)
    for mount in (request.snap, request.base, *request.providers):
        if mount is None:
            continue
        plan.set(f"/snap/{mount.name}/{mount.revision}", "local", mount.path)
        plan.set(f"/snap/{mount.name}/current", "symlink", mount.revision)
    # Deeper paths last, so their parents are ready when they are mounted.
    for path, source in sorted(request.content, key=lambda item: item[0].count("/")):
        plan.mount(path, "local", source)
    for layout in sorted(request.layouts, key=lambda item: item.path.count("/")):
        if layout.kind == "symlink":
            plan.replace(layout.path, "symlink", layout.source)
        elif layout.kind == "tmpfs":
            plan.mount(layout.path, "tmpfs")
        else:
            writable = not layout.source.startswith(f"/snap/{request.snap.name}/")
            source = _local_source(request, layout.source)
            if not os.path.lexists(source):
                continue  # Nothing to show: the path keeps what the base has there.
            plan.mount(layout.path, "local", source, "rw" if writable else "ro")
    for path, local in request.accounts:
        plan.set(path, "local", local)
    identity = ["--unshare-user", "--uid", "0", "--gid", "0"] if request.as_root else []
    arguments = [*identity, "--die-with-parent", *plan.arguments(), "--chdir", request.workdir]
    for key, value in request.environment:
        arguments.extend(["--setenv", key, value])
    return arguments, plan.host_requests


def _inside(path, directory):
    real, root = os.path.realpath(path), os.path.realpath(directory)
    return real == root or real.startswith(root.rstrip("/") + "/")


def _local_source(request, path):
    """The local file behind a path in /snap or /var/snap for a layout.

    A path inside a content plug's target is the provider's file: with snapd, layouts
    are applied after content is mounted, so they see it (such as a GPU snap's libdrm
    at $SNAP/gpu-2404/libdrm)."""
    current = f"/snap/{request.snap.name}/current"
    if path == current or path.startswith(current + "/"):
        path = f"/snap/{request.snap.name}/{request.snap.revision}" + path[len(current) :]
    for target, local in sorted(request.content, key=lambda item: -len(item[0])):
        if path == target or path.startswith(target + "/"):
            resolved = local + path[len(target) :]
            if not _inside(resolved, local):
                raise ValueError("a layout leaves its content")
            return resolved
    prefixes = [
        (f"/snap/{request.snap.name}/{request.snap.revision}", request.snap.path),
        (f"/snap/{request.snap.name}/current", request.snap.path),
        (f"/var/snap/{request.snap.name}/{request.snap.revision}", request.data),
        (f"/var/snap/{request.snap.name}/current", request.data),
        (f"/var/snap/{request.snap.name}/common", request.common),
    ]
    for prefix, local in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            resolved = local + path[len(prefix) :]
            if not _inside(resolved, local):
                raise ValueError("a layout leaves its snap")
            return resolved
    raise ValueError("a layout source is outside its snap")


def _nvidia(host_entries, host_exists):
    """Host NVIDIA driver files, as snapd exposes them in /var/lib/snapd/lib/gl."""
    import fnmatch

    if not host_exists("/sys/module/nvidia"):
        return {}
    result = {}
    for directory, sources in NVIDIA_DIRECTORIES.items():
        found = {}
        for source in sources:
            for entry in host_entries(source) or ():
                if entry.kind in ("f", "l") and any(
                    fnmatch.fnmatch(entry.name, pattern) for pattern in NVIDIA_GLOBS
                ):
                    found[entry.name] = posixpath.join(source, entry.name)
        result[directory] = sorted(found.items())
    return result

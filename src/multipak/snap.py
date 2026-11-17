"""Install and run snaps with bubblewrap, without snapd.

A snap's files, its base and the content snaps it needs are extracted once into
MultiPak's data directory. Launching an app builds, with bubblewrap, the filesystem
view snapd would give it and runs the command declared in meta/snap.yaml. Bases and
content snaps are shared between installed snaps.
"""

import fcntl
import hashlib
import io
import json
import os
import platform
import posixpath
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from multipak.localization import tr

from . import (
    host,
    snap_application,
    snap_components,
    snap_ctl,
    snap_dialog,
    snap_open,
    snap_services,
)
from .desktop import parse_desktop, quote_exec
from .models import Package, Transaction
from .process import CommandError
from .snap_daemons import Services, runner_call
from .snap_meta import (
    INSTALL_HOOKS,
    NAME,
    REMOVE_HOOKS,
    REVISION,
    SYSTEM_FILE_HOST,
    SYSTEM_USERS,
    check_layouts,
    expand,
    limitations,
    parse,
    refusal,
    runnable_services,
    system_files,
    within,
)
from .snap_sandbox import LIBRARY_PATH, Entry, Mount, Request, build
from .storage import record_install_failure, require_space
from .store import remove_tree

MAX_METADATA = 1024 * 1024
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
MAX_ICON = 4 * 1024 * 1024
LOCAL_REVISION = "x1"
SNAP_PATH = (
    "{snap}/usr/sbin:{snap}/usr/bin:{snap}/sbin:{snap}/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games"
)
CLASSIC_PATH = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games"
)


def _missing(name):
    return tr("A component this snap needs ({0}) is missing. Install the snap again.").format(name)


class NotASnap(ValueError):
    pass


# Reading snap files


def is_snap(path):
    with Path(path).open("rb") as stream:
        return stream.read(4) == b"hsqs"


def _unsquashfs():
    tool = shutil.which("unsquashfs")
    if not tool:
        raise RuntimeError(tr("Could not find unsquashfs to read the snap."))
    return tool


def read_member(runner, path, name, limit=MAX_METADATA):
    return runner.read_bytes([_unsquashfs(), "-cat", str(path), name], limit=limit)


# Base snaps ship device nodes such as /dev/null. Only root can create those, and snaps
# never use them: the bubble shows the computer's own /dev. They are found in the
# listing first and left out of the extraction, so unsquashfs reports no error at all
# and any error it does report stops the installation. Special files anywhere else are
# not expected in a snap and refuse it.
SPECIAL_KINDS = "cbps"  # Character and block devices, named pipes and sockets.
_DEVICE_PATH = re.compile(r"dev(?:/[A-Za-z0-9._+-]+)+")
MAX_LISTING = 256 * 1024 * 1024


def listing(runner, path):
    """(special files, bytes once extracted) of a snap: the paths, relative to its
    root, of its device nodes, pipes and sockets, and the size of everything else."""
    output = runner.run(
        [_unsquashfs(), "-lln", str(path)],
        timeout=600,
        binary=True,
        output_limit=MAX_LISTING,
    ).decode("utf-8", "surrogateescape")
    special, size = [], 0
    for line in output.splitlines():
        if not line:
            continue
        if line[0] == "-":
            fields = line.split(None, 3)
            if len(fields) < 3 or not fields[2].isdigit():
                raise ValueError(tr("The snap's file list could not be read."))
            size += int(fields[2])
        # Folders and links take a block each.
        size += 4096
        if line[0] not in SPECIAL_KINDS:
            continue
        _before, separator, name = line.partition(" squashfs-root/")
        if not separator or not name:
            raise ValueError(tr("The snap's file list could not be read."))
        special.append(name)
    return special, size


def special_files(runner, path):
    """Paths, relative to the snap's root, of its device nodes, pipes and sockets."""
    return listing(runner, path)[0]


def extract(runner, path, destination, marker="meta/snap.yaml"):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError(tr("The extraction directory already exists."))
    special, size = listing(runner, path)
    unexpected = [name for name in special if not _DEVICE_PATH.fullmatch(name)]
    if unexpected:
        raise ValueError(
            tr(
                "The snap contains special files outside /dev, which MultiPak cannot create: {0}"
            ).format(", ".join(unexpected[:5]))
        )
    # Before writing anything: a large base that fills the disk halfway would leave
    # an unclear failure behind.
    require_space(destination.parent, size)
    command = [
        _unsquashfs(),
        "-d",
        str(destination),
        "-no-xattrs",
        "-no-progress",
        "-processors",
        "2",
        "-mem",
        "64M",
    ]
    with tempfile.TemporaryDirectory(prefix="multipak-extract-") as scratch:
        if special:
            excluded = Path(scratch) / "excluded"
            excluded.write_text("".join(name + "\n" for name in special), encoding="utf-8")
            # The names come from the listing of this same image and hold no
            # wildcards (_DEVICE_PATH), so each leaves out exactly the file it names
            # and nothing else. -match makes newer unsquashfs also stop when a name
            # to leave out matches nothing; 4.7.5, in the Flatpak, checks only names
            # to extract. A device missing from the list fails the extraction anyway.
            command.extend(["-exclude-file", str(excluded), "-match"])
        runner.run([*command, str(path)], timeout=1800)
    if not regular_file(destination, marker):
        raise ValueError(tr("The snap does not contain {0}.").format(marker))


def _without_links(root, path):
    """Whether path is inside root with no link on the way, the parts that exist."""
    root, path = Path(root), Path(path)
    if not path.is_relative_to(root) or root.is_symlink():
        return False
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return False
        if not current.exists():
            break
    return True


def regular_file(root, relative):
    """Whether root/relative is a file of its own: no link on the way, inside or out."""
    path = Path(root, relative)
    real = os.path.realpath(path)
    return real == os.path.join(os.path.realpath(root), relative) and path.is_file()


def read_metadata(root, relative, limit=MAX_METADATA):
    """Text of a metadata file of an extracted snap, read only when it is a file of
    the snap's own and not too large."""
    path = Path(root, relative)
    if not regular_file(root, relative):
        raise ValueError(tr("The snap does not contain {0}.").format(relative))
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(tr("The snap metadata is too large."))
    return data.decode("utf-8")


def read_info(runner, path):
    try:
        raw = read_member(runner, path, "meta/snap.yaml")
    except CommandError as error:
        raise ValueError(tr("The snap does not contain meta/snap.yaml.")) from error
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(tr("The snap metadata could not be read: {0}").format(error)) from error
    return parse(text)


def inspect_snap(path, runner, *, revision=""):
    path = Path(path)
    if not is_snap(path):
        raise NotASnap(tr("This file is not a snap."))
    info = read_info(runner, path)
    desktop, icon, extension = "", b"", ""
    for app in info.apps:
        try:
            desktop = read_member(runner, path, f"meta/gui/{app.name}.desktop").decode("utf-8")
            break
        except (CommandError, UnicodeDecodeError):
            continue
    for candidate in ("meta/gui/icon.png", "meta/gui/icon.svg", "meta/icon.png", "meta/icon.svg"):
        try:
            icon = read_member(runner, path, candidate, limit=MAX_ICON)
        except (CommandError, ValueError):
            continue
        if icon:
            extension = posixpath.splitext(candidate)[1]
            break
    return Package(
        path,
        "snap",
        info.name,
        info.version,
        ",".join(info.architectures),
        desktop_entry=desktop,
        icon_data=icon,
        icon_extension=extension,
        snap=info,
        snap_revision=revision,
    )


def check(info):
    reason = refusal(info, platform.machine())
    if reason:
        raise ValueError(reason)


# Bases and content snaps


def runtime_names(info):
    """The base and content snaps info needs, in order."""
    names = []
    if info.base and info.base != "bare":
        names.append(info.base)
    for plug in info.content:
        if plug.provider not in names and plug.provider != info.name:
            names.append(plug.provider)
    return names


def _runtime_path(store, name, revision):
    if not NAME.fullmatch(name) or not REVISION.fullmatch(revision):
        raise ValueError(tr("Invalid snap name: {0}").format(name))
    return store.snap_runtime_directory() / name / revision


def installed_runtime(store, name):
    """The newest extracted revision of a shared snap, or ""."""
    directory = store.snap_runtime_directory() / name
    if not NAME.fullmatch(name) or not directory.is_dir() or directory.is_symlink():
        return ""
    revisions = [
        path.name
        for path in directory.iterdir()
        if REVISION.fullmatch(path.name) and regular_file(path, "meta/snap.yaml")
    ]
    return max(revisions, key=int, default="")


def missing_runtime(store, info):
    return [name for name in runtime_names(info) if not installed_runtime(store, name)]


# The installation's progress while the base and content snaps are prepared: between
# the stage that starts it (30) and extracting the snap (80), shared out among them.
RUNTIME_PROGRESS = (30, 80)


def _runtime_percent(index, count, fraction):
    start, end = RUNTIME_PROGRESS
    return start + int((end - start) * (index + min(1.0, fraction)) / max(1, count))


def _set_aside(target):
    """An unfinished copy where a shared snap goes (without meta/snap.yaml, so not in
    use: a removal that stopped halfway, or folders another tool left there) would
    make the new one fail to take its place: it is moved away and removed, as far
    as possible. What cannot be removed stays in a hidden folder, never in use."""
    if not os.path.lexists(target):
        return
    import uuid

    aside = target.parent / f".{target.name}.stale-{uuid.uuid4().hex[:8]}"
    os.replace(target, aside)
    if aside.is_symlink() or not aside.is_dir():
        aside.unlink(missing_ok=True)
    else:
        remove_tree(aside, ignore_errors=True)


def ensure_runtime(engine, info, *, store_api=None):
    """Download and extract what info needs. Returns ["name=revision", ...]."""
    from . import snap_store

    store_api = store_api or snap_store
    result = []
    names = runtime_names(info)
    for index, name in enumerate(names):
        revision = installed_runtime(engine.store, name)
        if not revision:
            release = store_api.release(name)
            if not NAME.fullmatch(release.name) or not REVISION.fullmatch(release.revision):
                raise RuntimeError(
                    tr("The Snap Store returned incomplete information for {0}.").format(name)
                )
            target = _runtime_path(engine.store, name, release.revision)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            archive = target.parent / f"{release.revision}.snap"

            def progress(received, total, name=name, index=index):
                engine.progress(
                    _runtime_percent(index, len(names), 0.9 * received / max(1, total)),
                    tr("Downloading {0} from the Snap Store: {1} of {2} MB…").format(
                        name, received // 1024**2, max(1, total // 1024**2)
                    ),
                )

            store_api.download(release, archive, progress=progress, cancelled=engine.cancelled)
            engine.progress(
                _runtime_percent(index, len(names), 0.9), tr("Preparing {0}…").format(name)
            )
            staging = target.parent / f".{release.revision}.tmp"
            if staging.exists():
                remove_tree(staging)
            try:
                extract(engine.runner, archive, staging)
                provided = read_info(engine.runner, archive)
                if provided.name != name:
                    raise ValueError(tr("The snap {0} has unexpected contents.").format(name))
                _set_aside(target)
                staging.replace(target)
            finally:
                archive.unlink(missing_ok=True)
                if staging.exists():
                    remove_tree(staging, ignore_errors=True)
            revision = release.revision
        result.append(f"{name}={revision}")
        engine._log(
            tr("Using {0} revision {1} ({2} of {3}).").format(name, revision, index + 1, len(names))
        )
    return result


def collect_runtime(store):
    """Remove shared snaps no installed snap uses any more."""
    used = {
        item
        for tx in store.transactions()
        if tx.method == "snap"
        for item in (*tx.snap_runtime, *tx.snap_previous_runtime)
    }
    root = store.snap_runtime_directory()
    if not root.is_dir():
        return
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        for revision in directory.iterdir():
            if revision.is_dir() and f"{directory.name}={revision.name}" not in used:
                remove_tree(revision, ignore_errors=True)
        try:
            directory.rmdir()
        except OSError:
            pass


def _mounts(store, tx):
    mounts = {}
    for item in tx.snap_runtime:
        name, _, revision = item.partition("=")
        path = _runtime_path(store, name, revision)
        if not regular_file(path, "meta/snap.yaml"):
            raise RuntimeError(_missing(name))
        mounts[name] = Mount(name, revision, str(path))
    return mounts


def content_mounts(info, providers, variables, warn=lambda _message: None):
    """(namespace path, local source) for each content plug that has a provider."""
    result = []
    for plug in info.content:
        provider = providers.get(plug.provider)
        if provider is None:
            continue
        try:
            provided = parse(read_metadata(provider.path, "meta/snap.yaml"))
        except (OSError, ValueError) as error:
            warn(tr("Could not read the content snap {0}: {1}").format(plug.provider, error))
            continue
        slot = next((item for item in provided.slots if item[0] == plug.content), None)
        if slot is None:
            warn(
                tr("The content snap {0} does not provide {1}.").format(plug.provider, plug.content)
            )
            continue
        _content, paths, uses_source = slot
        target = expand(plug.target, variables)
        if not target.startswith(("/snap/", "/var/snap/")):
            warn(tr("Ignoring the content target {0}.").format(plug.target))
            continue
        for path in paths:
            if path == "$SNAP" or path.startswith("$SNAP/"):
                relative = path.removeprefix("$SNAP")
            elif path.startswith("$"):
                # $SNAP_DATA and $SNAP_COMMON: the provider's own data, which MultiPak
                # does not keep for content snaps.
                warn(tr("Ignoring the content source {0}.").format(path))
                continue
            else:
                # As snapd does, a path without a variable is inside the provider: "/"
                # is all of it, as gnome-core26 shares itself.
                relative = path
            relative = relative.strip("/")
            source = os.path.normpath(os.path.join(provider.path, relative))
            root = os.path.realpath(provider.path)
            real = os.path.realpath(source)
            if real != root and not real.startswith(root + "/"):
                continue
            # With "source:", each path goes below the target under its own name;
            # without it, each goes at the target itself, as snapd does.
            destination = (
                posixpath.join(target, posixpath.basename(relative)) if uses_source else target
            )
            if os.path.isdir(source):
                result.append((destination, source))
    return tuple(_unclash(result))


def _unclash(mounts):
    """snapd's unclashMountEntries: a second mount at the same place goes to place-2,
    a third to place-3, and so on; the same source twice is mounted once."""
    result, seen = [], {}
    for destination, source in mounts:
        first = seen.get(destination)
        if first is None:
            seen[destination] = [source, 1]
            result.append((destination, source))
        elif first[0] != source:
            first[1] += 1
            result.append((f"{destination}-{first[1]}", source))
    return result


# Installation


def _snap_root(store, tx):
    return store.snap_directory(tx.id) / "root"


def launcher_invocation():
    """How the computer starts MultiPak's snap launcher, for shortcuts and services."""
    if host.is_flatpak():
        return ["/usr/bin/flatpak", "run", "--command=multipak-snap", host.APP_ID, "--"]
    return ["/usr/bin/python3", str(Path(__file__).with_name("snap_launcher.py"))]


def _autostart_path(tx):
    return _home() / ".config/autostart" / f"multipak-snap-{tx.id}-services.desktop"


def _write_autostart(tx, info):
    """snapd starts a snap's user services when each session starts, and its system
    services when the computer starts. MultiPak's services are units of the user's
    systemd, made with systemd-run, which the computer forgets when it restarts: an
    entry in the desktop's autostart folder starts both when the session starts."""
    path = _autostart_path(tx)
    wanted = [name for name in runnable_services(info) if info.app(name).install_mode == "enable"]
    if not wanted:
        path.unlink(missing_ok=True)
        return
    command = [*launcher_invocation(), "--services", "login", tx.id]
    text = (
        "[Desktop Entry]\nType=Application\n"
        f"Name=MultiPak snap {info.name}: {', '.join(wanted)}\n"
        f"Exec={' '.join(quote_exec(word) for word in command)}\n"
        "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _bus_directory():
    return _home() / ".local/share/dbus-1/services"


def _bus_files(tx, info):
    """(path, service) of each D-Bus name that starts one of the snap's user services."""
    return [
        (_bus_directory() / f"{bus_name}.service", name)
        for name in runnable_services(info)
        if info.app(name).scope == "user"
        for bus_name in info.app(name).bus_names
    ]


def _bus_marker(tx):
    return f"# MultiPak snap {tx.id}"


def _reload_bus(runner):
    try:
        runner.run(
            [
                "busctl",
                "--user",
                "call",
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ReloadConfig",
            ],
            timeout=30,
        )
    except (CommandError, OSError):
        pass  # The bus reads new files on its own too.


def _write_bus_services(engine, tx, info):
    """As snapd does for activates-on and bus-name: asking the session bus for the
    name starts the service. A name another program already provides is left to it."""
    written = False
    for path, name in _bus_files(tx, info):
        bus_name = path.name.removesuffix(".service")
        if path.exists() and _bus_marker(tx) not in path.read_text(errors="replace"):
            engine._log(
                tr("The D-Bus name {0} belongs to another program; it does not start {1}.").format(
                    bus_name, name
                )
            )
            continue
        command = [*launcher_invocation(), "--services", "start", tx.id, name]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            f"{_bus_marker(tx)}\n[D-BUS Service]\nName={bus_name}\n"
            f"Exec={' '.join(quote_exec(word) for word in command)}\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        written = True
    if written:
        _reload_bus(engine.runner)


def _remove_bus_services(engine, tx):
    directory = _bus_directory()
    removed = False
    for path in directory.glob("*.service") if directory.is_dir() else ():
        try:
            if _bus_marker(tx) in path.read_text(errors="replace"):
                path.unlink()
                removed = True
        except OSError:
            pass
    if removed:
        _reload_bus(engine.runner)


def _desktops(store, tx, info, root, *, save=True):
    """Write a launcher for each desktop file the snap ships."""
    gui = root / "meta/gui"
    if not gui.is_dir() or os.path.realpath(gui) != os.path.join(
        os.path.realpath(root), "meta/gui"
    ):
        return  # Missing, or a link that could lead out of the snap.
    snap = f"/snap/{info.name}/{tx.snap_revision}"
    invocation = launcher_invocation()
    for desktop in sorted(gui.glob("*.desktop")):
        try:
            parser = parse_desktop(read_metadata(root, f"meta/gui/{desktop.name}"))
        except (OSError, UnicodeDecodeError, ValueError):
            tx.warnings.append(tr("The shortcut {0} was skipped.").format(desktop.name))
            continue
        app_name = ""
        for section in list(parser.sections()):
            if section != "Desktop Entry" and not section.startswith("Desktop Action "):
                parser.remove_section(section)
                continue
            current = parser[section]
            tokens = shlex.split(current.get("Exec", ""))
            while tokens and (tokens[0] == "env" or ASSIGNMENT.match(tokens[0])):
                tokens.pop(0)
            if not tokens:
                parser.remove_section(section)
                continue
            target = posixpath.basename(tokens[0])
            name = target.removeprefix(info.name + ".") if target != info.name else info.name
            if info.app(name) is None or name in info.services:
                tx.warnings.append(tr("The shortcut {0} was skipped.").format(desktop.name))
                parser.remove_section(section)
                continue
            app_name = app_name or name
            command = [*invocation, tx.id, name]
            arguments = [
                quote_exec(word.replace("%%", "%"))
                if word not in ("%f", "%F", "%u", "%U")
                else word
                for word in tokens[1:]
                if word not in ("%k", "%c", "%i")
            ]
            current["Exec"] = " ".join(quote_exec(value) for value in command) + (
                " " + " ".join(arguments) if arguments else ""
            )
            icon = current.get("Icon", "")
            if icon:
                icon = icon.replace("${SNAP}", snap).replace("$SNAP", snap)
                icon = icon.replace(f"/snap/{info.name}/current", snap)
                if icon.startswith(snap + "/"):
                    local = root / icon.removeprefix(snap + "/")
                    resolved = local.resolve()
                    icon = str(resolved) if resolved.is_relative_to(root.resolve()) else ""
                current["Icon"] = icon or "application-x-executable"
            for key in ("TryExec", "Path", "X-SnapInstanceName", "X-SnapAppName"):
                current.pop(key, None)
        if "Desktop Entry" not in parser.sections() or not app_name:
            continue
        entry = parser["Desktop Entry"]
        entry["X-MultiPak-Method"] = "snap"
        entry["DBusActivatable"] = "false"
        filename = f"multipak-{tx.id}-snap-{desktop.stem}.desktop"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            continue
        if filename not in tx.launchers:
            tx.launchers.append(filename)
            if save:
                store.save(tx)
        text = io.StringIO()
        parser.write(text, space_around_delimiters=False)
        target = store.launcher(tx, filename)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(text.getvalue(), encoding="utf-8")
        temporary.replace(target)


ICON_PART = re.compile(r"[A-Za-z0-9@._+-]+")


def _icon_directory(store):
    """The desktop's own icons folder, next to the launchers (~/.local/share/icons)."""
    return Path(store.applications).parent / "icons"


def _icons_record(store, tx):
    return store.snap_directory(tx.id) / "icons.json"


def _export_icons(store, tx, info, root):
    """Icons the launchers name, as snapd shows them (wrappers/icons.go): the files in
    meta/gui/icons called snap.<name>.*.png or .svg, copied into the desktop's icons
    folder at the same place in the theme. Folders called snap.* are skipped."""
    source = Path(root) / "meta/gui/icons"
    expected = os.path.join(os.path.realpath(root), "meta/gui/icons")
    if not source.is_dir() or os.path.realpath(source) != expected:
        return
    prefix = f"snap.{info.name}."
    target, copied = _icon_directory(store), []
    for directory, folders, files in os.walk(source):
        folders[:] = [f for f in folders if not f.startswith("snap.") and ICON_PART.fullmatch(f)]
        for name in files:
            path = Path(directory) / name
            if not name.startswith(prefix) or path.suffix not in (".png", ".svg"):
                continue
            if not ICON_PART.fullmatch(name) or path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > MAX_ICON:
                continue
            relative = path.relative_to(source)
            destination = target / relative
            if destination.is_symlink():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_bytes(path.read_bytes())
            temporary.replace(destination)
            copied.append(str(relative))
    if copied:
        _icons_record(store, tx).write_text(json.dumps(sorted(copied)), encoding="utf-8")
        try:
            os.utime(target / "hicolor")  # Desktops refresh a theme when it changes.
        except OSError:
            pass


def _remove_icons(store, tx):
    try:
        names = json.loads(_icons_record(store, tx).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    target = _icon_directory(store)
    prefix = f"snap.{tx.snap_name}."
    for relative in names if isinstance(names, list) else []:
        parts = Path(str(relative)).parts
        if not parts or not all(ICON_PART.fullmatch(p) for p in parts):
            continue
        if not parts[-1].startswith(prefix) or ".." in parts:
            continue
        path = target.joinpath(*parts)
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)


def _complete(engine, package, components=None):
    tx = engine.tx
    tx.warnings.clear()
    directory = engine.store.snap_directory(tx.id)
    archive = directory / "package.snap"
    info = read_info(engine.runner, archive)
    if info.name != package.name:
        raise ValueError(tr("The file has changed. Open the snap again."))
    check(info)
    # Refuse a layout snapd would refuse before anything is downloaded.
    check_layouts(info, _variables(info, tx, str(Path.home())))
    engine._stage(30, tr("Preparing the components the snap needs…"))
    tx.snap_runtime = ensure_runtime(engine, info)
    engine.store.save(tx)
    engine._stage(80, tr("Extracting the snap…"))
    root = directory / "root"
    if root.exists():
        remove_tree(root)
    extract(engine.runner, archive, root)
    # The components chosen now, or, when retrying, the ones chosen before.
    if components is None:
        resources, requested = snap_components.load_resources(engine.store, tx)
    else:
        resources, requested = tuple(package.snap_resources), list(components)
        snap_components.save_resources(engine.store, tx, resources, requested)
    wanted = [n for n in requested if n in snap_components.installable(info, resources)]
    remove_tree(snap_components.directory(engine.store, tx), ignore_errors=True)
    tx.snap_components = []
    for name in wanted:
        engine._stage(83, tr("Downloading the component {0}…").format(name))
        resource = next(item for item in resources if item.name == name)
        tx.snap_components.append(
            snap_components.install(
                engine.runner, engine.store, tx, info, resource, cancelled=engine.cancelled
            )
        )
        engine.store.save(tx)
    # As with snapd, a failing script stops the installation.
    early = [name for name in INSTALL_HOOKS if name != "configure" and has_hook(root, name)]
    component_hooks = [
        name
        for name in wanted
        if has_hook(snap_components.hook_directory(engine.store, tx, name), "install")
    ]
    if early or component_hooks or has_hook(root, "configure"):
        engine._stage(86, tr("Running the snap's installation scripts…"))
    for name in early:
        run_hook(engine, tx, name)
    for name in component_hooks:
        run_hook(engine, tx, "install", component=name)
    if has_hook(root, "configure"):
        run_hook(engine, tx, "configure")
    engine._stage(92, tr("Adding the application to the application launcher…"))
    _desktops(engine.store, tx, info, root)
    _export_icons(engine.store, tx, info, root)
    try:
        services = missing_service_features(info)
    except Exception as error:
        engine._log(tr("Could not check the services the snap uses: {0}").format(error))
        services = []
    tx.limitations = limitations(info, snap_components.installed(tx), services)
    tx.warnings.extend(tx.limitations)
    archive.unlink(missing_ok=True)
    if not tx.launchers:
        tx.warnings.append(tr("This snap does not include an application shortcut."))
    tx.status, tx.error = "installed", ""
    engine.store.save(tx)
    if runnable_services(info):
        _write_autostart(tx, info)
        _write_bus_services(engine, tx, info)
        engine._stage(96, tr("Starting the snap's background services…"))
        try:
            Services(tx, info, runner_call(engine.runner), store=engine.store).start_enabled()
        except (OSError, RuntimeError, ValueError) as error:
            engine._log(str(error))
            tx.warnings.append(
                tr("The snap's background services could not be started: {0}").format(error)
            )
            engine.store.save(tx)
    engine.progress(100, tr("Installation completed."))
    return tx


# What still holds the snap: an installation, or one whose removal must be finished.
HELD = ("installed", "cleanup_failed")


def installed_snap(store, name):
    """The installation of the snap called name, or None. One that failed or was
    interrupted does not count: the next installation cleans it up first."""
    return next(
        (
            tx
            for tx in store.transactions()
            if tx.method == "snap" and tx.snap_name == name and tx.status in HELD
        ),
        None,
    )


def _clean_leftovers(engine, name):
    """Remove what a failed or interrupted installation of the snap left behind (its
    copy and shortcuts). While another installation runs, its lock refuses this."""
    for tx in engine.store.transactions():
        if tx.method == "snap" and tx.snap_name == name and tx.status not in HELD:
            engine.cleanup(tx)


def already_installed():
    return tr(
        "This snap is already installed. MultiPak will not overwrite the existing installation."
    )


def install(engine, package, components=()):
    if package.kind != "snap" or package.snap is None:
        raise ValueError(tr("The package is not a snap."))
    check(package.snap)
    # Before anything is copied: the computer must be able to give its system users.
    user_mapping(package.snap)
    _clean_leftovers(engine, package.snap.name)
    with engine.store.lock():
        # One installation per snap, as with snapd: a second would share its data in
        # ~/snap and both launchers would show the same app.
        if installed_snap(engine.store, package.snap.name) is not None:
            raise ValueError(already_installed())
        tx = Transaction(
            uuid.uuid4().hex,
            "",
            "",
            package.name,
            package.version,
            "snap",
            "",
            method="snap",
            container_created=False,
            snap_name=package.snap.name,
            snap_revision=package.snap_revision or LOCAL_REVISION,
            snap_channel=package.snap_channel,
            snap_confinement=package.snap.confinement,
        )
        engine.tx = tx
        try:
            engine.store.save(tx)
            engine._stage(5, tr("Copying the snap…"))
            directory = engine.store.snap_directory(tx.id)
            directory.mkdir(parents=True, mode=0o700)
            target = directory / "package.snap"
            downloads = engine.store.snap_download_directory()
            if Path(package.path).parent == downloads:
                # Downloaded by MultiPak for this installation: move it, do not copy.
                os.replace(package.path, target)
            else:
                engine._copy_package(package.path, target)
            with target.open("rb") as stream:
                tx.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            engine.store.save(tx)
            return _complete(engine, package, components)
        except Exception as error:
            reported = record_install_failure(engine.store, tx, error)
            if reported is not error:
                raise reported from error
            raise


def retry(engine, tx):
    with engine.store.lock():
        engine.tx = tx
        try:
            archive = engine.store.snap_directory(tx.id) / "package.snap"
            if archive.is_symlink() or not archive.is_file():
                raise ValueError(tr("The snap copy is missing. Open the snap again."))
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if not tx.sha256 or digest != tx.sha256:
                raise ValueError(tr("The snap copy has changed. Open the snap again."))
            package = inspect_snap(archive, engine.runner, revision=tx.snap_revision)
            return _complete(engine, package)
        except Exception as error:
            reported = record_install_failure(engine.store, tx, error)
            if reported is not error:
                raise reported from error
            raise


def cleanup(engine, tx):
    with engine.store.lock():
        engine.tx = tx
        try:
            if tx.status == "installed":
                root = _snap_root(engine.store, tx)
                _stop_before_removing(engine, tx)
                for component in snap_components.installed(tx):
                    if has_hook(
                        snap_components.hook_directory(engine.store, tx, component), "remove"
                    ):
                        try:
                            run_hook(engine, tx, "remove", component=component)
                        except Exception as error:
                            engine._log(str(error))
                for name in REMOVE_HOOKS:
                    if not has_hook(root, name):
                        continue
                    try:
                        run_hook(engine, tx, name)
                    except Exception as error:
                        # As with snapd, the application is removed anyway.
                        engine._log(str(error))
            _autostart_path(tx).unlink(missing_ok=True)
            _remove_bus_services(engine, tx)
            for name in tx.launchers:
                engine.store.launcher(tx, name).unlink(missing_ok=True)
                engine.store.launcher(tx, name).with_suffix(".tmp").unlink(missing_ok=True)
            _trash_user_data(engine, tx)
            _remove_icons(engine.store, tx)
            directory = engine.store.snap_directory(tx.id)
            _remove_private_tmp(engine, tx)
            _reclaim(engine, tx, directory)
            if directory.exists():
                remove_tree(directory)
            engine.store.remove_directory(tx)
            engine.tx = None
            collect_runtime(engine.store)
        except ServicesStillRunning:
            raise  # Nothing was removed: the snap stays installed, as it was.
        except Exception as error:
            tx.status, tx.error = "cleanup_failed", str(error)
            engine.store.save(tx)
            raise


def _home():
    return Path(os.environ.get("HOME") or Path.home())


def trash(path, home=None):
    """Move path to the user's trash, as file managers do (freedesktop.org Trash)."""
    from datetime import datetime
    from urllib.parse import quote

    home = Path(home or _home())
    files, info = home / ".local/share/Trash/files", home / ".local/share/Trash/info"
    files.mkdir(parents=True, exist_ok=True, mode=0o700)
    info.mkdir(parents=True, exist_ok=True, mode=0o700)
    name, number = path.name, 1
    while True:
        try:
            # The info file reserves the name; exclusive creation avoids two at once.
            fd = os.open(info / f"{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            number += 1
            name = f"{path.name}.{number}"
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                "[Trash Info]\n"
                f"Path={quote(str(path))}\n"
                f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
            )
        try:
            os.rename(path, files / name)
        except OSError:
            (info / f"{name}.trashinfo").unlink(missing_ok=True)
            raise
        return files / name


def _trash_user_data(engine, tx):
    """The app's own folder in ~/snap goes to the trash, recoverable: snapd removes it
    and keeps a snapshot for a while."""
    if not tx.snap_name or not NAME.fullmatch(tx.snap_name):
        return
    others = [
        item
        for item in engine.store.transactions()
        if item.id != tx.id and item.method == "snap" and item.snap_name == tx.snap_name
    ]
    folder = _home() / "snap" / tx.snap_name
    if others or folder.is_symlink() or not folder.is_dir():
        return
    try:
        trash(folder)
        engine._log(tr("The app's data in {0} was moved to the trash.").format(folder))
    except OSError as error:
        engine._log(tr("Could not move {0} to the trash: {1}").format(folder, error))


# Services


def _runtime_directory():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def missing_service_features(info, state=None):
    """Features of this snap whose service the computer lacks."""
    from .snap_meta import interfaces

    runtime_directory = _runtime_directory()
    state = state or snap_services.probe(runtime_directory)
    return snap_services.missing_features(interfaces(info), state, runtime_directory)


# Hooks


def has_hook(root, name):
    return regular_file(root, f"meta/hooks/{name}")


def _context(store, tx):
    info = parse(read_metadata(_snap_root(store, tx), "meta/snap.yaml"))
    return info, snap_ctl.state(store.snap_directory(tx.id))


def run_hook(engine, tx, name, *, timeout=600, component="", locked=True):
    """Run one of the snap's hooks, or one of a component's, as snapd would: as root,
    in the snap's view. locked: the caller already holds the store's lock."""
    if not host.which("bwrap"):
        raise RuntimeError(tr("Install bubblewrap on the system to open snaps."))
    info, state = _context(engine.store, tx)
    channel = snap_ctl.channel_directory(engine.store.snap_directory(tx.id))
    manager = ComponentRequests(engine.store, tx.id, engine.runner, locked=locked)
    # Requests to start or stop services wait for the script to end, as with snapd.
    daemons = Services(tx, info, runner_call(engine.runner), deferred=True, store=engine.store)
    with snap_ctl.Server(channel, info, state, manager, daemons=daemons):
        options, argv = plan_launch(
            engine.store, tx, "", [], hook=name, channel=channel, component=component
        )
        try:
            engine.runner.run(bubble_command(info, options, argv), timeout=timeout)
        except CommandError as error:
            lines = [line for line in (error.output or "").splitlines() if line.strip()]
            detail = "\n".join(lines[-5:]) or str(error)
            script = f"{info.name}+{component} {name}" if component else name
            raise RuntimeError(
                tr("The snap's {0} script failed:\n{1}").format(script, detail)
            ) from error


class ComponentRequests:
    """Installs and removes components when the snap asks with snapctl."""

    def __init__(self, store, transaction_id, runner, *, locked=False):
        self.store, self.transaction_id, self.runner = store, transaction_id, runner
        self.locked = locked

    def _transaction(self):
        tx = next((t for t in self.store.transactions() if t.id == self.transaction_id), None)
        if tx is None:
            raise RuntimeError(tr("This snap installation is unavailable."))
        return tx

    def _with_lock(self, work):
        if self.locked:
            return work()
        # Asked by a running app: another installation in progress is waited for,
        # instead of failing at once.
        with self.store.lock(wait=LOCK_WAIT):
            return work()

    def install(self, names):
        def work():
            from .engine import Engine

            tx = self._transaction()
            info, _state = _context(self.store, tx)
            resources, _requested = snap_components.load_resources(self.store, tx)
            published = {item.name: item for item in resources}
            for name in names:
                if name in snap_components.installed(tx):
                    continue
                if name not in snap_components.installable(info, resources):
                    raise ValueError(
                        tr("The component {0} cannot be installed by MultiPak.").format(name)
                    )
                tx.snap_components.append(
                    snap_components.install(self.runner, self.store, tx, info, published[name])
                )
                self.store.save(tx)
                engine = Engine(self.store, self.runner.cancelled)
                engine.runner = self.runner
                if has_hook(snap_components.hook_directory(self.store, tx, name), "install"):
                    run_hook(engine, tx, "install", component=name, locked=True)

        return self._with_lock(work)

    def remove(self, names):
        def work():
            from .engine import Engine

            tx = self._transaction()
            for name in names:
                if name not in snap_components.installed(tx):
                    continue
                engine = Engine(self.store, self.runner.cancelled)
                engine.runner = self.runner
                if has_hook(snap_components.hook_directory(self.store, tx, name), "remove"):
                    try:
                        run_hook(engine, tx, "remove", component=name, locked=True)
                    except RuntimeError as error:
                        engine._log(str(error))
                snap_components.remove(self.store, tx, name)
                tx.snap_components = [
                    item for item in tx.snap_components if item.partition("=")[0] != name
                ]
                self.store.save(tx)

        return self._with_lock(work)


# Launching


def _variables(info, tx, home):
    name, revision = info.name, tx.snap_revision
    return {
        "SNAP": f"/snap/{name}/{revision}",
        "SNAP_DATA": f"/var/snap/{name}/{revision}",
        "SNAP_COMMON": f"/var/snap/{name}/common",
        "SNAP_USER_DATA": f"{home}/snap/{name}/{revision}",
        "SNAP_USER_COMMON": f"{home}/snap/{name}/common",
        "SNAP_NAME": name,
        "SNAP_INSTANCE_NAME": name,
        "SNAP_INSTANCE_KEY": "",
        "SNAP_REVISION": revision,
        "SNAP_VERSION": info.version,
        "SNAP_ARCH": {"x86_64": "amd64", "aarch64": "arm64"}.get(
            platform.machine(), platform.machine()
        ),
        "SNAP_LIBRARY_PATH": LIBRARY_PATH,
        "SNAP_REAL_HOME": home,
        **({"SNAP_COMPONENTS": f"/snap/{name}/components/{revision}"} if info.components else {}),
    }


def environment(
    info, app, tx, host_environment, uid, *, runtime_directory=True, home=None, hook=False
):
    """The environment snap-exec would build, starting from the host's.

    app is the App, or the Hook when hook is set; hooks run as root, with home as the
    root's home directory."""
    home = home or host_environment.get("HOME") or str(Path.home())
    variables = _variables(info, tx, home)
    env = dict(host_environment)
    env.update(variables)
    if not hook:
        env["SNAP_APP_NAME"] = app.name
    else:
        env["USER"] = env["LOGNAME"] = "root"
        env.pop("XDG_RUNTIME_DIR", None)
        runtime_directory = False
    env["SNAP_UID"] = env["SNAP_EUID"] = str(uid)
    env["SNAP_LAUNCHER_ARCH_TRIPLET"] = {
        "x86_64": "x86_64-linux-gnu",
        "aarch64": "aarch64-linux-gnu",
    }.get(platform.machine(), "")
    if info.classic:
        env["PATH"] = CLASSIC_PATH + ":" + snap_ctl.TOOLS
        if hook:
            env["HOME"] = variables["SNAP_USER_DATA"]
    else:
        env["PATH"] = SNAP_PATH.format(snap=variables["SNAP"]) + ":" + snap_ctl.TOOLS
        env["HOME"] = variables["SNAP_USER_DATA"]
        if runtime_directory:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}/snap.{info.name}"
    # Top-level environment first, then the app's, each value expanded as it is set.
    for key, value in (*info.environment, *app.environment):
        env[key] = expand(value, env)
    return env, variables


def hook_command(info, hook, env):
    snap = env["SNAP"]
    return [
        *(posixpath.join(snap, item) for item in hook.command_chain),
        posixpath.join(snap, "meta/hooks", hook.name),
    ]


def command(info, app, env, arguments):
    words = app.command.split(" ")
    snap = env["SNAP"]
    argv = [posixpath.join(snap, words[0].lstrip("/"))]
    argv.extend(word for word in (expand(item, env) for item in words[1:]) if word)
    argv.extend(arguments)
    return [*(posixpath.join(snap, item) for item in app.command_chain), *argv]


PROBE = r"""
printf 'E\0'; env -0; printf '\0'
mode=paths
for item in "$@"; do
  if [ "$item" = "--list" ]; then mode=list; continue; fi
  if [ "$mode" = paths ]; then
    if [ -e "$item" ] || [ -L "$item" ]; then printf 'X\0%s\0' "$item"; fi
    continue
  fi
  [ -d "$item" ] || continue
  for entry in "$item"/* "$item"/.[!.]* "$item"/..?*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name=${entry##*/}
    if [ -L "$entry" ]; then kind=l; target=$(readlink "$entry")
    elif [ -d "$entry" ]; then kind=d; target=
    elif [ -f "$entry" ]; then kind=f; target=
    else kind=o; target=; fi
    printf 'L\0%s\0%s\0%s\0%s\0' "$item" "$name" "$kind" "$target"
  done
done
"""


def probe(paths, listings):
    """One host call: its environment, which paths exist and directory listings."""
    result = host.run(
        ["sh", "-c", PROBE, "multipak-snap", *paths, "--list", *listings],
        capture_output=True,
        timeout=30,
        check=True,
    )
    data = result.stdout.decode("utf-8", errors="surrogateescape")
    head, _, rest = data.partition("E\0")
    env_part, _, rest = rest.partition("\0\0")
    environment_values = {}
    for item in env_part.split("\0"):
        key, separator, value = item.partition("=")
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            environment_values[key] = value
    fields = rest.split("\0")
    exists, entries = set(), {}
    index = 0
    while index < len(fields):
        tag = fields[index]
        if tag == "X" and index + 1 < len(fields):
            exists.add(fields[index + 1])
            index += 2
        elif tag == "L" and index + 4 < len(fields):
            directory, name, kind, target = fields[index + 1 : index + 5]
            entries.setdefault(directory, []).append(Entry(name, kind, target))
            index += 5
        else:
            index += 1
    for directory in listings:
        entries.setdefault(directory, [])
    return environment_values, exists, entries


# Runs a service in its bubble, as systemd would run it: MultiPak writes "stop
# SIGNAL" or "reload" to the control channel, and the snap's stop-command or
# reload-command runs, then the signal goes to the service, or to all its processes
# (stop-mode); post-stop-command runs once it has ended. A forking service's daemon
# keeps the bubble until it ends (1 is bubblewrap's init, then this shell and the
# reader). A socket-activated service is the process that receives the sockets.
SERVICE = r"""
control=/snap/.multipak/channel/control
# Each start has a new process namespace, whose numbers would begin at 1 again: a
# daemon killed outright leaves a pid file whose number its next start then gets
# (MiniDLNA: "already running"). Start from a random number, as on the computer
# with snapd: set where the bubble may (with system users), else after using up a
# random few hundred numbers (a few milliseconds).
n=$(od -An -N2 -tu2 /dev/urandom 2>/dev/null)
n=${n:-$$}
if ! { echo $(( n % 30000 + 1000 )) > /proc/sys/kernel/ns_last_pid; } 2>/dev/null; then
  i=$(( n % 512 ))
  while [ "$i" -gt 0 ]; do : & i=$(( i - 1 )); done
  wait
fi
if [ -n "$LISTEN_FDS" ]; then
  /bin/sh -c 'LISTEN_PID=$$; export LISTEN_PID; exec "$@"' multipak-socket "$@" &
else
  "$@" &
fi
child=$!
(
  while :; do
    while read -r what signal; do
      case "$what" in
        reload) [ -n "$MULTIPAK_RELOAD" ] && eval "$MULTIPAK_RELOAD" ;;
        stop)
          [ -n "$MULTIPAK_STOP" ] && eval "$MULTIPAK_STOP"
          if [ "$MULTIPAK_STOP_ALL" = 1 ]; then
            # "--": dash, the shell of the bases, reads a bare -1 as an option.
            kill -s "$signal" -- -1 2>/dev/null
          else
            kill -s "$signal" "$child" 2>/dev/null
          fi ;;
      esac
    done < "$control"
  done
) &
reader=$!
trap '' HUP INT TERM USR1 USR2
wait "$child"
status=$?
if [ "$MULTIPAK_FORKING" = 1 ] && [ "$status" = 0 ]; then
  while :; do
    set -- /proc/[0-9]*
    [ "$#" -le 3 ] && break
    sleep 1
  done
fi
kill "$reader" 2>/dev/null
[ -n "$MULTIPAK_POST_STOP" ] && eval "$MULTIPAK_POST_STOP"
exit "$status"
"""
CONTROL = posixpath.join(snap_ctl.CHANNEL, "control")
# How long a request from a running snap waits for another installation to end.
LOCK_WAIT = 300
# systemd's defaults, which snapd keeps unless the snap sets its own.
START_SECONDS, STOP_SECONDS = 90, 30
SIGNALS = {
    "sigterm": "TERM",
    "sighup": "HUP",
    "sigusr1": "USR1",
    "sigusr2": "USR2",
    "sigint": "INT",
}


def _service_environment(info, app, env):
    """What the service script needs: the snap's other commands, as the shell runs
    them, and how to stop the service."""
    import dataclasses

    result = {"MULTIPAK_FORKING": "1" if app.daemon == "forking" else ""}
    for key, text in (
        ("MULTIPAK_STOP", app.stop_command),
        ("MULTIPAK_POST_STOP", app.post_stop_command),
        ("MULTIPAK_RELOAD", app.reload_command),
    ):
        if text:
            other = dataclasses.replace(app, command=text)
            result[key] = shlex.join(command(info, other, env, []))
    # As systemd: without stop-mode, the signal reaches every process of the service.
    result["MULTIPAK_STOP_ALL"] = "1" if not app.stop_mode or app.stop_mode.endswith("-all") else ""
    return result


# System users


def _subordinate(kind):
    """(first, count) of the user's subordinate IDs on the computer, /etc/subuid or
    /etc/subgid, as rootless Podman uses them; None when there are none."""
    import pwd

    result = host.run(["cat", f"/etc/sub{kind}"], capture_output=True, text=True, check=False)
    names = {str(os.getuid())}
    try:
        names.add(pwd.getpwuid(os.getuid()).pw_name)
    except KeyError:
        pass
    for line in (result.stdout or "").splitlines():
        fields = line.strip().split(":")
        if len(fields) == 3 and fields[0] in names and fields[1].isdigit():
            if fields[2].isdigit() and int(fields[2]) >= len(SYSTEM_USERS):
                return int(fields[1]), int(fields[2])
    return None


def user_mapping(info):
    """The unshare command that gives the bubble the snap's system users besides root:
    root is the user, as always, and each system user one of the user's subordinate
    IDs, the last ones, which Podman's containers leave unused. RuntimeError when
    the computer cannot do it."""
    names = [name for name in info.system_usernames if name in SYSTEM_USERS]
    if not names:
        return []
    ranges = {kind: _subordinate(kind) for kind in ("uid", "gid")}
    if not (host.which("newuidmap") and host.which("newgidmap")) or None in ranges.values():
        raise RuntimeError(
            tr(
                "This snap needs system user accounts ({0}). They need subordinate IDs "
                "for your user (/etc/subuid and /etc/subgid) and newuidmap, as rootless "
                "Podman does."
            ).format(", ".join(names))
        )
    argv = ["unshare", f"--map-users=0:{os.getuid()}:1", f"--map-groups=0:{os.getgid()}:1"]
    # snap_daemon the last ID, _daemon_ the one before, whatever the snap asks.
    for index, name in enumerate(sorted(SYSTEM_USERS, key=SYSTEM_USERS.get)):
        if name not in names:
            continue
        inner = SYSTEM_USERS[name]
        for kind, option in (("uid", "--map-users"), ("gid", "--map-groups")):
            first, count = ranges[kind]
            argv.append(f"{option}={inner}:{first + count - 1 - index}:1")
    return [*argv, "--setuid", "0", "--setgid", "0", "--"]


def bubble_command(info, options, argv):
    """The command that runs bubblewrap with options and argv. As root, a snap with
    system users gets them: bubblewrap runs in a user namespace that has them, made
    by unshare, instead of making its own with root alone."""
    identity = ["--unshare-user", "--uid", "0", "--gid", "0"]
    # plan_launch puts --clearenv (and --unshare-pid for services) before it.
    at = next(
        (
            index
            for index in range(len(options) - len(identity) + 1)
            if options[index : index + len(identity)] == identity
        ),
        None,
    )
    mapping = user_mapping(info) if at is not None else []
    if mapping:
        options = [*options[:at], *options[at + len(identity) :]]
    return [*mapping, "bwrap", *options, "--", *argv]


def _accounts(store, tx, info):
    """/etc/passwd and /etc/group as the computer has them, with the snap's system
    users added, as snapd adds them to the system: (path, local file) pairs."""
    names = [name for name in info.system_usernames if name in SYSTEM_USERS]
    if not names:
        return ()
    directory = store.snap_directory(tx.id) / "accounts"
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for path, line in (
        ("/etc/passwd", "{0}:x:{1}:{1}::/nonexistent:/usr/bin/false"),
        ("/etc/group", "{0}:x:{1}:"),
    ):
        read = host.run(["cat", path], capture_output=True, text=True, check=False)
        lines = [
            item for item in (read.stdout or "").splitlines() if item.split(":", 1)[0] not in names
        ]
        lines += [line.format(name, SYSTEM_USERS[name]) for name in names]
        target = directory / posixpath.basename(path)
        temporary = target.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(target)
        result.append((path, str(target)))
    return tuple(result)


# The snap's own /tmp, as snapd's /tmp/snap-private-tmp/snap.<name>/tmp: one for
# all its apps, services and hooks, so that a service finds the sockets another
# one leaves there (MySQL's and Redis's for Nextcloud's PHP). It is in the
# computer's /tmp, which no snap's bubble shows (each has its own /tmp), so other
# snaps never see it, and the computer empties it when it starts. A test may set
# PRIVATE_TMP_ROOT elsewhere.
PRIVATE_TMP_ROOT = None
_PRIVATE_TMP = r"""
root=$1 name=$2
umask 077
mkdir -m 700 "$root" 2>/dev/null
# Ours alone: a link, or a folder someone else made in /tmp, is never used.
if [ -L "$root" ] || [ ! -d "$root" ] || [ "$(stat -c %u "$root")" != "$(id -u)" ]; then
    echo "$root does not belong to this user" >&2
    exit 3
fi
chmod 700 "$root" || exit 3
mkdir -p "$root/$name" && chmod 700 "$root/$name" || exit 3
# Sticky and open to all, as /tmp: also for the snap's system users.
mkdir "$root/$name/tmp" 2>/dev/null
[ -d "$root/$name/tmp" ] && [ ! -L "$root/$name/tmp" ] || exit 3
chmod 1777 "$root/$name/tmp"
"""


def private_tmp_root():
    return Path(PRIVATE_TMP_ROOT or f"/tmp/multipak-private-tmp-{os.getuid()}")


def _private_tmp_folder(tx):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", tx.id):
        raise ValueError(tr("This snap installation is unavailable."))
    return private_tmp_root() / tx.id


def private_tmp(tx):
    """The snap's /tmp on the computer, made when missing."""
    folder = _private_tmp_folder(tx)
    result = host.run(
        ["sh", "-c", _PRIVATE_TMP, "multipak-tmp", str(folder.parent), folder.name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or "").strip() or str(result.returncode)
        raise RuntimeError(tr("Could not prepare the snap's /tmp folder: {0}").format(detail))
    return folder / "tmp"


def _remove_private_tmp(engine, tx):
    """On uninstall: the snap's /tmp goes with it. Its system users' files are
    removed from root, in the user namespace that has them."""
    import subprocess

    try:
        folder = _private_tmp_folder(tx)
    except ValueError:
        return
    mapping = []
    try:
        info, _state = _context(engine.store, tx)
        mapping = user_mapping(info)
    except Exception as error:
        engine._log(str(error))
    try:
        result = host.run(
            [*mapping, "rm", "-rf", "--one-file-system", "--", str(folder)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            engine._log((result.stderr or "").strip() or str(result.returncode))
    except (OSError, subprocess.TimeoutExpired) as error:
        engine._log(str(error))


class ServicesStillRunning(RuntimeError):
    """Uninstalling stopped before removing anything: a service would not stop."""


def _stop_before_removing(engine, tx):
    """Stop the snap's services. One that could not be stopped and still runs keeps
    the snap as it is: removing its files under it would be worse than trying again."""
    try:
        info, _state = _context(engine.store, tx)
    except Exception as error:
        engine._log(str(error))
        return
    services = Services(tx, info, runner_call(engine.runner), store=engine.store)
    try:
        services.stop_all()
    except Exception as error:
        engine._log(tr("Could not stop the snap's services: {0}").format(error))
        still = services.running()
        if still:
            raise ServicesStillRunning(
                tr(
                    "These services of the snap are still running: {0}. Nothing was "
                    "removed; stop them and try again."
                ).format(", ".join(still))
            ) from error


def _reclaim(engine, tx, directory):
    """Give back to the user the files the snap's system users own, so that they can
    be removed: from root, in the same user namespace."""
    try:
        info, _state = _context(engine.store, tx)
        mapping = user_mapping(info)
    except Exception as error:
        engine._log(str(error))
        return
    if mapping and directory.exists():
        try:
            engine.runner.run([*mapping, "chown", "-R", "0:0", str(directory)], timeout=600)
        except (CommandError, OSError) as error:
            engine._log(str(error))


def plan_launch(
    store,
    tx,
    app_name,
    arguments,
    *,
    uid=None,
    prober=probe,
    hook=None,
    channel="",
    component="",
    services=None,
    service=False,
    activation=(),
    notify=False,
    admin=False,
):
    """(bubblewrap arguments, command) for an installed snap's app, or for one of its
    hooks when hook names it; with component, that component's hook; with service,
    the background service app_name; activation names the sockets passed to it.
    channel is the directory snapctl requests go through. admin: the app runs as
    root in its bubble, as hooks do, like sudo with snapd (only when the user asks)."""
    from .snap_meta import interfaces, personal_directories, plug_attributes
    from .snap_sandbox import (
        ETC_FROM_DEBIAN_HOST,
        ETC_FROM_HOST,
        FONT_CACHES,
        HOST_DIRECTORIES,
        HOST_OPTIONAL,
        INTERFACE_DIRECTORIES,
        NVIDIA_DIRECTORIES,
    )

    if tx.method != "snap" or (tx.status != "installed" and hook is None):
        raise RuntimeError(tr("This snap installation is unavailable."))
    root = _snap_root(store, tx)
    if root.is_symlink() or not regular_file(root, "meta/snap.yaml"):
        raise RuntimeError(tr("The snap files are missing. Install the snap again."))
    info = parse(read_metadata(root, "meta/snap.yaml"))
    if service:
        app = info.app(app_name)
        if app is None or info.name != tx.snap_name or app_name not in info.services:
            raise RuntimeError(
                tr("This snap does not have a background service named {0}.").format(app_name)
            )
        if app_name not in runnable_services(info):
            raise RuntimeError(
                tr("MultiPak cannot run this service yet: {0}").format(", ".join(app.unsupported))
            )
    elif hook is None:
        app = info.app(app_name)
        if app is None or info.name != tx.snap_name:
            raise RuntimeError(
                tr("This snap does not have an application named {0}.").format(app_name)
            )
        if app_name in info.services:
            raise RuntimeError(
                tr("MultiPak does not run the snap's background services; nothing was started")
            )
    else:
        app = info.hook(hook)
        script = (
            snap_components.hook_directory(store, tx, component) if component else root
        ) / f"meta/hooks/{hook}"
        if info.name != tx.snap_name or script.is_symlink() or not script.is_file():
            raise RuntimeError(tr("This snap does not have a {0} script.").format(hook))
    check(info)
    if admin and (hook is not None or service):
        raise ValueError("admin is for apps")
    if admin and info.classic:
        raise RuntimeError(
            tr(
                "Classic snaps run without MultiPak's sandbox, so MultiPak does not run "
                "them as administrator."
            )
        )
    if admin and app_name not in admin_apps(info, graphical_apps(store, tx)):
        raise RuntimeError(
            tr(
                "Applications with a window do not run as administrator: as root they "
                "could leave files in the snap's data that only root can change."
            )
        )
    # Hooks and system services run as root, as with snapd; in the bubble, without
    # privileges. User services run as the user, in the session, as apps do. An app
    # the user asks to run as administrator, as with sudo, runs as root too: root of
    # its own user namespace, which is the user on the computer.
    as_root = hook is not None or (service and app.scope == "system") or admin
    uid = 0 if as_root else os.getuid() if uid is None else uid
    mounts = _mounts(store, tx)
    base = mounts.get(info.base) if info.base and info.base != "bare" else None
    if info.base and info.base != "bare" and base is None:
        raise RuntimeError(_missing(info.base))
    providers = tuple(mount for name, mount in mounts.items() if name != info.base)
    home = str(Path.home())
    paths = [
        *HOST_DIRECTORIES,
        *HOST_OPTIONAL,
        "/tmp/.X11-unix",
        "/sys/module/nvidia",
        posixpath.dirname(home),
        f"/run/user/{uid}",
        "/etc/debian_version",
        "/etc/fedora-release",
        "/etc/arch-release",
        *ETC_FROM_HOST,
        *ETC_FROM_DEBIAN_HOST,
        *FONT_CACHES,
        *(path for values in INTERFACE_DIRECTORIES.values() for path in values),
    ]
    # Parents of what may be shown from the host, to know files from links.
    shown = {*ETC_FROM_HOST, *FONT_CACHES, *(p for v in INTERFACE_DIRECTORIES.values() for p in v)}
    host_files = (
        ()
        if info.classic
        else tuple(p for p, _w in system_files(info)[0] if within(p, SYSTEM_FILE_HOST))
    )
    for path in host_files:
        # Every step of the path, to see links on the way.
        parts = path.strip("/").split("/")
        shown.update("/" + "/".join(parts[: index + 1]) for index in range(len(parts)))
    listings = sorted(
        {"/", "/etc", *(posixpath.dirname(path) for path in shown)}
        | {item for values in NVIDIA_DIRECTORIES.values() for item in values}
    )
    host_environment, exists, entries = prober(paths, listings)
    home = host_environment.get("HOME") or home
    root_home = store.snap_directory(tx.id) / "root-home"
    env, variables = environment(
        info,
        app,
        tx,
        host_environment,
        uid,
        runtime_directory=f"/run/user/{uid}" in exists,
        home=(str(root_home) if info.classic else "/root") if as_root else None,
        hook=as_root,
    )
    if service:
        env["SNAP_APP_NAME"] = app.name
        if notify:
            env["NOTIFY_SOCKET"] = posixpath.join(snap_ctl.CHANNEL, "notify")
        if app.watchdog_timeout:
            env["WATCHDOG_USEC"] = str(int(app.watchdog_timeout * 1_000_000))
        if activation:
            # sd_listen_fds: the sockets are 3, 4… in this order.
            env["LISTEN_FDS"] = str(len(activation))
            env["LISTEN_FDNAMES"] = ":".join(activation)
    if not as_root and "gsettings" in interfaces(info):
        runtime_directory = host_environment.get("XDG_RUNTIME_DIR") or _runtime_directory()
        if snap_services.needs_settings_file(
            interfaces(info), (services or snap_services.probe)(runtime_directory)
        ):
            # Without dconf, GSettings would keep the settings only until the app closes.
            env["GSETTINGS_BACKEND"] = "keyfile"
    if component:
        # What snapd tells a component's hooks about their component.
        revision = snap_components.installed(tx).get(component, "")
        env["SNAP_COMPONENT"] = f"/snap/{info.name}/components/mnt/{component}/{revision}"
        env["SNAP_COMPONENT_NAME"] = f"{info.name}+{component}"
        env["SNAP_COMPONENT_REVISION"] = revision
        env["SNAP_COMPONENT_VERSION"] = info.version
    if service:
        env.update(_service_environment(info, app, env))
    layouts = () if info.classic else check_layouts(info, variables)
    data = store.snap_directory(tx.id) / "data"
    local_data, local_common = data / tx.snap_revision, data / "common"
    if as_root and not info.classic:
        # /root/snap is shown from the installation's own directory.
        user_data = root_home / "snap" / info.name / tx.snap_revision
        user_common = root_home / "snap" / info.name / "common"
    else:
        user_data = Path(variables["SNAP_USER_DATA"])
        user_common = Path(variables["SNAP_USER_COMMON"])
    for directory in (local_data, local_common, user_data, user_common):
        directory.mkdir(parents=True, exist_ok=True)
    if any(name in SYSTEM_USERS for name in info.system_usernames):
        # As /var/snap with snapd: the snap's system users can reach their folders
        # in it, whatever umask made them.
        for directory in (local_data, local_common):
            directory.chmod(directory.stat().st_mode | 0o755)
    tools = snap_ctl.install_client(store.snap_directory(tx.id) / "tools")
    components = snap_components.directory(store, tx)
    snap_mount = Mount(info.name, tx.snap_revision, str(root))
    content = content_mounts(info, {m.name: m for m in providers}, variables)
    request = Request(
        snap=snap_mount,
        base=base,
        classic=info.classic,
        providers=providers,
        content=content if not info.classic else (),
        layouts=layouts,
        data=str(local_data),
        common=str(local_common),
        uid=uid,
        home=home,
        environment=tuple(sorted(env.items())),
        argv=(),
        workdir=variables["SNAP_USER_DATA"] if as_root or not info.classic else home,
        tools=str(tools),
        channel=str(channel),
        components=str(components) if components.is_dir() else "",
        as_root=as_root,
        root_home=str(root_home / "snap") if as_root and not info.classic else "",
        interfaces=interfaces(info),
        # snapd does not show the host's font caches on Fedora and Arch: they break
        # desktop snaps there.
        font_cache=not ({"/etc/fedora-release", "/etc/arch-release"} & exists),
        private_shm=any(
            value.get("private") is True for value in plug_attributes(info, "shared-memory")
        ),
        system_files=host_files,
        accounts=_accounts(store, tx, info),
        # Classic snaps use the computer's /tmp, as with snapd.
        tmp="" if info.classic else str(private_tmp(tx)),
    )
    if not as_root:
        for directory in personal_directories(info, home):
            Path(directory).mkdir(parents=True, exist_ok=True)
    prefixes = {
        f"/var/snap/{info.name}/common": local_common,
        f"/var/snap/{info.name}/{tx.snap_revision}": local_data,
    }
    for layout in layouts:
        # snapd creates missing layout sources in the snap's data directories.
        if layout.kind not in ("bind", "bind-file"):
            continue
        for prefix, local in prefixes.items():
            if layout.source == prefix or layout.source.startswith(prefix + "/"):
                path = local / layout.source[len(prefix) :].lstrip("/")
                # The app writes in these folders and may have left links there:
                # nothing is created through one, which could lead out of them.
                if not _without_links(local, path):
                    continue
                if layout.kind == "bind":
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch(exist_ok=True)
    arguments_bwrap, requested = build(
        request, lambda path: entries.get(path), lambda path: path in exists
    )
    missing = sorted(path for path in requested if path not in entries)
    if missing:
        _environment, more_exists, more = prober([], missing)
        entries.update(more)
        arguments_bwrap, _requested = build(
            request, lambda path: entries.get(path), lambda path: path in exists
        )
    if component:
        argv = [posixpath.join(env["SNAP_COMPONENT"], "meta/hooks", hook)]
    elif service:
        argv = ["/bin/sh", "-c", SERVICE, "multipak-service", *command(info, app, env, [])]
    elif hook is not None:
        argv = hook_command(info, app, env)
    else:
        argv = command(info, app, env, list(arguments))
    if service:
        # Its own process namespace: when the service ends, or is stopped, nothing it
        # started is left behind.
        return ["--unshare-pid", "--clearenv", *arguments_bwrap], argv
    return ["--clearenv", *arguments_bwrap], argv


class ApplicationRunning(RuntimeError):
    def __init__(self, transaction):
        super().__init__(tr("Close the snap application before updating it."))
        self.transaction = transaction


class ProcessesStillRunning(RuntimeError):
    """A process of the snap outlived what started it. Its services are left stopped,
    so that a second copy never runs beside it."""


# Written in the update's staging folder once the updater lets services start again.
SERVICES_ALLOWED = "services-allowed"
# How long the updater waits for a stopped service's processes to end.
SERVICE_STOP_WAIT = 10


def _lock_file(path):
    """A read-only descriptor for flock: what a sandbox inherits cannot write to it."""
    return os.open(path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)


@contextmanager
def application_lock(store, tx, *, update=False):
    """Prevent a launcher and a revision switch from using the same snap at once.
    Yields the locked descriptor; the launcher passes it to the app, so the lock
    stays held while any of the app's processes runs."""
    directory = store.snap_directory(tx.id)
    descriptor = _lock_file(directory / "application.lock")
    try:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if update else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if update:
                raise ApplicationRunning(tx) from error
            raise RuntimeError(tr("Wait for the snap update to finish.")) from error
        if not update and (directory / ".refresh").is_dir():
            raise RuntimeError(tr("An interrupted snap update needs recovery before retrying."))
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def service_lock(store, tx):
    """Held by each running service and passed to its processes. While a revision
    changes, the updater holds it exclusively: no service starts until the updater
    allows it, and a process that survived its service keeps the update waiting."""
    directory = store.snap_directory(tx.id)
    descriptor = _lock_file(directory / "services.lock")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(tr("Wait for the snap update to finish.")) from error
        stage = directory / ".refresh"
        # Checked while holding the lock: the updater cannot start changing the
        # revision until this service ends.
        if stage.is_dir() and not (stage / SERVICES_ALLOWED).is_file():
            raise RuntimeError(tr("Wait for the snap update to finish."))
        yield descriptor
    finally:
        os.close(descriptor)


class ServiceGate:
    """The updater's side of service_lock, for one snap and its staging folder."""

    def __init__(self, store, tx, stage, *, wait=None, clock=time.monotonic, sleep=time.sleep):
        self.path = store.snap_directory(tx.id) / "services.lock"
        self.stage, self.clock, self.sleep = stage, clock, sleep
        self.wait = SERVICE_STOP_WAIT if wait is None else wait
        self.descriptor = None

    def close(self):
        """After the services were stopped: wait for every process that holds the
        lock to end, then keep new services from starting."""
        descriptor = _lock_file(self.path)
        deadline = self.clock() + self.wait
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if self.clock() >= deadline:
                    os.close(descriptor)
                    raise ProcessesStillRunning(
                        tr(
                            "A process of the snap's services is still running. The services "
                            "were left stopped. End that process or restart the session, "
                            "then try again."
                        )
                    ) from None
                self.sleep(0.2)
        (self.stage / SERVICES_ALLOWED).unlink(missing_ok=True)
        self.descriptor = descriptor

    def open(self):
        """Let the updater's own services start: the new revision, or the old one back."""
        if self.stage.is_dir():
            (self.stage / SERVICES_ALLOWED).touch()
        self.release()

    def release(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def graphical_apps(store, tx):
    """The apps with a window: the ones the snap gives a menu shortcut, or a desktop
    file in snap.yaml. Commands for the administrator, such as Nextcloud's occ, have
    neither."""
    names = set()
    for filename in tx.launchers:
        try:
            parser = parse_desktop(store.launcher(tx, filename).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for section in parser.sections():
            try:
                tokens = shlex.split(parser[section].get("Exec", ""))
            except ValueError:
                continue
            # Our launchers run: <launcher> <transaction id> <app> [arguments].
            if tx.id in tokens[:-1]:
                names.add(tokens[tokens.index(tx.id) + 1])
    return names


def admin_apps(info, graphical=()):
    """The apps of a snap that can run as administrator: not its services, not its
    apps with a window (as root they could leave files only root can change in the
    snap's data), and not for classic snaps, which run without the sandbox."""
    if info.classic:
        return []
    return [
        app.name
        for app in info.apps
        if app.name not in info.services and app.name not in graphical and not app.desktop
    ]


def admin_label(info, name):
    """How snapd names the app: nextcloud.occ, or nextcloud for the snap's own."""
    return info.name if name == info.name else f"{info.name}.{name}"


def launch(transaction_id, app_name, arguments, *, admin=False):
    """Open one of the snap's apps; admin: as root in its bubble (never on the
    computer), for the snap's administration commands, such as Nextcloud's occ."""
    from .store import Store

    store = Store()
    tx = _installed(store, transaction_id)
    with application_lock(store, tx) as held:
        tx = _installed(store, transaction_id)
        return _launch(store, tx, app_name, arguments, held, admin=admin)


def _launch(store, tx, app_name, arguments, held, *, admin=False):
    if not host.which("bwrap"):
        raise RuntimeError(tr("Install bubblewrap on the system to open snaps."))
    info, state = _context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    # MultiPak stays alongside the app to answer snapctl and zenity, then cleans up.
    from .process import Runner

    runner = Runner(threading.Event(), lambda _line: None)
    requests = ComponentRequests(store, tx.id, runner)
    with (
        snap_application.close_channel(store, tx) as close_requested,
        snap_ctl.Server(
            channel,
            info,
            state,
            requests,
            dialogs=snap_dialog.run,
            opener=snap_open.run,
            daemons=Services(tx, info, store=store),
        ),
    ):
        options, argv = plan_launch(store, tx, app_name, arguments, channel=channel, admin=admin)
        # The app's processes inherit the lock, as 3: an update waits for all of
        # them. The lifeline, as 4, ends them if this launcher is killed outright.
        life = host.lifeline() if host.is_flatpak() else None
        invocation = host.command(
            bubble_command(info, options, argv),
            supervise=True,
            forward_fds=(3,),
            lifeline=None if life is None else 4,
        )
        if not host.is_flatpak() and invocation[0] == "bwrap":
            invocation[0] = shutil.which("bwrap") or invocation[0]
        return _supervise(invocation, close_requested=close_requested, keep=held, lifeline=life)[0]


def run_service(transaction_id, name):
    """Run one of the snap's background services until it ends; systemd calls this."""
    from .store import Store

    store = Store()

    def installed():
        tx = next((item for item in store.transactions() if item.id == transaction_id), None)
        if tx is None or tx.method != "snap" or tx.status != "installed":
            raise RuntimeError(tr("This snap installation is unavailable."))
        return tx

    tx = installed()
    if not host.which("bwrap"):
        raise RuntimeError(tr("Install bubblewrap on the system to open snaps."))
    with service_lock(store, tx) as held:
        # Read again under the lock: an update may have changed the revision.
        return _run_service(store, installed(), name, held)


def _run_service(store, tx, name, held):
    from .process import Runner

    info, state = _context(store, tx)
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    runner = Runner(threading.Event(), lambda _line: None)
    requests = ComponentRequests(store, tx.id, runner)
    app = info.app(name)
    sockets = _listen(store, tx, info, app) if app is not None and app.sockets else []
    # No dialogs and nothing opened on the desktop: a system service has no window.
    with snap_ctl.Server(
        channel,
        info,
        state,
        requests,
        opener=snap_open.run if app is not None and app.scope == "user" else None,
        daemons=Services(tx, info, store=store),
    ):
        watched = app.daemon == "notify" or app.watchdog_timeout
        notify = _NotifyListener(store, tx, name, channel) if watched else None
        os.mkfifo(channel / "control", 0o600)
        running = running_path(store, tx, name)
        running.parent.mkdir(parents=True, exist_ok=True)
        halt = halt_path(store, tx, name)
        halt.unlink(missing_ok=True)
        running.write_text(str(channel), encoding="utf-8")
        from .snap_daemons import unit

        watch = _UnitWatch(unit(tx, name)) if os.environ.get("INVOCATION_ID") else None
        try:
            while True:
                if sockets:
                    # Started by the first connection, as with socket activation.
                    if not _wait_for_connection(sockets, lambda: _ending(halt, watch)):
                        return 0
                names = [item[0] for item in app.sockets] if sockets else []
                options, argv = plan_launch(
                    store,
                    tx,
                    name,
                    [],
                    channel=channel,
                    service=True,
                    activation=names,
                    notify=notify is not None,
                )
                life = host.lifeline() if host.is_flatpak() else None
                if 3 + len(sockets) + 1 not in host.LIFELINE_NUMBERS:
                    life = None  # Too many sockets for the supervisor to watch it.
                invocation = host.command(
                    bubble_command(info, options, argv),
                    # If MultiPak's launcher ends, so does everything the service
                    # started on the computer: the lifeline tells even when the
                    # launcher is killed outright.
                    watch_bus=True,
                    supervise=True,
                    # The sockets as 3, 4…, then the service lock, which every
                    # process of the service inherits, then the lifeline.
                    forward_fds=range(3, 3 + len(sockets) + 1),
                    lifeline=None if life is None else 3 + len(sockets) + 1,
                )
                if not host.is_flatpak() and invocation[0] == "bwrap":
                    invocation[0] = shutil.which("bwrap") or invocation[0]
                run = _ServiceRun(
                    invocation, sockets, channel, app, notify, keep=held, lifeline=life
                )
                run.ending = lambda: _ending(halt, watch)
                status, stopped = run.run()
                # Without sockets the unit ends with the service; with them, the
                # sockets wait for the next connection, unless it was stopped.
                if not sockets or stopped:
                    return status
        finally:
            running.unlink(missing_ok=True)
            halt.unlink(missing_ok=True)
            for item in sockets:
                item.close()
            if notify is not None:
                notify.close()


def halt_path(store, tx, name):
    """Asks the service's launcher to stop the service and end."""
    return store.snap_directory(tx.id) / "running" / f"{name}.halt"


def _halt(store, tx, app):
    if app is None:
        return
    running = running_path(store, tx, app.name)
    if not running.exists():
        return
    halt_path(store, tx, app.name).touch()
    deadline = time.monotonic() + (app.stop_timeout or STOP_SECONDS) + 10
    while running.exists() and time.monotonic() < deadline:
        time.sleep(0.2)


def running_path(store, tx, name):
    """Present while the service runs: the folder of its control channel."""
    return store.snap_directory(tx.id) / "running" / name


def send_control(path, line):
    """Write one request to a running service's control channel; False when nothing
    reads it, because the service is not running."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(descriptor, (line + "\n").encode())
        return True
    except OSError:
        return False
    finally:
        os.close(descriptor)


class _ServiceRun:
    """One run of a service, as systemd supervises it: asked to stop, the service
    gets its stop-command and its stop signal, and is ended for good after its stop
    timeout; a notify service that does not say it is ready within its start timeout,
    or misses its watchdog, has failed and is stopped, so that systemd restarts it."""

    def __init__(
        self, invocation, sockets, channel, app, notify, *, clock=None, keep=None, lifeline=None
    ):
        import subprocess

        self.clock = clock or time.monotonic
        self.app, self.notify = app, notify
        self.control = Path(channel) / "control"
        self.process = (
            _SpawnedWithSockets(invocation, sockets, keep, lifeline)
            if sockets or keep is not None or lifeline is not None
            else subprocess.Popen(invocation)
        )
        self.started = self.clock()
        self.stop_deadline = None
        self.failed = False
        self.stopped = False
        self.ending = lambda: False

    def _hard_stop(self):
        import signal

        try:
            self.process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass

    def request_stop(self, signal_name=None):
        """Ask the service to stop, as systemd's ExecStop and KillSignal do."""
        if self.stop_deadline is not None:
            self._hard_stop()  # Asked twice: no more waiting.
            return
        name = signal_name or SIGNALS.get(self.app.stop_mode.removesuffix("-all"), "TERM")
        self.stop_deadline = self.clock() + (self.app.stop_timeout or STOP_SECONDS)
        if not send_control(self.control, f"stop {name}"):
            self._hard_stop()

    def _check(self):
        now = self.clock()
        if self.stop_deadline is None and self.ending():
            self.stopped = True
            self.request_stop()
            return
        if self.stop_deadline is not None:
            if now > self.stop_deadline:
                self._hard_stop()
                self.stop_deadline = now + 5
            return
        notify = self.notify
        if notify is None:
            return
        if self.app.daemon == "notify" and not notify.is_ready:
            if now - self.started > (self.app.start_timeout or START_SECONDS):
                self.failed = True
                self.request_stop()
            return
        timeout = self.app.watchdog_timeout
        if timeout and now - max(notify.last_ping or 0, self.started) > timeout:
            self.failed = True
            self.request_stop("ABRT")

    def run(self):
        """(exit status, whether it was asked to stop)."""
        import signal

        def asked(number, _frame):
            self.stopped = True
            self.request_stop()

        previous = {}
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, asked)
        try:
            while True:
                status = self.process.poll()
                if status is not None:
                    return (1 if self.failed and not self.stopped else status), self.stopped
                self._check()
                time.sleep(0.2)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


class _NotifyListener:
    """sd_notify for a notify service: the socket it sends READY=1 to, in the
    bubble's MultiPak folder. Systemd itself would not accept the message from the
    bubble, so MultiPak keeps it: snap_daemons waits for it before starting what
    comes after the service."""

    def __init__(self, store, tx, name, channel):
        import socket as sockets_module

        from .snap_daemons import ready_path

        self.ready = ready_path(store, tx, name)
        self.is_ready, self.last_ping = False, None
        self.ready.parent.mkdir(parents=True, exist_ok=True)
        self.ready.unlink(missing_ok=True)
        self.socket = sockets_module.socket(sockets_module.AF_UNIX, sockets_module.SOCK_DGRAM)
        folder = os.open(channel, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.socket.bind(f"/proc/self/fd/{folder}/notify")
        finally:
            os.close(folder)
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _listen(self):
        while True:
            try:
                message = self.socket.recv(4096).decode("utf-8", "replace")
            except OSError:
                return
            lines = message.splitlines()
            if "READY=1" in lines:
                self.is_ready = True
                self.ready.touch()
            if "WATCHDOG=1" in lines:
                self.last_ping = time.monotonic()
            if "STOPPING=1" in lines:
                self.ready.unlink(missing_ok=True)

    def close(self):
        self.ready.unlink(missing_ok=True)
        self.socket.close()


def socket_path(store, tx, info, app, listen):
    """Where a service's socket is on the computer, for a listen-stream path."""
    variable, _slash, rest = listen.partition("/")
    data = store.snap_directory(tx.id) / "data"
    home = _home()
    places = {
        "$SNAP_DATA": data / tx.snap_revision,
        "$SNAP_COMMON": data / "common",
        "$SNAP_USER_DATA": home / "snap" / info.name / tx.snap_revision,
        "$SNAP_USER_COMMON": home / "snap" / info.name / "common",
        "$XDG_RUNTIME_DIR": Path(_runtime_directory()) / f"snap.{info.name}",
    }
    base = places[variable]
    path = base / rest
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(tr("Invalid socket for a snap service: {0}").format(listen))
    return path


def _listen(store, tx, info, app):
    """The service's sockets, open and listening, in the order snap.yaml gives."""
    import socket as sockets_module

    from .snap_meta import socket_address

    result = []
    try:
        for _name, listen, mode in app.sockets:
            address = socket_address(listen, app.scope)
            if listen.startswith("$XDG_RUNTIME_DIR/") and host.is_flatpak():
                # The Flatpak's /run/user is its own: a socket there is not the
                # computer's, where the service and its clients would look.
                raise RuntimeError(
                    tr("MultiPak cannot create this service's socket: {0}").format(listen)
                )
            if address[0] == "unix":
                path = socket_path(store, tx, info, app, listen)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_socket() or path.is_symlink():
                    path.unlink()
                item = sockets_module.socket(sockets_module.AF_UNIX, sockets_module.SOCK_STREAM)
                result.append(item)
                # Through the folder's descriptor: the full path can be longer than
                # the 107 bytes a socket address holds.
                folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    item.bind(f"/proc/self/fd/{folder}/{path.name}")
                finally:
                    os.close(folder)
                path.chmod(mode)
            else:
                _kind, host_address, port = address
                family = (
                    sockets_module.AF_INET6
                    if ":" in host_address or not host_address
                    else sockets_module.AF_INET
                )
                item = sockets_module.socket(family, sockets_module.SOCK_STREAM)
                result.append(item)
                item.setsockopt(sockets_module.SOL_SOCKET, sockets_module.SO_REUSEADDR, 1)
                if family == sockets_module.AF_INET6 and not host_address:
                    # A port alone: every address, IPv4 too, as systemd does.
                    item.setsockopt(sockets_module.IPPROTO_IPV6, sockets_module.IPV6_V6ONLY, 0)
                item.bind((host_address or "::", port))
            item.listen(sockets_module.SOMAXCONN)
    except BaseException:
        for item in result:
            item.close()
        raise
    return result


def _wait_for_connection(sockets, ending=lambda: False):
    """True at the first connection; False when the service is to end first."""
    import select

    while not ending():
        try:
            ready, _w, _x = select.select(sockets, [], [], 1)
        except InterruptedError:
            continue
        if ready:
            return True
    return False


class _UnitWatch:
    """Whether systemd still runs the unit. Its signals do not reach into the Flatpak's
    sandbox: when the unit has ended anyway, the launcher must end its service."""

    def __init__(self, name, call=None):
        from .snap_daemons import host_call

        self.name, self.call = name, call or host_call
        self.checked, self.inactive = 0.0, 0

    def gone(self):
        now = time.monotonic()
        if now - self.checked < 5:
            return self.inactive >= 2
        self.checked = now
        _code, output = self.call(["systemctl", "--user", "is-active", self.name])
        state = output.strip().splitlines()[0] if output.strip() else ""
        self.inactive = self.inactive + 1 if state in ("inactive", "failed") else 0
        return self.inactive >= 2


def _ending(halt, watch):
    return halt.exists() or (watch is not None and watch.gone())


def _installed(store, key):
    """The installed snap whose installation ID or snap name is key."""
    tx = next(
        (
            item
            for item in store.transactions()
            if item.method == "snap"
            and item.status == "installed"
            and key in (item.id, item.snap_name)
        ),
        None,
    )
    if tx is None:
        raise RuntimeError(tr("This snap installation is unavailable."))
    return tx


def control_services(transaction_id, action, names):
    """multipak-snap --services: start, stop, restart or list a snap's services.
    transaction_id may also be the snap's name."""
    from .store import Store

    store = Store()
    tx = _installed(store, transaction_id)
    info, _state = _context(store, tx)
    services = Services(tx, info)
    if action == "list":
        return services.table(names)
    if action == "halt":
        # systemd's ExecStop: ask the running launcher to stop, and wait for it.
        for name in names:
            _halt(store, tx, info.app(name))
        return ""
    if (
        action in ("login", "start", "restart")
        and (store.snap_directory(tx.id) / ".refresh").is_dir()
    ):
        raise RuntimeError(tr("Wait for the snap update to finish."))
    if action == "login":
        # The session started: what snapd starts for each user then, and what it
        # starts with the computer, which the user's systemd forgot on restarting.
        services.start_enabled()
        return ""
    if action in ("start", "restart"):
        # Installations from before system services started with the session.
        _write_autostart(tx, info)
    if action not in ("start", "stop", "restart"):
        raise ValueError(tr("Unknown action: {0}").format(action))
    return services.act(action, names)


def configure(transaction_id, values, *, runner=None):
    """multipak-snap --set: what snap set does. The values are saved, then the snap's
    configure script runs; if it fails, the previous values are put back."""
    from .engine import Engine
    from .process import Runner
    from .store import Store

    store = Store()
    runner = runner or Runner(threading.Event(), lambda line: print(line, file=sys.stderr))
    with store.lock(wait=LOCK_WAIT):
        tx = _installed(store, transaction_id)
        _info, state = _context(store, tx)
        previous = state.config()
        code, text = snap_ctl.answer(["set", *values], _info, state)
        if code:
            raise ValueError(text.strip())
        if not has_hook(_snap_root(store, tx), "configure"):
            return
        engine = Engine(store, runner.cancelled)
        engine.runner = runner
        try:
            run_hook(engine, tx, "configure")
        except Exception:
            state.change_config(lambda config: (config.clear(), config.update(previous)))
            raise


class _SpawnedWithSockets:
    """A process started with the sockets as its 3, 4… and no other descriptor:
    Python's own are not inheritable, and posix_spawn moves the sockets there
    through numbers out of the way first, so that none overwrites another.
    keep: one more descriptor, given the number after the sockets; lifeline: the
    lifeline's read end (host.lifeline()), given the number after those."""

    def __init__(self, invocation, sockets, keep=None, lifeline=None):
        descriptors = [item.fileno() for item in sockets]
        if keep is not None:
            descriptors.append(keep)
        if lifeline is not None:
            descriptors.append(lifeline)
        actions = []
        for index, descriptor in enumerate(descriptors):
            actions.append((os.POSIX_SPAWN_DUP2, descriptor, 1000 + index))
        for index in range(len(descriptors)):
            actions.append((os.POSIX_SPAWN_DUP2, 1000 + index, 3 + index))
            actions.append((os.POSIX_SPAWN_CLOSE, 1000 + index))
        self.pid = os.posix_spawnp(invocation[0], invocation, os.environ, file_actions=actions)
        self.returncode = None

    def send_signal(self, number):
        if self.returncode is None:
            os.kill(self.pid, number)

    def wait(self, timeout=None):
        if timeout is not None:
            import subprocess

            deadline = time.monotonic() + timeout
            while self.poll() is None:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.pid, timeout)
                time.sleep(min(0.02, timeout))
            return self.returncode
        if self.returncode is None:
            _pid, status = os.waitpid(self.pid, 0)
            self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def poll(self):
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode


def _supervise(invocation, sockets=(), *, close_requested=None, keep=None, lifeline=None):
    """Run the app, passing on the signals that ask it to stop. Returns its status,
    and whether it was asked to stop. sockets: open sockets it receives as 3, 4…
    keep, then lifeline: descriptors it receives after them (without sockets, 3
    and 4)."""
    import signal
    import subprocess

    if sockets or keep is not None or lifeline is not None:
        process = _SpawnedWithSockets(invocation, sockets, keep, lifeline)
    else:
        process = subprocess.Popen(invocation)
    stopping = []

    def forward(number, _frame):
        stopping.append(number)
        try:
            process.send_signal(number)
        except ProcessLookupError:
            pass

    previous = {}
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[number] = signal.signal(number, forward)
    try:
        while True:
            try:
                status = process.wait() if close_requested is None else process.wait(timeout=0.2)
                return status, bool(stopping)
            except subprocess.TimeoutExpired:
                if close_requested():
                    forward(signal.SIGTERM, None)
            except InterruptedError:
                continue
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)

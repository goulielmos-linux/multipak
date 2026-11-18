"""Install or restore one revision of a Snap Store application."""

import hashlib
import json
import re
import threading
import uuid
from contextlib import ExitStack
from dataclasses import replace

from . import snap, snap_application, snap_components, snap_ctl, snap_store
from .localization import mark, tr
from .models import Transaction
from .process import Cancelled, Runner
from .snap_daemons import Services, runner_call
from .snap_meta import (
    NAME,
    REVISION,
    check_layouts,
    interfaces,
    plug_attributes,
    reaches_network,
    runnable_services,
    service_sockets,
    system_files,
)
from .store import remove_tree


def _paths(info):
    system, _ = system_files(info)
    paths = {("system-files", path, write) for path, write in system}
    for attributes in plug_attributes(info, "personal-files"):
        for key in ("read", "write"):
            values = attributes.get(key)
            if isinstance(values, list):
                paths.update(
                    ("personal-files", path, key == "write")
                    for path in values
                    if isinstance(path, str)
                )
    return paths


# What common interfaces let an application do, in plain words. In MultiPak's
# compatibility layer every interface a snap declares counts as connected.
INTERFACE_LABELS = {
    "audio-playback": mark("Play sound"),
    "audio-record": mark("Record sound from the microphone"),
    "avahi-control": mark("Announce and find services on the local network"),
    "avahi-observe": mark("Find services on the local network"),
    "bluez": mark("Use Bluetooth devices"),
    "browser-support": mark("Run a web browser engine"),
    "camera": mark("Use the camera"),
    "content": mark("Use files shared by another snap"),
    "cups-control": mark("Manage printers and print jobs"),
    "desktop": mark("Use the desktop: fonts, themes and notifications"),
    "gpg-keys": mark("Read your GnuPG keys"),
    "hardware-observe": mark("Read information about the computer's hardware"),
    "home": mark("Read and change the files in your home folder"),
    "joystick": mark("Use game controllers"),
    "locale-control": mark("Change the system language settings"),
    "login-session-control": mark("Control login sessions"),
    "mount-observe": mark("See the mounted disks and folders"),
    "network": mark("Access the internet and the network"),
    "network-bind": mark("Accept network connections"),
    "network-manager": mark("Manage the network connections"),
    "network-observe": mark("See the network settings and connections"),
    "opengl": mark("Use the graphics card"),
    "password-manager-service": mark("Read and store passwords in your keyring"),
    "process-control": mark("Control other programs running on the computer"),
    "pulseaudio": mark("Play and record sound"),
    "raw-usb": mark("Access USB devices directly"),
    "removable-media": mark("Read and change files on USB drives and other disks"),
    "screen-inhibit-control": mark("Keep the screen from turning off"),
    "serial-port": mark("Use serial ports"),
    "shutdown": mark("Shut down or restart the computer"),
    "ssh-keys": mark("Read your SSH keys"),
    "ssh-public-keys": mark("Read your public SSH keys"),
    "system-observe": mark("See the programs running on the computer"),
    "u2f-devices": mark("Use security keys"),
    "unity7": mark("Use the desktop of an X11 session"),
    "wayland": mark("Show windows in a Wayland session"),
    "x11": mark("Show windows and read the X11 session"),
}
# Shown with their own lines: the paths they give, and the D-Bus names (session_bus_names).
_DETAILED_INTERFACES = {"system-files", "personal-files"}
_SHOWN_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,80}")


def _shown(text):
    """Text from the package, fit for one line: at most 80 characters, no control ones."""
    text = str(text)
    return text if _SHOWN_TEXT.fullmatch(text) else repr(text[:80])


def _path(path):
    return "~/" + path[len("$HOME/") :] if path.startswith("$HOME/") else path


def _item(text, warning=False):
    return {"text": text, "warning": warning}


def _sockets(info):
    return {address: (name, listen) for name, listen, address in service_sockets(info)}


def _socket_item(name, listen, address):
    if address[0] == "unix":
        return _item(tr("Create the local socket {0} for the service {1}").format(listen, name))
    if not reaches_network(address):
        return _item(tr("Accept connections only from this computer (port {0})").format(address[2]))
    if not address[1]:
        text = tr("Accept connections from other computers on the network (port {0})")
        return _item(text.format(address[2]), warning=True)
    text = tr("Accept connections from other computers on the network (address {0}, port {1})")
    return _item(text.format(address[1], address[2]), warning=True)


def _layouts(info):
    return {(layout.path, layout.kind, layout.source) for layout in info.layouts}


def _layout_item(path, kind, source):
    if kind == "tmpfs":
        return _item(
            tr("Replace {0} with an empty temporary folder inside the app").format(_shown(path))
        )
    if kind == "symlink":
        return _item(
            tr("Replace {0} with a link to {1} inside the app").format(_shown(path), _shown(source))
        )
    return _item(tr("Replace {0} with {1} inside the app").format(_shown(path), _shown(source)))


def _bus_names(info):
    names = set(info.session_bus_names)
    for name in runnable_services(info):
        app = info.app(name)
        if app.scope == "user":
            names.update(app.bus_names)
    return names


def new_permissions(old, new):
    """What the new revision may do that the old one could not, one dict each:
    text (one line in plain words) and warning (other computers can connect to it).

    Only what is new: something the old revision already had is left out, while a
    path that changes from reading to writing is shown."""
    items = []
    if new.classic and not old.classic:
        items.append(_item(tr("Run without confinement (classic), with full access to the system")))

    services = runnable_services(new)
    previous = set(runnable_services(old))
    for name in services:
        if name not in previous:
            items.append(_item(tr("Start a background service: {0}").format(name)))
    for name in services:
        app, before = new.app(name), old.app(name)
        if app.timer and not (name in previous and before.timer):
            items.append(
                _item(
                    tr("Start the service {0} automatically on a schedule ({1})").format(
                        name, _shown(app.timer)
                    )
                )
            )

    sockets = _sockets(old)
    for address, (name, listen) in sorted(
        _sockets(new).items(), key=lambda entry: (not reaches_network(entry[0]), entry[1])
    ):
        if address not in sockets:
            items.append(_socket_item(name, listen, address))

    for bus_name in sorted(_bus_names(new) - _bus_names(old)):
        items.append(_item(tr("Use the D-Bus name {0} in your session").format(bus_name)))

    for user in sorted(set(new.system_usernames) - set(old.system_usernames)):
        items.append(_item(tr("Create the system user {0}").format(_shown(user))))

    for layout in sorted(_layouts(new) - _layouts(old)):
        items.append(_layout_item(*layout))

    previous_paths = _paths(old)
    added = sorted(
        (scope, path, write)
        for scope, path, write in _paths(new) - previous_paths
        if write or (scope, path, True) not in previous_paths
    )
    for _scope, path, write in added:
        text = tr("Write to {0}") if write else tr("Read {0}")
        items.append(_item(text.format(_shown(_path(path)))))

    for interface in sorted(interfaces(new) - interfaces(old)):
        if interface in _DETAILED_INTERFACES and any(kind == interface for kind, _, _ in added):
            continue
        label = INTERFACE_LABELS.get(interface)
        if label:
            text = tr("{0} ({1})").format(tr(label), interface)
        else:
            text = tr("Use the {0} interface").format(_shown(interface))
        items.append(_item(text))
    return tuple(items)


def permissions_title(name, permissions):
    """The dialog's sentence: how many new permissions the new version of name needs."""
    if len(permissions) == 1:
        return tr("The new version of {0} needs 1 new permission:").format(name)
    return tr("The new version of {0} needs {1} new permissions:").format(name, len(permissions))


def _installed(store, selected):
    matches = [tx for tx in store.transactions() if tx.id == selected.id]
    tx = matches[0] if matches else None
    if (
        tx is None
        or tx.method != "snap"
        or tx.status != "installed"
        or tx.snap_name != selected.snap_name
        or tx.snap_revision != selected.snap_revision
        or tx.snap_channel != selected.snap_channel
        or not NAME.fullmatch(tx.snap_name)
        or not REVISION.fullmatch(tx.snap_revision)
    ):
        raise ValueError(tr("This snap installation changed. Reload the list before updating."))
    return tx


def _present(path):
    return path.exists() or path.is_symlink()


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        remove_tree(path)


def _move(source, target, rollback):
    source.replace(target)
    rollback.callback(target.replace, source)


def _replace(source, target, backup, rollback):
    if _present(target):
        _move(target, backup, rollback)
    if _present(source):
        _move(source, target, rollback)


def _copy_data(runner, source, target, mapping):
    if not _present(source):
        return
    if source.is_symlink() or not source.is_dir():
        raise ValueError(tr("A snap data directory is unavailable or is a symbolic link."))
    runner.run([*mapping, "cp", "-a", "--", str(source), str(target)], timeout=1800)


def _data_paths(directory, old, revision):
    home = snap._home().resolve()
    homes = (
        directory / "data",
        directory / "root-home" / "snap" / old.snap_name,
        home / "snap" / old.snap_name,
    )
    for parent in homes:
        if any(path.is_symlink() for path in (parent, *parent.parents)):
            raise ValueError(tr("A snap data directory is unavailable or is a symbolic link."))
        yield parent / old.snap_revision, parent / revision


def _replace_registrations(engine, before, after, info):
    store = engine.store
    snap._remove_bus_services(engine, before)
    for name in before.launchers:
        store.launcher(before, name).unlink(missing_ok=True)
    snap._remove_icons(store, before)
    snap._icons_record(store, before).unlink(missing_ok=True)
    after.launchers = []
    root = snap._snap_root(store, after)
    snap._desktops(store, after, info, root, save=False)
    snap._export_icons(store, after, info, root)
    snap._write_autostart(after, info)
    snap._write_bus_services(engine, after, info)


def _restore_record(engine, old, new, old_info, registrations):
    try:
        if registrations:
            _replace_registrations(engine, new, old, old_info)
    finally:
        engine.store.save(old)


def _stop_services(store, tx, services, gate):
    """Stop the snap's services and make sure nothing of the snap still runs."""
    services.stop_all()
    if services.running():
        raise RuntimeError(tr("The snap still has running services. Stop them before updating."))
    # Waits for the processes that inherited the service lock, then holds it.
    gate.close()
    if snap_ctl.clear_orphan_channels(store.snap_directory(tx.id)):
        raise snap.ProcessesStillRunning(
            tr(
                "A process of the snap is still running. Its services were left stopped. "
                "End that process or restart the session, then try again."
            )
        )
    snap_application.clear_orphans(store, tx)


def _installed_components(engine, old, new, info, resources, target):
    names = snap_components.installed(old)
    available = set(snap_components.installable(info, resources))
    for name in names:
        if name not in available:
            raise ValueError(
                tr("The new revision does not provide the installed component {0}.").format(name)
            )
    for resource in resources:
        if resource.name in names:
            new.snap_components.append(
                snap_components.install(
                    engine.runner,
                    engine.store,
                    new,
                    info,
                    resource,
                    cancelled=engine.cancelled,
                    target_dir=target / "new-components",
                )
            )


class RecoveryFailed(RuntimeError):
    """The originals must stay in the staging directory for recovery."""


class _Undo:
    def __init__(self, stack):
        self.stack = stack
        self.errors = []

    def callback(self, function, *args):
        def attempt():
            try:
                function(*args)
            except Exception as error:
                self.errors.append(str(error))

        self.stack.callback(attempt)


def _activate(engine, old, new, old_info, new_info, stage, data=(), *, gate, reverting=False):
    store = engine.store
    directory = stage.parent
    runner = engine.runner
    recovery = _recovery_engine(engine)
    new_services = Services(new, new_info, runner_call(runner), store=store)
    recovery_services = Services(new, new_info, runner_call(recovery.runner), store=store)
    registrations = False
    guard = ExitStack()
    rollback = _Undo(guard)

    def restore():
        _restore_record(recovery, old, new, old_info, registrations)

    try:
        with guard:
            rollback.callback(restore)
            for name in ("root", "components", "resources.json"):
                source = directory / f"previous-{name}" if reverting else stage / f"new-{name}"
                _replace(source, directory / name, stage / f"old-{name}", rollback)
            for prepared, destination, backup in data:
                if _present(destination):
                    _move(destination, backup, rollback)
                if _present(prepared):
                    _move(prepared, destination, rollback)
                else:
                    rollback.callback(_remove, destination)
            store.save(new)
            registrations = True
            _replace_registrations(engine, old, new, new_info)
            store.save(new)
            rollback.callback(recovery_services.stop_all)
            if not reverting and snap.has_hook(directory / "root", "post-refresh"):
                snap.run_hook(engine, new, "post-refresh")
            # The updater's own services may start now; no other start gets through
            # while the staging folder remains.
            gate.open()
            new_services.start_enabled()
            # Publish the old revision only after the new revision is ready.
            for name in ("root", "components", "resources.json", "manifest.json"):
                _replace(
                    stage / f"old-{name}",
                    directory / f"previous-{name}",
                    stage / f"older-{name}",
                    rollback,
                )
            guard.pop_all()
    except BaseException as error:
        if rollback.errors:
            for detail in rollback.errors:
                engine._log(detail)
            raise RecoveryFailed(
                tr("Could not restore the snap after the update failed. The backup was kept.")
            ) from error
        raise


def _recovery_engine(engine):
    """Use an uncancelled runner while undoing a refresh."""
    from .engine import Engine

    recovery = Engine(engine.store, threading.Event(), log=engine._log)
    recovery.runner = Runner(recovery.cancelled, engine._log)
    return recovery


DOWNLOADS = ".refresh-download"


def _remove_leftovers(directory):
    """Preparation folders of an update that ended abruptly; never the staging folder,
    which holds what recovery needs."""
    for path in directory.glob(".refresh-*"):
        if path.name != DOWNLOADS and not path.is_symlink() and path.is_dir():
            remove_tree(path, ignore_errors=True)


def _reusable(archive, release):
    """A download kept from an attempt that found the app open, if it is intact."""
    size, expected = getattr(release, "size", None), getattr(release, "sha3_384", "")
    if not expected or archive.is_symlink() or not archive.is_file():
        return False
    if archive.stat().st_size != size:
        return False
    digest = hashlib.sha3_384()
    with archive.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest() == expected


def _download(engine, release, downloads):
    archive = downloads / f"{release.revision}.snap"
    if _reusable(archive, release):
        engine._log(tr("Using the snap revision downloaded earlier."))
        return archive
    if _present(downloads):
        _remove(downloads)
    downloads.mkdir(mode=0o700)
    snap_store.download(
        release,
        archive,
        cancelled=engine.cancelled,
        progress=lambda received, total: engine.progress(
            100 * received // max(total, 1), tr("Downloading the new snap revision…")
        ),
    )
    return archive


def refresh(engine, selected, expected_revision, confirm):
    """Refresh an installed snap and leave its old revision available for recovery.

    Everything that can fail on its own (the download, the checks, the permission
    prompt) happens while the snap keeps working. Only then are new launches held
    back, the pre-refresh script run with the services still up, and the services
    stopped for the switch."""
    store = engine.store
    with store.lock():
        old = _installed(store, selected)
        directory = store.snap_directory(old.id)
        stage = directory / ".refresh"
        if _present(stage):
            raise RuntimeError(tr("An interrupted snap update needs recovery before retrying."))
        # Tell at once if the app is open, before downloading anything.
        with snap.application_lock(store, old, update=True):
            pass
        _remove_leftovers(directory)
        old_info, state = snap._context(store, old)
        channel = old.snap_channel or "latest/stable"
        release = snap_store.release(old.snap_name, channel=channel)
        if release.revision != expected_revision or release.revision == old.snap_revision:
            raise ValueError(tr("The available snap revision changed. Check for updates again."))
        downloads = directory / DOWNLOADS
        prepared = directory / f".refresh-prepare-{uuid.uuid4().hex}"
        keep_download = False
        try:
            archive = _download(engine, release, downloads)
            new_info = snap.read_info(engine.runner, archive)
            if new_info.name != old.snap_name:
                raise ValueError(tr("The snap {0} has unexpected contents.").format(old.snap_name))
            snap.check(new_info)
            permissions = new_permissions(old_info, new_info)
            if permissions and not confirm(old.package_name, permissions):
                return None
            if engine.cancelled.is_set():
                raise Cancelled
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            new = replace(
                old,
                package_version=new_info.version,
                snap_revision=release.revision,
                snap_confinement=new_info.confinement,
                sha256=digest,
                snap_previous_revision=old.snap_revision,
                snap_previous_runtime=old.snap_runtime,
                launchers=[],
                warnings=[],
                snap_components=[],
                stage="",
                error="",
            )
            check_layouts(new_info, snap._variables(new_info, new, str(snap._home())))
            snap.user_mapping(new_info)
            engine.progress(-1, tr("Preparing the new snap revision…"))
            new.snap_runtime = snap.ensure_runtime(engine, new_info)
            prepared.mkdir(mode=0o700)
            snap.extract(engine.runner, archive, prepared / "new-root")
            _installed_components(engine, old, new, new_info, release.resources, prepared)
            snap_components.save_resources(
                store,
                new,
                release.resources,
                snap_components.installed(new),
                target=prepared / "new-resources.json",
            )
            new.limitations = snap.limitations(
                new_info, snap_components.installed(new), snap.missing_service_features(new_info)
            )
            new.warnings.extend(new.limitations)
            if engine.cancelled.is_set():
                raise Cancelled
            with ExitStack() as held:
                try:
                    held.enter_context(snap.application_lock(store, old, update=True))
                except snap.ApplicationRunning:
                    # Opened during the download: keep it for the next attempt.
                    keep_download = True
                    raise
                _installed(store, selected)
                try:
                    _switch(engine, old, new, old_info, new_info, state, prepared, stage)
                except RecoveryFailed:
                    # The staging folder is all recovery has: keep every package.
                    keep_download = True
                    raise
        finally:
            try:
                if _present(prepared):
                    remove_tree(prepared)
                if not keep_download and _present(downloads):
                    _remove(downloads)
                if not keep_download:
                    # Drops a runtime package downloaded for a revision that was not used.
                    snap.collect_runtime(store)
            except OSError as error:
                engine._log(str(error))
        try:
            previous = old.snap_previous_revision
            if REVISION.fullmatch(previous) and previous not in (
                old.snap_revision,
                new.snap_revision,
            ):
                for _, target in _data_paths(directory, old, previous):
                    _remove(target)
        except (OSError, ValueError) as error:
            engine._log(str(error))
        engine.progress(100, tr("Update complete."))
        return new


def _switch(engine, old, new, old_info, new_info, state, prepared, stage):
    """With new launches held back: pre-refresh, stop, copy the data, switch."""
    store = engine.store
    directory = stage.parent
    old_services = Services(old, old_info, runner_call(engine.runner), store=store)
    running, old_config = old_services.running(), state.config()
    stage.mkdir(mode=0o700)
    gate = snap.ServiceGate(store, old, stage)
    data, updated, preserve, survivors = [], False, False, False
    try:
        for name in ("new-root", "new-components", "new-resources.json"):
            if _present(prepared / name):
                (prepared / name).replace(stage / name)
        if snap.has_hook(directory / "root", "pre-refresh"):
            # With the services up, as snapd does: the script may need them.
            snap.run_hook(engine, old, "pre-refresh")
        if engine.cancelled.is_set():
            raise Cancelled
        engine.progress(-1, tr("Stopping the snap's services…"))
        _stop_services(store, old, old_services, gate)
        mapping = snap.user_mapping(old_info)
        for source, target in _data_paths(directory, old, new.snap_revision):
            # Prepare and save any previous copy on the same filesystem as the data.
            copy = target.with_name(f".multipak-refresh-{old.id}-{new.snap_revision}")
            backup = copy.with_suffix(".backup")
            if _present(copy) or _present(backup):
                raise RuntimeError(tr("An interrupted snap update needs recovery before retrying."))
            data.append((copy, target, backup))
            _copy_data(engine.runner, source, copy, mapping)
        (stage / "old-manifest.json").write_text(json.dumps(old.to_dict()), encoding="utf-8")
        _activate(engine, old, new, old_info, new_info, stage, data, gate=gate)
        updated = True
    except RecoveryFailed:
        preserve = True
        raise
    except snap.ProcessesStillRunning:
        survivors = True
        raise
    finally:
        if not preserve:
            try:
                if not updated:
                    state.change_config(lambda config: (config.clear(), config.update(old_config)))
                    if not survivors:
                        gate.open()
                        old_recovery = _recovery_engine(engine)
                        Services(
                            old, old_info, runner_call(old_recovery.runner), store=store
                        ).start(running)
                gate.release()
                # The revision is consistent now. Clear the launch guard before
                # pruning files so a cleanup failure does not stop it opening.
                retired = _retire_stage(stage)
                for copy, _, backup in data:
                    _remove(copy)
                    _remove(backup)
                remove_tree(retired)
            except (OSError, RuntimeError) as error:
                engine._log(str(error))
                if not updated:
                    raise RecoveryFailed(
                        tr(
                            "Could not restore the snap after the update failed. "
                            "The backup was kept."
                        )
                    ) from error
        else:
            gate.release()


def _retire_stage(stage):
    retired = stage.with_name(f".refresh-cleanup-{uuid.uuid4().hex}")
    stage.replace(retired)
    return retired


def _saved_revision(store, old, directory):
    message = tr("The previous snap revision is unavailable.")
    manifest = directory / "previous-manifest.json"
    root = directory / "previous-root"
    components = directory / "previous-components"
    resources = directory / "previous-resources.json"
    if (
        not REVISION.fullmatch(old.snap_previous_revision)
        or old.snap_previous_revision == old.snap_revision
        or manifest.is_symlink()
        or not manifest.is_file()
        or root.is_symlink()
        or (_present(components) and (components.is_symlink() or not components.is_dir()))
        or (_present(resources) and (resources.is_symlink() or not resources.is_file()))
    ):
        raise ValueError(message)
    try:
        previous = Transaction(**json.loads(manifest.read_text(encoding="utf-8")))
        info = snap.parse(snap.read_metadata(root, "meta/snap.yaml"))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(message) from error
    if (
        previous.id != old.id
        or previous.method != "snap"
        or previous.status != "installed"
        or previous.snap_revision != old.snap_previous_revision
        or previous.snap_name != old.snap_name
        or previous.snap_runtime != old.snap_previous_runtime
        or info.name != old.snap_name
        or info.version != previous.package_version
        or info.confinement != previous.snap_confinement
    ):
        raise ValueError(message)
    # The saved revision keeps its own base and optional components.
    snap._mounts(store, previous)
    for name, revision in snap_components.installed(previous).items():
        if (
            not NAME.fullmatch(name)
            or not REVISION.fullmatch(revision)
            or not snap.regular_file(components, f"mnt/{name}/{revision}/meta/component.yaml")
        ):
            raise ValueError(message)
    previous.snap_channel = old.snap_channel
    previous.snap_previous_revision = old.snap_revision
    previous.snap_previous_runtime = old.snap_runtime
    return previous, info


def revert(engine, selected):
    """Switch to the saved revision without copying data or consulting the store."""
    store = engine.store
    with store.lock(), snap.application_lock(store, selected, update=True):
        old = _installed(store, selected)
        if old.snap_previous_revision != selected.snap_previous_revision:
            raise ValueError(tr("This snap installation changed. Reload the list before updating."))
        directory = store.snap_directory(old.id)
        stage = directory / ".refresh"
        if _present(stage):
            raise RuntimeError(tr("An interrupted snap update needs recovery before retrying."))
        new, new_info = _saved_revision(store, old, directory)
        snap.check(new_info)
        snap.user_mapping(new_info)
        old_info, _ = snap._context(store, old)
        engine.progress(-1, tr("Stopping the snap's services…"))
        old_services = Services(old, old_info, runner_call(engine.runner), store=store)
        running = old_services.running()
        stage.mkdir(mode=0o700)
        gate = snap.ServiceGate(store, old, stage)
        try:
            _stop_services(store, old, old_services, gate)
            if engine.cancelled.is_set():
                raise Cancelled
            (stage / "old-manifest.json").write_text(json.dumps(old.to_dict()), encoding="utf-8")
            _activate(engine, old, new, old_info, new_info, stage, gate=gate, reverting=True)
        except RecoveryFailed:
            gate.release()
            raise
        except BaseException as error:
            try:
                # A surviving process keeps its services stopped: never a second copy.
                if not isinstance(error, snap.ProcessesStillRunning):
                    gate.open()
                    recovery = _recovery_engine(engine)
                    Services(old, old_info, runner_call(recovery.runner), store=store).start(
                        running
                    )
                gate.release()
                remove_tree(_retire_stage(stage))
            except (OSError, RuntimeError) as cleanup:
                engine._log(str(cleanup))
                raise RecoveryFailed(
                    tr("Could not restore the snap after the update failed. The backup was kept.")
                ) from cleanup
            raise
        else:
            gate.release()
            try:
                remove_tree(_retire_stage(stage))
            except OSError as error:
                engine._log(str(error))
        engine.progress(100, tr("The previous snap revision was restored."))
        return new

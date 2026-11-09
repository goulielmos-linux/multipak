"""Offer optional removal of an owned container after its last registered application."""

import configparser
import json
import re
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .environments import OWNER_LABEL
from .localization import tr
from .models import valid_environment_name
from .process import CommandError


@dataclass(frozen=True)
class ManagedContainer:
    name: str
    container_id: str
    owner_id: str = ""
    managed_home: bool = False

    def valid(self):
        return (
            valid_environment_name(self.name)
            and bool(re.fullmatch(r"[0-9a-f]{64}", self.container_id))
            and (not self.owner_id or bool(re.fullmatch(r"[0-9a-f]{32}", self.owner_id)))
            and isinstance(self.managed_home, bool)
        )


def _record_path(store, name):
    path = store.environment_directory(name) / "container.json"
    if path.is_symlink():
        raise ValueError(tr("The container record must not be a symbolic link."))
    return path


def _read_record(store, name):
    path = _record_path(store, name)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError(tr("Invalid container record."))
    container = ManagedContainer(**json.loads(path.read_text(encoding="utf-8")))
    if not container.valid() or container.name != name:
        raise ValueError(tr("Invalid container record."))
    return container


def _save_record(store, container):
    if not container.valid():
        raise ValueError(tr("Invalid container record."))
    path = _record_path(store, container.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(container), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def remember_container(store, transaction):
    """Keep ownership independently of the first application's installation record."""
    if transaction.method == "distrobox" and transaction.container_created:
        container = ManagedContainer(
            transaction.container,
            transaction.container_id,
            transaction.id,
            transaction.managed_home,
        )
        if container.valid():
            _save_record(store, container)


def _registered_shortcut(store, container):
    path = store.applications / f"{container.name}.desktop"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return False
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (configparser.Error, UnicodeError):
        return False
    return (
        parser.get("Desktop Entry", "X-MultiPak-ContainerIcon", fallback="") == "true"
        and parser.get("Desktop Entry", "X-MultiPak-ContainerID", fallback="")
        == container.container_id
    )


def _candidate(store, transaction):
    if transaction.method != "distrobox":
        return None
    container = ManagedContainer(transaction.container, transaction.container_id)
    if not container.valid():
        return None
    record = _read_record(store, container.name)
    if record is not None:
        return record if record.container_id == container.container_id else None
    if transaction.container_created:
        return ManagedContainer(
            container.name, container.container_id, transaction.id, transaction.managed_home
        )
    # An existing MultiPak shortcut pins ownership to the stored, immutable container ID.
    # A familiar container name alone never establishes ownership.
    return container if _registered_shortcut(store, container) else None


def _referenced(store, container):
    return any(
        tx.method == "distrobox"
        and (tx.container_id == container.container_id or tx.container == container.name)
        for tx in store.transactions()
    )


def _exists(runner, container_id):
    try:
        runner.run(["podman", "container", "exists", container_id], timeout=30)
        return True
    except CommandError as error:
        if error.code == 1:
            return False
        raise


def _home_path(store, container):
    home = store.environment_directory(container.name) / "home"
    if home.is_symlink() or home.resolve() != home or (home.exists() and not home.is_dir()):
        raise ValueError(tr("The container's home folder could not be verified. It will be kept."))
    return home


def _home_mounted(data, home):
    return any(
        mount.get("Type") == "bind" and mount.get("Source") == str(home)
        for mount in data.get("Mounts", [])
    )


def _check_home_references(runner, container, home):
    rows = json.loads(runner.run(["podman", "ps", "--all", "--format", "json"], timeout=30))
    for row in rows:
        container_id = row.get("Id", "")
        if container_id == container.container_id:
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError(
                tr("The container's home folder could not be verified. It will be kept.")
            )
        items = json.loads(runner.run(["podman", "inspect", container_id], timeout=30))
        for item in items:
            for mount in item.get("Mounts", []):
                source = Path(mount.get("Source", "/"))
                if source == home or home in source.parents:
                    raise RuntimeError(
                        tr("Another container uses this home folder. It will be kept.")
                    )


def _inspect(runner, container):
    if not _exists(runner, container.container_id):
        return None
    items = json.loads(runner.run(["podman", "inspect", container.container_id], timeout=30))
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError(tr("Could not verify container ownership."))
    data = items[0]
    if not isinstance(data, dict) or not isinstance(data.get("Config"), dict):
        raise ValueError(tr("Could not verify container ownership."))
    labels = data["Config"].get("Labels") or {}
    if not isinstance(labels, dict):
        raise ValueError(tr("Could not verify container ownership."))
    if (
        data.get("Id") != container.container_id
        or not isinstance(data.get("Name"), str)
        or data["Name"].removeprefix("/") != container.name
        or labels.get("manager") != "distrobox"
        or (container.owner_id and labels.get(OWNER_LABEL) != container.owner_id)
    ):
        raise ValueError(tr("The container identity or ownership has changed. It will be kept."))
    return data


def uninstall_application(engine, transaction):
    """Uninstall first; a failed eligibility check must not undo successful removal."""
    try:
        container = _candidate(engine.store, transaction)
    except (OSError, ValueError, TypeError, configparser.Error) as error:
        engine._log(tr("Could not check whether the container can be removed: {0}").format(error))
        container = None
    engine.cleanup(transaction, preserve_environment=True)
    if container is None:
        return None
    try:
        with engine.store.lock():
            data = _inspect(engine.runner, container)
            if data is None:
                return None
            if container.managed_home and not _home_mounted(
                data, _home_path(engine.store, container)
            ):
                container = replace(container, managed_home=False)
            _save_record(engine.store, container)
            if _referenced(engine.store, container):
                return None
        return container
    except (OSError, RuntimeError, ValueError, TypeError, TimeoutError) as error:
        engine._log(tr("Could not check whether the container can be removed: {0}").format(error))
        return None


def remove_container(engine, container, *, remove_home=False):
    """Delete the confirmed ID, optionally its verified home; preserve base images."""
    if not isinstance(container, ManagedContainer) or not container.valid():
        raise ValueError(tr("Invalid container record."))
    with engine.store.lock():
        if _read_record(engine.store, container.name) != container:
            raise ValueError(
                tr("The container identity or ownership has changed. It will be kept.")
            )
        if _referenced(engine.store, container):
            raise RuntimeError(tr("Another application uses this container. It will be kept."))
        if remove_home and not container.managed_home:
            raise ValueError(
                tr("The container's home folder could not be verified. It will be kept.")
            )
        home = _home_path(engine.store, container) if remove_home else None
        data = _inspect(engine.runner, container)
        if home is not None:
            if data is not None and not _home_mounted(data, home):
                raise ValueError(
                    tr("The container's home folder could not be verified. It will be kept.")
                )
            _check_home_references(engine.runner, container, home)
        engine.progress(-1, tr("Removing container {0}…").format(container.name))
        if data is not None:
            engine.runner.run(
                ["podman", "rm", "--force", "--time", "2", container.container_id], timeout=120
            )
            if _exists(engine.runner, container.container_id):
                raise RuntimeError(tr("The container still exists."))
        if home is not None and home.exists():
            shutil.rmtree(_home_path(engine.store, container))
        if _registered_shortcut(engine.store, container):
            (engine.store.applications / f"{container.name}.desktop").unlink()
        # Other files in the environment directory are outside this confirmation.
        _record_path(engine.store, container.name).unlink()

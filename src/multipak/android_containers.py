"""Offer removal only after Android confirms an owned instance has no user apps."""

from dataclasses import dataclass

from . import android, android_instances
from .localization import tr


@dataclass(frozen=True)
class ManagedAndroidContainer:
    name: str
    container_id: str


def no_user_apps(output):
    begin, end = "MULTIPAK_ANDROID_USER_PACKAGES_BEGIN", "MULTIPAK_ANDROID_USER_PACKAGES_END"
    lines = output.splitlines()
    if lines.count(begin) != 1 or lines.count(end) != 1:
        return False
    start, stop = lines.index(begin), lines.index(end)
    if start >= stop:
        return False
    for line in lines[start + 1 : stop]:
        if not line:
            continue
        if not line.startswith("package:") or not android.valid_package_id(line[8:]):
            return False
        if line[8:] != "io.github.multipak.gestures":
            return False
    return True


def _references(store, identifier):
    return any(
        tx.method == "waydroid" and tx.android_instance_id == identifier
        for tx in store.transactions()
    )


def uninstall_application(engine, transaction):
    eligible = (
        transaction.status == "installed"
        and transaction.android_package_owned
        and android_instances.valid_id(transaction.android_instance_id)
    )
    if not eligible:
        return engine.cleanup(transaction)
    empty = android.cleanup(engine, transaction, check_empty=True)
    with engine.store.lock():
        if not empty or _references(engine.store, transaction.android_instance_id):
            return None
        try:
            entry = next(
                (
                    e
                    for e in android_instances.discover(engine.runner)
                    if e["id"] == transaction.android_instance_id
                ),
                None,
            )
        except (OSError, RuntimeError, ValueError, TimeoutError) as error:
            engine._log(
                tr("Could not check whether the container can be removed: {0}").format(error)
            )
            return None
        if entry and entry.get("initialized"):
            return ManagedAndroidContainer(entry["label"], entry["id"])
    return None


def remove_container(engine, container, *, delete_data=False):
    with engine.store.lock():
        if not android_instances.valid_id(container.container_id) or _references(
            engine.store, container.container_id
        ):
            raise RuntimeError(tr("The Android container is still in use. Nothing was removed."))
        entry = next(
            (
                e
                for e in android_instances.discover(engine.runner)
                if e["id"] == container.container_id
            ),
            None,
        )
        if entry is None or entry["label"] != container.name:
            raise RuntimeError(tr("The Android container has changed. Nothing was removed."))
        android_instances.remove(
            engine.runner, container.container_id, delete_data=delete_data, only_empty=True
        )

"""Update a shared Snap Store base or content package."""

import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from . import snap, snap_refresh, snap_store
from .localization import tr
from .process import Cancelled
from .snap_daemons import Services, runner_call
from .snap_meta import NAME, REVISION
from .snap_updates import Runtime, managed
from .store import remove_tree


def _references(tx, name):
    return [entry for entry in tx.snap_runtime if entry.partition("=")[0] == name]


def _prepare(engine, selected, expected, dependents):
    release = snap_store.release(selected.name)
    if release.revision != expected:
        raise ValueError(tr("The available snap revision changed. Check for updates again."))
    store = engine.store
    target = snap._runtime_path(store, selected.name, expected)
    if target.parent.is_symlink() or target.is_symlink():
        raise ValueError(tr("The selected snap base or content is unavailable."))
    if target.exists():
        info = snap.parse(snap.read_metadata(target, "meta/snap.yaml"))
        if info.name != selected.name:
            raise ValueError(tr("The snap {0} has unexpected contents.").format(selected.name))
        _validate_runtime(selected.name, info, dependents)
        return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(prefix=".refresh-", dir=target.parent) as temporary:
        staging = Path(temporary)
        archive = staging / "runtime.snap"
        snap_store.download(
            release,
            archive,
            cancelled=engine.cancelled,
            progress=lambda received, total: engine.progress(
                100 * received // max(total, 1),
                tr("Downloading {0} from the Snap Store…").format(selected.name),
            ),
        )
        info = snap.read_info(engine.runner, archive)
        if info.name != selected.name:
            raise ValueError(tr("The snap {0} has unexpected contents.").format(selected.name))
        _validate_runtime(selected.name, info, dependents)
        extracted = staging / "root"
        snap.extract(engine.runner, archive, extracted)
        extracted.replace(target)


def _validate_runtime(name, info, dependents):
    for _, application in dependents:
        if application.base == name and info.kind not in ("base", "os"):
            raise ValueError(tr("{0} is not a snap base.").format(name))
        provided = {content for content, _, _ in info.slots}
        for plug in application.content:
            if plug.provider == name and plug.content not in provided:
                raise ValueError(
                    tr("{0} no longer provides the content {1} required by {2}.").format(
                        name, plug.content, application.name
                    )
                )


def _service(engine, tx, info):
    return Services(tx, info, runner_call(engine.runner), store=engine.store)


def _switch(engine, runtime, revision, dependents):
    store = engine.store
    saved, restarted, paused, gates, survivors = [], [], [], {}, set()
    reference = f"{runtime.name}={revision}"
    with ExitStack() as guards:
        for tx, _info in dependents:
            stage = store.snap_directory(tx.id) / ".refresh"
            if snap_refresh._present(stage):
                raise RuntimeError(tr("An interrupted snap update needs recovery before retrying."))
            stage.mkdir(mode=0o700)
            guards.callback(remove_tree, stage)
            (stage / "old-manifest.json").write_text(json.dumps(tx.to_dict()), encoding="utf-8")
            gates[tx.id] = gate = snap.ServiceGate(store, tx, stage)
            guards.callback(gate.release)
        try:
            engine.progress(-1, tr("Stopping the snap's services…"))
            for tx, info in dependents:
                services = _service(engine, tx, info)
                paused.append((tx, info, services.running()))
                try:
                    snap_refresh._stop_services(store, tx, services, gates[tx.id])
                except snap.ProcessesStillRunning:
                    survivors.add(tx.id)
                    raise
            if engine.cancelled.is_set():
                raise Cancelled
            for tx, _ in dependents:
                updated = replace(
                    tx,
                    snap_runtime=[
                        reference if entry.partition("=")[0] == runtime.name else entry
                        for entry in tx.snap_runtime
                    ],
                )
                saved.append(tx)
                store.save(updated)
            for tx, info, running in paused:
                restarted.append((tx, info))
                gates[tx.id].open()
                _service(engine, tx, info).start(running)
        except BaseException:
            recovery = snap_refresh._recovery_engine(engine)
            errors = []
            for tx, info in reversed(restarted):
                try:
                    _service(recovery, tx, info).stop_all()
                except (OSError, RuntimeError) as error:
                    errors.append(str(error))
            for tx in reversed(saved):
                try:
                    store.save(tx)
                except OSError as error:
                    errors.append(str(error))
            for tx, info, running in paused:
                # A surviving process keeps its services stopped: never a second copy.
                if tx.id in survivors:
                    continue
                try:
                    gates[tx.id].open()
                    _service(recovery, tx, info).start(running)
                except (OSError, RuntimeError) as error:
                    errors.append(str(error))
            if errors:
                guards.pop_all()
                for gate in gates.values():
                    gate.release()
                for error in errors:
                    engine._log(error)
                raise snap_refresh.RecoveryFailed(
                    tr("Could not restore the snap after the update failed. The backup was kept.")
                ) from None
            raise


def update(engine, selected, expected):
    """Install a shared revision, then change the active references together.

    The download happens while the apps keep working; launches are held back only
    for the switch itself."""
    store = engine.store
    if not NAME.fullmatch(selected.name) or not REVISION.fullmatch(expected):
        raise ValueError(tr("The selected snap base or content is unavailable."))
    reference = f"{selected.name}={expected}"
    with store.lock():
        items = managed(store)
        if selected not in items:
            raise ValueError(tr("This snap installation changed. Reload the list before updating."))
        pending = [
            item
            for item in items
            if not isinstance(item, Runtime)
            and (references := _references(item, selected.name))
            and references != [reference]
        ]
        if not pending:
            return Runtime(selected.name, (expected,))
        # Tell at once if one of the apps is open, before downloading anything.
        for item in pending:
            with snap.application_lock(store, item, update=True):
                pass
        dependents = [(item, snap._context(store, item)[0]) for item in pending]
        _prepare(engine, selected, expected, dependents)
        if engine.cancelled.is_set():
            raise Cancelled
        with ExitStack() as locks:
            # The package stays on disk: if an app was opened meanwhile, the next
            # attempt does not download it again.
            for item in pending:
                locks.enter_context(snap.application_lock(store, item, update=True))
            _switch(engine, selected, expected, dependents)
        try:
            # Previous snap revisions can still reference the old runtime.
            snap.collect_runtime(store)
        except OSError as error:
            engine._log(str(error))
        engine.progress(100, tr("Update complete."))
        return Runtime(selected.name, (expected,))

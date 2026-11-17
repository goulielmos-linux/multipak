"""Check for updates to installed snaps and their shared runtime packages."""

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import snap_store
from .localization import tr
from .process import Cancelled
from .snap import LOCAL_REVISION, _runtime_path
from .snap_meta import NAME, REVISION

CACHE_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class Runtime:
    name: str
    revisions: tuple[str, ...]

    @property
    def id(self):
        return f"runtime:{self.name}"


@dataclass(frozen=True)
class Check:
    status: str  # available, current, or failed
    revision: str = ""
    version: str = ""
    channel: str = ""
    error: str = ""
    checked: float = 0.0
    stale: bool = False


def managed(store):
    """Installed snaps followed by each shared runtime once."""
    snaps = sorted(
        (tx for tx in store.transactions() if tx.method == "snap" and tx.status == "installed"),
        key=lambda tx: (tx.package_name.casefold(), tx.id),
    )
    return snaps + runtimes(snaps)


def _references(tx):
    for entry in tx.snap_runtime or ():
        if not isinstance(entry, str):
            continue
        name, _, revision = entry.partition("=")
        if NAME.fullmatch(name) and REVISION.fullmatch(revision):
            yield name, revision


def runtimes(snaps):
    """Each shared runtime the snaps' active revisions use, with the revisions in use."""
    found = {}
    for tx in snaps:
        for name, revision in _references(tx):
            found.setdefault(name, set()).add(revision)
    return [
        Runtime(name, tuple(sorted(revisions, key=int)))
        for name, revisions in sorted(found.items())
    ]


def behind(snaps, name, revision):
    """The snaps that still use another revision of a shared runtime."""
    return [
        tx
        for tx in snaps
        if any(used == name and number != revision for used, number in _references(tx))
    ]


def downloaded(store, name, revision):
    """Whether that revision of a shared runtime is already on disk."""
    try:
        path = _runtime_path(store, name, revision)
    except ValueError:
        return False
    return not path.is_symlink() and (path / "meta/snap.yaml").is_file()


def reconcile(item, result):
    """Whether a stored result still says "available" or "current" for this item, after
    its installed revision changed without consulting the store."""
    if result is None or result.status not in ("available", "current"):
        return result
    if isinstance(item, Runtime):
        current = item.revisions == (result.revision,)
    else:
        current = item.snap_revision == result.revision
    return replace(result, status="current" if current else "available")


def _target(item):
    if isinstance(item, Runtime):
        # Runtime packages use the same default stable channel as installation.
        return item.name, "", item.revisions
    return item.snap_name, item.snap_channel or "latest/stable", (item.snap_revision,)


def _targets(store):
    return {
        item.id: _target(item)
        for item in managed(store)
        if isinstance(item, Runtime) or item.snap_revision != LOCAL_REVISION
    }


class _Cache:
    def __init__(self, store):
        self.path = store.root / "snap-updates.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.entries = data if isinstance(data, dict) else {}

    @staticmethod
    def fingerprint(target):
        name, channel, revisions = target
        return [name, channel, list(revisions)]

    def get(self, identifier, target):
        entry = self.entries.get(identifier)
        if not isinstance(entry, dict) or entry.get("target") != self.fingerprint(target):
            return None
        try:
            value = Check(**entry["result"])
            if value.status in ("available", "current"):
                return replace(value, checked=float(value.checked))
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, identifier, target, result):
        self.entries[identifier] = {"target": self.fingerprint(target), "result": asdict(result)}

    def save(self, targets):
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {key: self.entries[key] for key in targets if key in self.entries}, stream
                )
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def cached(store, *, now=time.time):
    """Recent results from disk, without consulting the Snap Store."""
    cache = _Cache(store)
    timestamp = now()
    return {
        identifier: result
        for identifier, target in _targets(store).items()
        if (result := cache.get(identifier, target)) is not None
        and 0 <= timestamp - result.checked < CACHE_SECONDS
    }


def check(store, *, cancelled=None, progress=None, log=None, now=time.time):
    """Consult the Snap Store for each target. Return results by item ID."""
    targets = _targets(store)
    if not targets:
        return {}
    cache, results = _Cache(store), {}
    for identifier, target in targets.items():
        if cancelled is not None and cancelled.is_set():
            raise Cancelled
        name, channel, revisions = target
        if progress is not None:
            progress(-1, tr("Checking for updates to {0}…").format(name))
        try:
            release = snap_store.release(name, channel=channel)
            status = "current" if revisions == (release.revision,) else "available"
            result = Check(
                status, release.revision, release.version, release.channel, checked=now()
            )
            cache.put(identifier, target, result)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            if log is not None:
                log(tr("Could not check for updates to {0}: {1}").format(name, error))
            previous = cache.get(identifier, target)
            result = (
                replace(previous, stale=True, error=str(error))
                if previous is not None
                else Check("failed", channel=channel, error=str(error))
            )
        results[identifier] = result
    try:
        cache.save(targets)
    except OSError as error:
        if log is not None:
            log(tr("Could not save the update check: {0}").format(error))
    return results


def remember(store, results):
    """Keep results the manager adjusted after an update, so that reopening the list
    shows them; only for what is installed now."""
    targets = _targets(store)
    cache = _Cache(store)
    for identifier, result in results.items():
        target = targets.get(identifier)
        if target is not None and result.status in ("available", "current") and not result.stale:
            cache.put(identifier, target, result)
    cache.save(targets)

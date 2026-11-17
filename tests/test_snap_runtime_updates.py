"""Shared snap runtime updates switch installed references consistently."""

import threading
from types import SimpleNamespace

import pytest
from test_snap_updates import installed_snap

from multipak import snap, snap_runtime_updates, snap_store
from multipak.engine import Engine
from multipak.snap_updates import Runtime


@pytest.fixture
def configured(store, monkeypatch):
    events = []
    remote = SimpleNamespace(revision="8", text="name: core22\ntype: base\n")
    for token, name in (("a", "alpha"), ("b", "beta")):
        tx = installed_snap(store, token, name, "1", runtime=("core22=7",))
        if token == "a":
            tx.snap_previous_runtime = ["core22=7"]
            store.save(tx)
        meta = store.snap_directory(tx.id) / "root/meta"
        meta.mkdir(parents=True)
        (meta / "snap.yaml").write_text(f"name: {name}\nbase: core22\n")
    for revision in ("6", "7"):
        path = store.snap_runtime_directory() / "core22" / revision / "meta"
        path.mkdir(parents=True)
        (path / "snap.yaml").write_text("name: core22\ntype: base\n")

    class Services:
        def __init__(self, tx, *_args, **_kwargs):
            self.name = tx.snap_name
            self.stopped = False

        def running(self):
            return [] if self.stopped else ["worker"]

        def stop_all(self):
            self.stopped = True
            events.append(("stop", self.name))

        def start(self, running):
            events.append(("start", self.name, running))

    def download(release, target, **_kwargs):
        events.append(("download", release.revision))
        target.write_text(release.text)

    def extract(runner, source, target):
        (target / "meta").mkdir(parents=True)
        (target / "meta/snap.yaml").write_bytes(source.read_bytes())

    monkeypatch.setattr(snap_runtime_updates, "Services", Services)
    monkeypatch.setattr(snap_store, "release", lambda name: remote)
    monkeypatch.setattr(snap_store, "download", download)
    monkeypatch.setattr(snap, "read_info", lambda runner, archive: snap.parse(archive.read_text()))
    monkeypatch.setattr(snap, "extract", extract)
    return SimpleNamespace(
        engine=Engine(store, threading.Event()), events=events, remote=remote, services=Services
    )


def test_update_downloads_one_shared_package_and_preserves_refs_to_previous_revision(
    configured, store
):
    p = configured
    assert snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8") == Runtime(
        "core22", ("8",)
    )
    assert p.events == [
        ("download", "8"),
        ("stop", "alpha"),
        ("stop", "beta"),
        ("start", "alpha", ["worker"]),
        ("start", "beta", ["worker"]),
    ]
    assert [tx.snap_runtime for tx in store.transactions()] == [["core22=8"], ["core22=8"]]
    folder = store.snap_runtime_directory() / "core22"
    assert sorted(path.name for path in folder.iterdir()) == ["7", "8"]
    assert all(
        not (store.snap_directory(tx.id) / ".refresh").exists() for tx in store.transactions()
    )


def test_runtime_update_refuses_removal_of_required_content(configured, store):
    p = configured
    alpha = store.transactions()[0]
    (store.snap_directory(alpha.id) / "root/meta/snap.yaml").write_text(
        "name: alpha\nbase: bare\nplugs:\n  theme:\n    interface: content\n"
        "    content: theme\n    default-provider: core22\n    target: $SNAP/theme\n"
    )
    with pytest.raises(ValueError, match="no longer provides the content theme"):
        snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    assert p.events == [("download", "8")]
    assert not (store.snap_runtime_directory() / "core22/8").exists()


def test_a_failed_restart_restores_all_active_runtime_references(configured, store, monkeypatch):
    p = configured
    snapshots = []

    class Services(p.services):
        def start(self, running):
            snapshots.append([tx.snap_runtime for tx in store.transactions()])
            if self.name == "beta" and snapshots[-1] == [["core22=8"], ["core22=8"]]:
                raise RuntimeError("could not start")
            super().start(running)

    monkeypatch.setattr(snap_runtime_updates, "Services", Services)
    with pytest.raises(RuntimeError, match="could not start"):
        snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    assert [tx.snap_runtime for tx in store.transactions()] == [["core22=7"], ["core22=7"]]
    assert p.events[-2:] == [("start", "alpha", ["worker"]), ("start", "beta", ["worker"])]
    assert all(
        not (store.snap_directory(tx.id) / ".refresh").exists() for tx in store.transactions()
    )


def test_running_application_prevents_the_download(configured, store):
    p = configured
    with snap.application_lock(store, store.transactions()[0]):
        with pytest.raises(RuntimeError, match="Close the snap"):
            snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    assert p.events == []


def test_an_app_opened_during_the_download_does_not_cost_a_second_download(
    configured, store, monkeypatch
):
    from contextlib import ExitStack

    p = configured
    opened = ExitStack()
    prior_download = snap_store.download

    def download(release, target, **kwargs):
        prior_download(release, target, **kwargs)
        opened.enter_context(snap.application_lock(store, store.transactions()[1]))

    monkeypatch.setattr(snap_store, "download", download)
    with opened, pytest.raises(RuntimeError, match="Close the snap"):
        snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    monkeypatch.setattr(snap_store, "download", prior_download)
    # Nothing stopped, nothing switched, and the package is ready for the next try.
    assert p.events == [("download", "8")]
    assert [tx.snap_runtime for tx in store.transactions()] == [["core22=7"], ["core22=7"]]
    assert (store.snap_runtime_directory() / "core22/8/meta/snap.yaml").is_file()
    snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    assert p.events.count(("download", "8")) == 1
    assert [tx.snap_runtime for tx in store.transactions()] == [["core22=8"], ["core22=8"]]


def test_a_surviving_service_process_keeps_that_snaps_services_stopped(
    configured, store, monkeypatch
):
    p = configured
    monkeypatch.setattr(snap, "SERVICE_STOP_WAIT", 0.3)
    beta = store.transactions()[1]
    with snap.service_lock(store, beta):
        with pytest.raises(snap.ProcessesStillRunning):
            snap_runtime_updates.update(p.engine, Runtime("core22", ("7",)), "8")
    assert [tx.snap_runtime for tx in store.transactions()] == [["core22=7"], ["core22=7"]]
    # alpha's services come back; beta's stay stopped beside the survivor.
    assert p.events[-1] == ("start", "alpha", ["worker"])
    assert ("start", "beta", ["worker"]) not in p.events
    assert all(
        not (store.snap_directory(tx.id) / ".refresh").exists() for tx in store.transactions()
    )

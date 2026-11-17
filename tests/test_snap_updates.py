"""Snap Store checks, shared runtimes and the local update cache."""

import threading
from types import SimpleNamespace

import pytest

from multipak import snap_updates
from multipak.models import Transaction
from multipak.process import Cancelled


def installed_snap(store, token, name, revision, *, channel="", runtime=(), status="installed"):
    tx = Transaction(
        token * 32,
        "",
        "",
        name,
        "1.0",
        "snap",
        "",
        method="snap",
        status=status,
        snap_name=name,
        snap_revision=revision,
        snap_channel=channel,
        snap_runtime=list(runtime),
    )
    store.save(tx)
    return tx


def release(revision, channel):
    return SimpleNamespace(revision=revision, version=f"v{revision}", channel=channel)


def test_checks_installed_channels_and_shared_packages_once(store, monkeypatch):
    beta = installed_snap(store, "a", "alpha", "12", channel="latest/beta", runtime=("core22=7",))
    stable = installed_snap(
        store, "b", "beta", "6", runtime=("core22=7", "gnome-content=8", "bad-format")
    )
    local = installed_snap(store, "c", "local", "x1", runtime=("core22=6",))
    installed_snap(store, "d", "broken", "1", status="failed", runtime=("core24=9",))
    calls = []
    remote = {"alpha": "13", "beta": "6", "core22": "7", "gnome-content": "8"}

    def lookup(name, *, channel):
        calls.append((name, channel))
        return release(remote[name], channel or "latest/stable")

    monkeypatch.setattr(snap_updates.snap_store, "release", lookup)
    assert snap_updates.cached(store) == {}
    assert calls == []
    results = snap_updates.check(store)
    assert calls == [
        ("alpha", "latest/beta"),
        ("beta", "latest/stable"),
        ("core22", ""),
        ("gnome-content", ""),
    ]
    assert results[beta.id] == snap_updates.Check(
        "available", "13", "v13", "latest/beta", checked=results[beta.id].checked
    )
    assert results[stable.id].status == results["runtime:gnome-content"].status == "current"
    assert results["runtime:core22"].status == "available"
    assert local.id not in results
    assert snap_updates.managed(store)[-2].revisions == ("6", "7")
    assert snap_updates.cached(store) == results
    assert len(calls) == 4


def test_cache_expires_and_tracks_the_installed_revision_and_channel(store, monkeypatch):
    tx = installed_snap(store, "a", "alpha", "12")
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda name, *, channel: release("13", channel)
    )
    snap_updates.check(store, now=lambda: 10)
    assert tx.id in snap_updates.cached(store, now=lambda: 11)
    assert snap_updates.cached(store, now=lambda: 10 + snap_updates.CACHE_SECONDS) == {}
    tx.snap_channel = "latest/edge"
    store.save(tx)
    assert snap_updates.cached(store, now=lambda: 11) == {}
    snap_updates.check(store, now=lambda: 12)
    tx.snap_revision = "13"
    store.save(tx)
    assert snap_updates.cached(store, now=lambda: 13) == {}


def test_an_outage_keeps_the_last_result_and_does_not_stop_other_snaps(store, monkeypatch):
    alpha = installed_snap(store, "a", "alpha", "1")
    beta = installed_snap(store, "b", "beta", "1")
    gamma = installed_snap(store, "c", "gamma", "1")

    def lookup(name, *, channel):
        if name != "alpha":
            raise RuntimeError("unavailable")
        return release("2", channel)

    monkeypatch.setattr(snap_updates.snap_store, "release", lookup)
    assert snap_updates.check(store)[alpha.id].status == "available"

    def refresh(name, *, channel):
        if name != "gamma":
            raise RuntimeError("offline")
        return release("1", channel)

    monkeypatch.setattr(snap_updates.snap_store, "release", refresh)
    results = snap_updates.check(store)
    assert results[alpha.id].status == "available" and results[alpha.id].stale
    assert results[beta.id].status == "failed" and results[beta.id].error == "offline"
    assert results[gamma.id].status == "current"
    assert not snap_updates.cached(store)[alpha.id].stale


def test_cancelled_check_never_consults_the_store(store, monkeypatch):
    installed_snap(store, "a", "alpha", "1")
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda *args, **kwargs: pytest.fail("unexpected lookup")
    )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(Cancelled):
        snap_updates.check(store, cancelled=cancelled)

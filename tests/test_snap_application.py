"""Closing a snap through its own launcher, not by searching process names."""

import sys
import threading

from test_snap_updates import installed_snap

from multipak import snap, snap_application


def test_close_requests_go_to_the_selected_snap_application(store):
    target = installed_snap(store, "a", "browser", "1")
    other = installed_snap(store, "b", "editor", "2")
    for tx in (target, other):
        store.snap_directory(tx.id).mkdir(parents=True)
    with (
        snap_application.close_channel(store, target) as target_requested,
        snap_application.close_channel(store, other) as other_requested,
    ):
        snap_application._request_close(store, target)
        assert target_requested()
        assert not other_requested()


def test_supervised_application_exits_when_the_manager_asks(store):
    tx = installed_snap(store, "a", "browser", "1")
    store.snap_directory(tx.id).mkdir(parents=True)
    completed = []

    def close():
        completed.append(snap_application.close(store, tx, wait=4))

    with snap.application_lock(store, tx), snap_application.close_channel(store, tx) as requested:
        thread = threading.Thread(target=close)
        thread.start()
        status, stopped = snap._supervise(
            [sys.executable, "-c", "import time; time.sleep(3)"], close_requested=requested
        )
        assert status != 0 and stopped
    thread.join(timeout=5)
    assert completed == [True]


def test_close_never_writes_to_a_symlink_or_regular_file(store, tmp_path):
    tx = installed_snap(store, "a", "browser", "1")
    for name in ("app-test1", "app-test2"):
        (snap_application._directory(store, tx) / name).mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"keep")
    directory = snap_application._directory(store, tx)
    (directory / "app-test1" / "close").symlink_to(unrelated)
    collision = directory / "app-test2" / "close"
    collision.write_bytes(b"another")
    snap_application._request_close(store, tx)
    assert unrelated.read_bytes() == b"keep"
    assert collision.read_bytes() == b"another"

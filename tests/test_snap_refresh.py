"""Updating one snap from the store, including permission and recovery paths."""

import hashlib
import os
import signal
import subprocess
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from subprocess import run
from types import SimpleNamespace

import pytest
from test_snap_updates import installed_snap

from multipak import snap, snap_refresh, snap_store
from multipak.engine import Engine
from multipak.snap_meta import parse

OLD = """name: demo
version: '1.0'
apps:
  demo:
    command: bin/demo
  worker:
    command: bin/worker
    daemon: simple
"""
NEW = OLD.replace("1.0", "2.0")


def texts(old, new):
    return [item["text"] for item in snap_refresh.new_permissions(old, new)]


def test_permission_changes_are_reported_by_path_and_access():
    old = parse(
        OLD
        + """
plugs:
  config:
    interface: system-files
    read: [/etc/demo]
  notes:
    interface: personal-files
    write: [$HOME/.demo]
"""
    )
    new = parse(
        OLD
        + """
confinement: classic
plugs:
  config:
    interface: system-files
    write: [/etc/demo]
  notes:
    interface: personal-files
    read: [$HOME/.demo, $HOME/.new]
  home:
"""
    )
    assert texts(old, new) == [
        "Run without confinement (classic), with full access to the system",
        "Read ~/.new",
        "Write to /etc/demo",
        "Read and change the files in your home folder (home)",
    ]
    # Writing that becomes reading is no new permission, but reading that becomes
    # writing is; the same plugs ask for nothing.
    assert texts(new, old) == ["Write to ~/.demo"]
    assert snap_refresh.new_permissions(new, new) == ()


def test_a_new_interface_counts_as_connected():
    old = parse(OLD + "plugs:\n  home:\n")
    new = parse(OLD + "plugs:\n  home:\n  camera:\n  my-plug:\n    interface: odd-thing\n")
    assert texts(old, new) == ["Use the camera (camera)", "Use the odd-thing interface"]
    # A system-files plug without new paths still shows its interface.
    assert texts(parse(OLD), parse(OLD + "plugs:\n  network-bind:\n")) == [
        "Accept network connections (network-bind)"
    ]


SERVICES = """name: demo
version: '1.0'
apps:
  demo:
    command: bin/demo
"""


def test_new_services_timers_and_users_are_reported():
    new = parse(
        SERVICES
        + """  worker:
    command: bin/worker
    daemon: simple
  cleaner:
    command: bin/clean
    daemon: oneshot
    daemon-scope: user
    timer: '00:00-24:00/2'
  broken:
    command: bin/broken
    daemon: dbus
system-usernames:
  snap_daemon: shared
"""
    )
    assert texts(parse(SERVICES), new) == [
        "Start a background service: worker",
        "Start a background service: cleaner",
        "Start the service cleaner automatically on a schedule (00:00-24:00/2)",
        "Create the system user snap_daemon",
    ]
    # A timer added to a service that already ran is new; the service itself is not.
    timed = parse(OLD + "    timer: '23:00'\n")
    assert texts(parse(OLD), timed) == [
        "Start the service worker automatically on a schedule (23:00)"
    ]
    assert texts(timed, parse(OLD.replace("1.0", "2.0") + "    timer: '22:00'\n")) == []


def sockets(*listens):
    lines = "".join(
        f"      s{number}:\n        listen-stream: '{listen}'\n"
        for number, listen in enumerate(listens)
    )
    return parse(OLD + "    sockets:\n" + lines)


def test_ports_open_to_the_network_are_told_apart_from_local_ones():
    items = snap_refresh.new_permissions(
        parse(OLD), sockets("8080", "127.0.0.1:9000", "[::1]:9001", "$SNAP_DATA/run.sock")
    )
    assert items == (
        {
            "text": "Accept connections from other computers on the network (port 8080)",
            "warning": True,
        },
        {
            "text": "Create the local socket $SNAP_DATA/run.sock for the service worker",
            "warning": False,
        },
        {
            "text": "Accept connections only from this computer (port 9000)",
            "warning": False,
        },
        {
            "text": "Accept connections only from this computer (port 9001)",
            "warning": False,
        },
    )
    assert texts(parse(OLD), sockets("0.0.0.0:8443")) == [
        "Accept connections from other computers on the network (address 0.0.0.0, port 8443)"
    ]
    # The same port, now open to the network instead of only to this computer.
    assert texts(sockets("127.0.0.1:8080"), sockets("8080")) == [
        "Accept connections from other computers on the network (port 8080)"
    ]
    assert texts(sockets("8080"), sockets("8080")) == []


def test_new_session_bus_names_and_layouts_are_reported():
    new = parse(
        SERVICES
        + """  tray:
    command: bin/tray
    daemon: dbus
    daemon-scope: user
    bus-name: org.example.Tray
slots:
  owned:
    interface: dbus
    bus: session
    name: org.example.Demo
layout:
  /usr/share/demo:
    bind: $SNAP/usr/share/demo
  /etc/demo.conf:
    symlink: $SNAP_DATA/demo.conf
  /var/cache/demo:
    type: tmpfs
"""
    )
    assert texts(parse(SERVICES), new) == [
        "Start a background service: tray",
        "Use the D-Bus name org.example.Demo in your session",
        "Use the D-Bus name org.example.Tray in your session",
        "Replace /etc/demo.conf with a link to $SNAP_DATA/demo.conf inside the app",
        "Replace /usr/share/demo with $SNAP/usr/share/demo inside the app",
        "Replace /var/cache/demo with an empty temporary folder inside the app",
    ]
    assert texts(new, new) == []


def test_the_title_counts_the_permissions():
    one = ({"text": "a", "warning": False},)
    assert snap_refresh.permissions_title("Demo", one) == (
        "The new version of Demo needs 1 new permission:"
    )
    assert snap_refresh.permissions_title("Demo", one * 3) == (
        "The new version of Demo needs 3 new permissions:"
    )


def test_the_title_and_the_items_are_translated():
    from multipak.localization import activate_language

    activate_language("pt_BR")
    try:
        assert snap_refresh.permissions_title("Demo", ({},) * 3) == (
            "A nova versão de Demo exige 3 novas autorizações:"
        )
        assert texts(parse(OLD), sockets("8080")) == [
            "Aceitar conexões de outros computadores da rede (porta 8080)"
        ]
    finally:
        activate_language("en")


def test_the_network_ports_show_when_installing():
    pytest.importorskip("PySide6.QtCore")
    from multipak.kirigami.main import Welcome
    from multipak.models import Package
    from multipak.snap_meta import limitations, network_ports

    info = sockets("8080", "127.0.0.1:9000", "192.168.0.2:9100")
    expected = [
        "This application accepts connections from other computers on the network (port 8080).",
        "This application accepts connections from other computers on the network "
        "(address 192.168.0.2, port 9100).",
    ]
    assert network_ports(info) == expected
    # Shown on its own above the folded alerts, and kept with the installed snap.
    welcome = Welcome(package=Package(Path("a.snap"), "snap", "demo", "1", "amd64", snap=info))
    assert welcome.snapNetworkWarning == "<p>" + "<br>".join(expected) + "</p>"
    assert all(sentence in limitations(info) for sentence in expected)
    assert network_ports(sockets("127.0.0.1:9000")) == []


@pytest.fixture
def prepared(store, tmp_path, monkeypatch):
    tx = installed_snap(store, "a", "demo", "11", channel="latest/beta")
    tx.snap_runtime = ["core22=7"]
    tx.snap_confinement = "strict"
    for name, revision in (("core22", "7"), ("core24", "8")):
        meta = store.snap_runtime_directory() / name / revision / "meta"
        meta.mkdir(parents=True)
        (meta / "snap.yaml").write_text(f"name: {name}\n")
    store.save(tx)
    directory = store.snap_directory(tx.id)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(snap, "_home", lambda: home)
    root = directory / "root"
    (root / "meta/hooks").mkdir(parents=True)
    (root / "meta/snap.yaml").write_text(OLD)
    (root / "meta/hooks/pre-refresh").touch()
    events = []
    remote = SimpleNamespace(text=NEW)
    # Revision 11's worker runs until stopped; a start goes through the service
    # lock, as run_service does, so it fails while the updater holds services back.
    active = {"11": True}

    for source, _ in snap_refresh._data_paths(directory, tx, "15"):
        source.mkdir(parents=True)
        (source / "data.txt").write_text("old data")

    class CopyRunner:
        def run(self, argv, **kwargs):
            assert argv[0] == "cp"
            run(argv, check=True)

    class Services:
        def __init__(self, tx, *args, **kwargs):
            self.tx, self.revision = tx, tx.snap_revision

        def running(self):
            return ["worker"] if active.get(self.revision) else []

        def stop_all(self):
            events.append(("stop", self.revision))
            active[self.revision] = False

        def _launch(self):
            with snap.service_lock(store, self.tx):
                active[self.revision] = True

        def start(self, services):
            events.append(("restore", self.revision, services))
            if services:
                self._launch()

        def start_enabled(self):
            events.append(("start", self.revision))
            self._launch()

    def release(name, *, channel):
        events.append(("lookup", name, channel))
        return SimpleNamespace(revision="15", resources=())

    def download(release, target, **kwargs):
        events.append(("download", release.revision))
        target.write_bytes(remote.text.encode())

    def extract(runner, source, target):
        (target / "meta/hooks").mkdir(parents=True)
        (target / "meta/snap.yaml").write_bytes(source.read_bytes())
        (target / "meta/hooks/post-refresh").touch()

    def run_hook(engine, tx, name):
        events.append((name, tx.snap_revision))
        if name == "post-refresh":
            state = snap.snap_ctl.state(directory)
            state.change_config(lambda values: values.update({"new": True}))

    monkeypatch.setattr(snap_refresh, "Services", Services)
    monkeypatch.setattr(snap_store, "release", release)
    monkeypatch.setattr(snap_store, "download", download)
    monkeypatch.setattr(snap, "read_info", lambda runner, archive: parse(archive.read_text()))
    monkeypatch.setattr(snap, "extract", extract)
    monkeypatch.setattr(snap, "run_hook", run_hook)
    monkeypatch.setattr(snap, "ensure_runtime", lambda *a: ["core24=8"])
    monkeypatch.setattr(snap, "missing_service_features", lambda info: [])
    engine = Engine(store, threading.Event())
    engine.runner = CopyRunner()
    return SimpleNamespace(
        tx=tx, directory=directory, engine=engine, events=events, remote=remote, active=active
    )


def test_refresh_follows_the_channel_and_preserves_revision_data(prepared, store):
    p = prepared
    result = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    # Everything that can fail on its own comes first; the services stop only after
    # the pre-refresh script, which runs with them up.
    assert p.events == [
        ("lookup", "demo", "latest/beta"),
        ("download", "15"),
        ("pre-refresh", "11"),
        ("stop", "11"),
        ("post-refresh", "15"),
        ("start", "15"),
    ]
    assert p.active == {"11": False, "15": True}
    assert result.snap_revision == "15" and result.snap_previous_revision == "11"
    assert result.snap_runtime == ["core24=8"] and result.snap_previous_runtime == ["core22=7"]
    assert store.transactions() == [result]
    assert (p.directory / "previous-root/meta/snap.yaml").read_text() == OLD
    assert (p.directory / "root/meta/snap.yaml").read_text() == NEW
    for source, target in snap_refresh._data_paths(p.directory, p.tx, "15"):
        assert (source / "data.txt").read_text() == (target / "data.txt").read_text() == "old data"
    assert not (p.directory / ".refresh").exists()


def test_declining_new_permissions_never_stops_the_services(prepared, store):
    p = prepared
    p.remote.text += "confinement: classic\n"
    requests = []
    assert snap_refresh.refresh(p.engine, p.tx, "15", lambda *args: requests.append(args)) is None
    assert requests == [
        (
            "demo",
            (
                {
                    "text": "Run without confinement (classic), with full access to the system",
                    "warning": False,
                },
            ),
        )
    ]
    assert p.events == [("lookup", "demo", "latest/beta"), ("download", "15")]
    assert p.active == {"11": True}
    assert store.transactions() == [p.tx]
    assert not (p.directory / "previous-root").exists()
    assert not (p.directory / ".refresh").exists()
    assert not list(p.directory.glob(".refresh-*"))


def test_failed_post_refresh_restores_files_record_and_services(prepared, store, monkeypatch):
    p = prepared
    previous_root = p.directory / "previous-root"
    previous_root.mkdir()
    (previous_root / "marker").write_text("earlier backup")
    prior_hook = snap.run_hook

    def run_hook(engine, tx, name):
        prior_hook(engine, tx, name)
        if name == "post-refresh":
            raise RuntimeError("hook failed")

    monkeypatch.setattr(snap, "run_hook", run_hook)
    with pytest.raises(RuntimeError, match="hook failed"):
        snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert store.transactions()[0].snap_revision == "11"
    assert snap.snap_ctl.state(p.directory).config() == {}
    assert (p.directory / "root/meta/snap.yaml").read_text() == OLD
    assert (previous_root / "marker").read_text() == "earlier backup"
    assert p.events[-3:] == [("post-refresh", "15"), ("stop", "15"), ("restore", "11", ["worker"])]
    # The old revision's services came back through the service lock.
    assert p.active == {"11": True, "15": False}
    for source, target in snap_refresh._data_paths(p.directory, p.tx, "15"):
        assert (source / "data.txt").read_text() == "old data"
        assert not target.exists()
    assert not (p.directory / ".refresh").exists()


def test_lock_refuses_refresh_while_snap_application_is_running(prepared, store):
    p = prepared
    with snap.application_lock(store, p.tx), pytest.raises(RuntimeError, match="Close the snap"):
        snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert p.events == []


def test_refused_metadata_does_not_ask_for_permission_or_activate(prepared, store):
    p = prepared
    p.remote.text += "plugs:\n  docker-support:\n"
    with pytest.raises(ValueError, match="system-level permissions"):
        snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("refused"))
    assert p.events == [("lookup", "demo", "latest/beta"), ("download", "15")]
    assert store.transactions()[0].snap_revision == "11"
    assert (p.directory / "root/meta/snap.yaml").read_text() == OLD


def test_revert_is_offline_and_keeps_each_revisions_data(prepared, store, monkeypatch):
    p = prepared
    active = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    for _, target in snap_refresh._data_paths(p.directory, p.tx, "15"):
        (target / "data.txt").write_text("changed on revision 15")

    p.events.clear()
    monkeypatch.setattr(snap_store, "release", lambda *a, **k: pytest.fail("no store on revert"))
    monkeypatch.setattr(
        snap_store, "download", lambda *a, **k: pytest.fail("no download on revert")
    )
    reverted = snap_refresh.revert(p.engine, active)
    assert p.events == [("stop", "15"), ("start", "11")]
    assert reverted.snap_revision == "11" and reverted.snap_previous_revision == "15"
    assert reverted.snap_runtime == ["core22=7"]
    assert reverted.snap_previous_runtime == ["core24=8"]
    assert reverted.snap_channel == "latest/beta"
    assert store.transactions() == [reverted]
    assert (p.directory / "root/meta/snap.yaml").read_text() == OLD
    assert (p.directory / "previous-root/meta/snap.yaml").read_text() == NEW

    again = snap_refresh.revert(p.engine, reverted)
    assert again.snap_revision == "15" and again.snap_previous_revision == "11"
    for source, target in snap_refresh._data_paths(p.directory, p.tx, "15"):
        assert (source / "data.txt").read_text() == "old data"
        assert (target / "data.txt").read_text() == "changed on revision 15"


def test_missing_saved_revision_does_not_stop_services(prepared, monkeypatch):
    p = prepared
    active = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    (p.directory / "previous-manifest.json").unlink()
    p.events.clear()
    with pytest.raises(ValueError, match="previous snap revision is unavailable"):
        snap_refresh.revert(p.engine, active)
    assert p.events == []


def test_revert_restores_active_revision_when_services_fail(prepared, store, monkeypatch):
    p = prepared
    active = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    base = snap_refresh.Services

    class FailingServices(base):
        def start_enabled(self):
            if self.revision == "11":
                raise RuntimeError("previous service could not start")
            super().start_enabled()

    monkeypatch.setattr(snap_refresh, "Services", FailingServices)
    p.events.clear()
    with pytest.raises(RuntimeError, match="previous service could not start"):
        snap_refresh.revert(p.engine, active)
    assert p.events == [("stop", "15"), ("stop", "11"), ("restore", "15", ["worker"])]
    assert store.transactions()[0].snap_revision == "15"
    assert (p.directory / "root/meta/snap.yaml").read_text() == NEW
    assert (p.directory / "previous-root/meta/snap.yaml").read_text() == OLD
    assert not (p.directory / ".refresh").exists()


def test_a_failed_pre_refresh_script_leaves_the_services_running(prepared, store, monkeypatch):
    p = prepared
    prior_hook = snap.run_hook

    def run_hook(engine, tx, name):
        prior_hook(engine, tx, name)
        if name == "pre-refresh":
            snap.snap_ctl.state(p.directory).change_config(lambda v: v.update({"half": 1}))
            raise RuntimeError("pre-refresh failed")

    monkeypatch.setattr(snap, "run_hook", run_hook)
    with pytest.raises(RuntimeError, match="pre-refresh failed"):
        snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert ("stop", "11") not in p.events
    assert p.active == {"11": True}
    assert snap.snap_ctl.state(p.directory).config() == {}
    assert store.transactions() == [p.tx]
    assert (p.directory / "root/meta/snap.yaml").read_text() == OLD
    assert not (p.directory / ".refresh").exists()


def test_no_other_service_starts_while_the_revision_changes(prepared, store, monkeypatch):
    p = prepared
    attempts = []
    prior_hook = snap.run_hook

    def run_hook(engine, tx, name):
        prior_hook(engine, tx, name)
        # A timer, a socket or snapctl from elsewhere trying to start a service.
        with pytest.raises(RuntimeError, match="Wait for the snap update"):
            with snap.service_lock(store, tx):
                pass
        attempts.append(name)

    monkeypatch.setattr(snap, "run_hook", run_hook)
    result = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert attempts == ["pre-refresh", "post-refresh"]
    assert result.snap_revision == "15" and p.active["15"]
    with snap.service_lock(store, result):
        pass


def test_a_service_that_took_its_lock_first_holds_the_switch_back(prepared, store, monkeypatch):
    p = prepared
    monkeypatch.setattr(snap, "SERVICE_STOP_WAIT", 0.3)
    with snap.service_lock(store, p.tx):
        with pytest.raises(snap.ProcessesStillRunning):
            snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert store.transactions() == [p.tx]
    assert (p.directory / "root/meta/snap.yaml").read_text() == OLD
    # Never a second copy beside the one still running.
    assert p.events[-1] == ("stop", "11") and p.active == {"11": False}
    assert not (p.directory / ".refresh").exists()


def _published(release, text):
    data = text.encode()
    release.size = len(data)
    release.sha3_384 = hashlib.sha3_384(data).hexdigest()
    return release


def test_an_app_opened_during_the_download_keeps_the_download(prepared, store, monkeypatch):
    p = prepared
    published = _published(SimpleNamespace(revision="15", resources=()), NEW)
    monkeypatch.setattr(snap_store, "release", lambda name, *, channel: published)
    opened = ExitStack()
    prior_download = snap_store.download

    def download(release, target, **kwargs):
        prior_download(release, target, **kwargs)
        opened.enter_context(snap.application_lock(store, p.tx))

    monkeypatch.setattr(snap_store, "download", download)
    with opened, pytest.raises(snap.ApplicationRunning):
        snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert p.events == [("download", "15")] and p.active == {"11": True}
    assert (p.directory / snap_refresh.DOWNLOADS / "15.snap").read_text() == NEW
    assert not (p.directory / ".refresh").exists()

    result = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert result.snap_revision == "15"
    assert p.events.count(("download", "15")) == 1
    assert not (p.directory / snap_refresh.DOWNLOADS).exists()


def test_a_damaged_kept_download_is_fetched_again(prepared, store, monkeypatch):
    p = prepared
    published = _published(SimpleNamespace(revision="15", resources=()), NEW)
    monkeypatch.setattr(snap_store, "release", lambda name, *, channel: published)
    kept = p.directory / snap_refresh.DOWNLOADS / "15.snap"
    kept.parent.mkdir()
    kept.write_text(NEW.replace("2.0", "6.6"))
    snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert p.events.count(("download", "15")) == 1
    assert (p.directory / "root/meta/snap.yaml").read_text() == NEW


# A launcher, as far as the update can tell: the application lock, its snapctl
# channel and its close channel. With "child", it also starts an app process that
# inherits the lock, as the sandbox does, and outlives the launcher.
LAUNCHER = """
import subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace
from multipak import snap, snap_application, snap_ctl
from multipak.store import Store

store = Store(Path(sys.argv[1]), Path(sys.argv[2]))
tx = SimpleNamespace(id=sys.argv[3])
with snap.application_lock(store, tx) as held:
    channel = snap_ctl.channel_directory(store.snap_directory(tx.id))
    with snap_ctl.Server(channel, None, None), snap_application.close_channel(store, tx):
        if sys.argv[4] == "child":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"], pass_fds=(held,)
            )
            print(child.pid, flush=True)
        else:
            print(0, flush=True)
        time.sleep(60)
"""


def _killed_launcher(store, tx, mode):
    environment = dict(os.environ, PYTHONPATH=str(Path(snap.__file__).parents[1]))
    launcher = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, str(store.root), str(store.applications), tx.id, mode],
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    child = int(launcher.stdout.readline())
    launcher.send_signal(signal.SIGKILL)
    launcher.wait()
    launcher.stdout.close()
    return child


def test_a_killed_launcher_leaves_folders_that_do_not_block_the_update(prepared, store):
    p = prepared
    _killed_launcher(store, p.tx, "alone")
    assert len(list((p.directory / "channels").iterdir())) == 1
    assert len(list((p.directory / "running-applications").iterdir())) == 1
    result = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert result.snap_revision == "15"
    assert not any((p.directory / "channels").iterdir())
    assert not any((p.directory / "running-applications").iterdir())


def test_an_app_process_that_outlived_its_launcher_blocks_the_update(prepared, store):
    p = prepared
    child = _killed_launcher(store, p.tx, "child")
    try:
        with pytest.raises(snap.ApplicationRunning):
            snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
        assert p.events == []
    finally:
        os.kill(child, signal.SIGKILL)
    # Once that process ends, nothing is left to wait for.
    deadline = 50
    while deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        deadline -= 1
        threading.Event().wait(0.1)
    result = snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert result.snap_revision == "15"


def test_a_live_snapctl_channel_without_a_lock_keeps_the_services_stopped(
    prepared, store, monkeypatch
):
    p = prepared
    # For example, a service started by an earlier MultiPak that did not take the
    # service lock, and whose process survived the stop.
    with snap.snap_ctl.Server(snap.snap_ctl.channel_directory(p.directory), None, None):
        with pytest.raises(snap.ProcessesStillRunning):
            snap_refresh.refresh(p.engine, p.tx, "15", lambda *a: pytest.fail("no prompt"))
    assert store.transactions() == [p.tx]
    assert p.active == {"11": False}
    assert not (p.directory / ".refresh").exists()


def test_a_service_process_that_outlived_its_service_keeps_the_lock(store):
    from test_snap_updates import installed_snap

    tx = installed_snap(store, "b", "server", "3")
    directory = store.snap_directory(tx.id)
    directory.mkdir(parents=True)
    stage = directory / ".refresh"
    with snap.service_lock(store, tx) as held:
        # As run_service starts the bubble: the lock after the sockets, here 3.
        survivor = snap._SpawnedWithSockets(
            [sys.executable, "-c", "import os, time; os.fstat(3); time.sleep(60)"], [], held
        )
    try:
        stage.mkdir()
        gate = snap.ServiceGate(store, tx, stage, wait=0.3)
        with pytest.raises(snap.ProcessesStillRunning, match="still running"):
            gate.close()
    finally:
        survivor.send_signal(signal.SIGKILL)
        survivor.wait()
    gate.close()
    gate.open()
    assert (stage / snap.SERVICES_ALLOWED).is_file()


def test_the_app_receives_the_application_lock(store):
    from test_snap_updates import installed_snap

    tx = installed_snap(store, "c", "editor", "4")
    store.snap_directory(tx.id).mkdir(parents=True)
    with snap.application_lock(store, tx) as held:
        # As 3, the number the launcher forwards to the computer.
        status, _ = snap._supervise([sys.executable, "-c", "import os; os.fstat(3)"], keep=held)
    assert status == 0


def test_a_service_receives_the_lifeline_after_its_sockets_and_lock(store, monkeypatch):
    import socket as sockets_module

    from multipak import host

    tx_lock = os.open(os.devnull, os.O_RDONLY)
    listener = sockets_module.socket(sockets_module.AF_UNIX, sockets_module.SOCK_STREAM)
    monkeypatch.setattr(host, "_lifeline", None)
    life = host.lifeline()
    try:
        # As run_service forwards them: socket 3, lock 4, lifeline 5, the number
        # host.command names in MULTIPAK_LIFELINE.
        check = (
            "import os, stat, sys; "
            "sys.exit(0 if stat.S_ISSOCK(os.fstat(3).st_mode) and stat.S_ISCHR(os.fstat(4).st_mode)"
            " and stat.S_ISFIFO(os.fstat(5).st_mode) else 1)"
        )
        child = snap._SpawnedWithSockets([sys.executable, "-c", check], [listener], tx_lock, life)
        assert child.wait() == 0
        monkeypatch.setattr(host, "is_flatpak", lambda: True)
        command = host.command(
            ["bwrap"], supervise=True, forward_fds=range(3, 5), lifeline=3 + 1 + 1
        )
        assert f"--env={host.LIFELINE_VARIABLE}=5" in command
    finally:
        os.close(tx_lock)
        listener.close()
        os.close(life)
        os.close(host._lifeline[1])
        monkeypatch.setattr(host, "_lifeline", None)


def test_only_channels_nobody_answers_are_removed(tmp_path):
    from multipak import snap_ctl

    root = tmp_path / "channels"
    live = snap_ctl.channel_directory(tmp_path)
    with snap_ctl.Server(live, None, None):
        (root / "starting").mkdir()
        (root / "abandoned").mkdir()
        os.utime(root / "abandoned", (0, 0))
        (root / "ended").mkdir()
        os.mkfifo(root / "ended/requests")
        (root / "invalid").mkdir()
        (root / "invalid/requests").write_text("not a pipe")
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "link").symlink_to(outside)
        assert snap_ctl.clear_orphan_channels(tmp_path) == sorted([live.name, "starting"])
        assert sorted(path.name for path in root.iterdir()) == sorted([live.name, "starting"])
    assert outside.is_dir()

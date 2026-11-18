"""The Application Manager's update list: containers and AppImages, one or all."""

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_manager_repairs import InlineWorker  # noqa: E402

from multipak import appimage_updates  # noqa: E402
from multipak.appimage_updates import Check, LinkMismatch, Prepared, Release  # noqa: E402
from multipak.kirigami.manager import Manager  # noqa: E402
from multipak.models import ExistingEnvironment, Transaction  # noqa: E402
from multipak.package_updates import Check as PackageCheck  # noqa: E402
from multipak.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def application():
    # Other tests need a full QApplication; only one application object may exist.
    return QCoreApplication.instance() or QApplication([])


def settle():
    for _ in range(5):
        QCoreApplication.processEvents()


@pytest.fixture
def manager(tmp_path, application, monkeypatch):
    store = Store(tmp_path / "data", tmp_path / "applications")
    for token, name in (("a", "Alpha"), ("b", "Beta")):
        tx = Transaction(
            token * 32,
            "",
            "",
            name,
            "1.0",
            "appimage",
            token * 64,
            method="appimage",
            status="installed",
            appimage_mode="extracted",
        )
        store.save(tx)
        store.appimage_directory(tx.id).mkdir(parents=True)
    checks = {
        "a" * 32: Check("available", "https://github.com/o/a", Release("https://f/a", "a", "2.0")),
        "b" * 32: Check("no-source"),
    }
    monkeypatch.setattr(appimage_updates, "check", lambda engine, force=False: dict(checks))
    monkeypatch.setattr(appimage_updates, "cached", lambda store: {})
    monkeypatch.setattr(appimage_updates, "running", lambda store, tx, *a: [])
    controller = Manager(
        store,
        worker_factory=InlineWorker,
        discoverer=lambda runner: [],
        package_checker=lambda runner, name, kind, packages, refresh=False: {
            package: PackageCheck("available", "1.0", "2.0") for package in packages
        },
        container_lister=lambda runner: None,
    )
    signals = []
    for name in (
        "updateLinkRequested",
        "updateClosingRequested",
        "updateDependenciesRequested",
        "groupUpdateRequested",
    ):
        getattr(controller, name).connect(lambda name=name: signals.append(name))
    controller.signals = signals
    controller.openList("update")
    # Nothing is checked until asked.
    assert [row["note"] for row in controller.rows] == ["Updates not checked yet."] * 2
    controller.select(0)
    assert not controller.canAct
    controller.refresh()
    return controller


def row(manager, title):
    return next(item for item in manager.rows if item["title"] == title)


def index(manager, title):
    return next(position for position, item in enumerate(manager.rows) if item["title"] == title)


def bases(manager):
    """The bases and content behind the list: "Update all" updates them, without rows."""
    from multipak import snap_updates

    return {
        item.name: manager._snap_update_row(item)
        for item in manager._items
        if isinstance(item, snap_updates.Runtime)
    }


def prepared(packages=(), link=""):
    return Prepared("a" * 32, None, "Alpha", "2.0", link, packages, "• Library libnew.so.1")


def test_rows_show_what_each_check_found(manager):
    notes = [row["note"] for row in manager.rows]
    assert notes == [
        "Update available: 2.0",
        "The application does not indicate its update address.",
    ]
    assert [row["title"] for row in manager.rows] == ["Alpha", "Beta"]
    manager.select(0)
    assert manager.canAct


def test_an_update_downloads_and_installs(manager, monkeypatch):
    installed = []
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared())

    def install(engine, value):
        installed.append(value)
        tx = manager.store.transactions()[0]
        tx.package_version = "2.0"
        return tx

    monkeypatch.setattr(appimage_updates, "install", install)
    manager.select(0)
    manager.updateSelected()
    settle()
    assert installed and manager.status == "Alpha was updated."
    assert row(manager, "Alpha")["note"] == "Up to date"
    assert manager.signals == []


def test_new_dependencies_are_confirmed_and_going_back_discards(manager, monkeypatch):
    discarded = []
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared(("libnew",)))
    monkeypatch.setattr(appimage_updates, "discard", lambda store, tx_id: discarded.append(tx_id))
    monkeypatch.setattr(appimage_updates, "install", lambda *a: pytest.fail("not confirmed"))
    manager.select(0)
    manager.updateSelected()
    assert manager.signals == ["updateDependenciesRequested"]
    assert "libnew.so.1" in manager.updateDependencies
    manager.cancelAppImageUpdate()
    assert discarded == ["a" * 32] and manager.updateName == ""


def test_a_running_application_must_be_closed_first(manager, monkeypatch):
    state = {"running": True}
    monkeypatch.setattr(
        appimage_updates, "running", lambda store, tx, *a: [7] if state["running"] else []
    )
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared(("libnew",)))

    def close(store, tx):
        state["running"] = False
        return True

    monkeypatch.setattr(appimage_updates, "close", close)
    manager.select(0)
    manager.updateSelected()
    assert manager.signals == ["updateClosingRequested"] and manager.updateName == "Alpha"
    manager.closeUpdatedApplication()
    settle()
    assert manager.signals[-1] == "updateDependenciesRequested"


def test_an_application_that_does_not_close_is_asked_again(manager, monkeypatch):
    monkeypatch.setattr(appimage_updates, "running", lambda store, tx, *a: [7])
    monkeypatch.setattr(appimage_updates, "close", lambda store, tx: False)
    manager.select(0)
    manager.updateSelected()
    manager.closeUpdatedApplication()
    assert manager.signals == ["updateClosingRequested", "updateClosingRequested"]
    assert "did not close" in manager.status


def test_without_an_address_the_link_is_asked_and_checked(manager, monkeypatch):
    def check_link(engine, identifier, text):
        if "wrong" in text:
            raise appimage_updates.UpdateError("The address was not found.")
        return Check("available", text, Release("https://f/b", "b", "3"), link=text)

    downloads = []
    monkeypatch.setattr(appimage_updates, "check_link", check_link)

    def download(engine, identifier, release, link=""):
        downloads.append(link)
        if len(downloads) == 1:
            raise LinkMismatch
        return prepared(("libnew",), link)

    monkeypatch.setattr(appimage_updates, "download", download)
    manager.select(1)
    assert manager.canAct
    manager.updateSelected()
    assert manager.signals == ["updateLinkRequested"] and manager.updateLinkError == ""
    manager.submitUpdateLink("https://wrong.example/x.AppImage")
    assert manager.updateLinkError == "The address was not found."
    manager.submitUpdateLink("https://github.com/o/b")
    settle()
    # The download showed another application: the link is asked for again.
    assert manager.updateLinkError == str(LinkMismatch())
    assert manager.signals.count("updateLinkRequested") == 3
    manager.submitUpdateLink("https://github.com/o/right")
    settle()
    assert downloads[-1] == "https://github.com/o/right"
    assert manager.signals[-1] == "updateDependenciesRequested"


def test_only_checking_consults_the_servers(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(
        appimage_updates, "check", lambda engine, force=False: calls.append(force) or {}
    )
    manager.openList("update")
    assert calls == []
    manager.refresh()
    assert calls == [True]


def test_recent_results_are_shown_without_checking(manager, monkeypatch):
    monkeypatch.setattr(appimage_updates, "check", lambda *a, **k: pytest.fail("no network"))
    monkeypatch.setattr(appimage_updates, "cached", lambda store: {"a" * 32: Check("current")})
    manager.openList("update")
    # What still has to be checked comes before what is up to date.
    assert [(item["title"], item["note"]) for item in manager.rows] == [
        ("Beta", "Updates not checked yet."),
        ("Alpha", "Up to date"),
    ]


@pytest.fixture
def mixed(manager, monkeypatch):
    """The list with two containers above the AppImages."""
    environments = [
        ExistingEnvironment("MultiPak-Ubuntu-24.04", "u" * 64, "ubuntu", "24.04"),
        ExistingEnvironment("MultiPak-Fedora-42", "f" * 64, "fedora", "42"),
    ]
    manager.discoverer = lambda runner: list(environments)
    manager.store.save(
        Transaction(
            "c" * 32,
            "MultiPak-Fedora-42",
            "",
            "Gimp",
            "3.0",
            "rpm",
            "c" * 64,
            method="distrobox",
            status="installed",
        )
    )
    updated = []
    manager.updater = lambda engine, name: updated.append(name)
    manager.updated = updated
    manager.refresh()
    return manager


def test_the_list_is_grouped(mixed):
    rows = mixed.rows
    # A container with applications is shown as its applications, grouped by what
    # their checks found; a container without any is not an application to update.
    assert [item["title"] for item in rows] == ["Alpha", "Gimp", "Beta"]
    assert [item["section"] for item in rows] == ["With updates", "", "No update source"]
    assert [item["kind"] for item in rows] == ["appimage", "container-app", "appimage"]
    assert rows[1]["subtitle"].endswith("· RPM")
    assert rows[1]["detail"] == "MultiPak-Fedora-42"
    assert rows[1]["note"] == "Update available: 2.0"
    assert mixed.updateCount == 2


def test_a_new_container_with_an_old_name_does_not_take_its_applications(mixed):
    # Gimp's container was deleted and another one was created with the same name.
    gimp = next(tx for tx in mixed.store.transactions() if tx.package_name == "Gimp")
    from dataclasses import replace

    mixed.store.save(replace(gimp, container_id="9" * 64))
    checked = []
    mixed.package_checker = lambda runner, name, kind, packages, refresh=False: (
        checked.append(name) or {}
    )
    mixed.container_lister = lambda runner: {"f" * 64: {"MultiPak-Fedora-42"}}
    mixed.refresh()
    assert "Gimp" not in [item["title"] for item in mixed.rows]
    # Nor is it checked inside the new container.
    assert checked == []
    # Its own container (same ID) shows it again.
    mixed.store.save(replace(gimp, container_id="f" * 64))
    mixed.refresh()
    assert "Gimp" in [item["title"] for item in mixed.rows]
    assert checked == ["MultiPak-Fedora-42"]


def test_an_application_alone_updates_its_container_at_once(mixed):
    mixed.select(index(mixed, "Gimp"))
    assert mixed.canAct
    mixed.updateSelected()
    settle()
    assert mixed.updated == ["MultiPak-Fedora-42"]
    assert "groupUpdateRequested" not in mixed.signals
    # Updated: it moves to the applications that are up to date.
    assert row(mixed, "Gimp")["note"] == "Up to date"
    assert row(mixed, "Gimp")["section"] == "Already up to date"


def test_an_application_sharing_its_container_asks_first(mixed):
    mixed.store.save(
        Transaction(
            "d" * 32,
            "MultiPak-Fedora-42",
            "",
            "Inkscape",
            "1.4",
            "rpm",
            "d" * 64,
            method="distrobox",
            status="installed",
        )
    )
    mixed.refresh()
    assert [item["title"] for item in mixed.rows[1:3]] == ["Gimp", "Inkscape"]
    mixed.select(1)
    mixed.updateSelected()
    assert mixed.signals[-1] == "groupUpdateRequested"
    assert mixed.groupUpdateName == "Gimp"
    assert mixed.groupUpdateOthers == "• Inkscape"
    assert mixed.updated == []
    mixed.confirmGroupUpdate()
    settle()
    assert mixed.updated == ["MultiPak-Fedora-42"]


def test_update_all_goes_through_everything(mixed, monkeypatch):
    installed = []
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: prepared())

    def install(engine, value):
        installed.append(value.transaction_id)
        return next(tx for tx in mixed.store.transactions() if tx.id == value.transaction_id)

    monkeypatch.setattr(appimage_updates, "install", install)
    assert mixed.canUpdateAll
    mixed.updateAll()
    settle()
    # The container without applications is not on the list, so it is not updated.
    assert mixed.updated == ["MultiPak-Fedora-42"]
    assert installed == ["a" * 32]
    assert not mixed.working and mixed.signals == []
    assert "Updated: Alpha, Gimp" in mixed.status
    # Beta needs its address, which "Update all" does not ask for.
    assert "Beta" in mixed.status.split("\n\n")[-1]


def test_update_all_asks_to_close_and_goes_on_when_skipped(mixed, monkeypatch):
    monkeypatch.setattr(appimage_updates, "running", lambda store, tx, *a: [7])
    monkeypatch.setattr(appimage_updates, "download", lambda *a, **k: pytest.fail("open"))
    failing = {"MultiPak-Fedora-42"}

    def updater(engine, name):
        if name in failing:
            raise RuntimeError("no network")
        mixed.updated.append(name)

    mixed.updater = updater
    mixed.updateAll()
    settle()
    assert mixed.signals == ["updateClosingRequested"] and mixed.working
    assert not mixed.canClose and not mixed.canAct
    mixed.cancelAppImageUpdate()
    settle()
    assert not mixed.working and mixed.hasError
    assert "Gimp: no network" in mixed.status
    assert "Not updated: Alpha" in mixed.status
    assert mixed.updated == []


def test_the_install_page(manager, monkeypatch):
    started = []
    monkeypatch.setattr(manager, "_start_installer", started.append)
    manager.openInstall()
    assert manager.mode == "install" and manager.rows == []
    assert not manager.validSnapName("https://example.com/app.AppImage")
    manager.installSnap("https://snapcraft.io/hello-world")
    assert started == ["snap:hello-world"]


def test_snap_updates_are_only_checked_on_request(manager, monkeypatch):
    from test_snap_updates import installed_snap, release

    from multipak import snap_updates

    installed_snap(manager.store, "c", "browser", "1", channel="latest/beta", runtime=("core22=7",))
    installed_snap(manager.store, "d", "local", "x1")
    calls = []

    def lookup(name, *, channel):
        calls.append((name, channel))
        return release("8", channel or "latest/stable")

    monkeypatch.setattr(snap_updates.snap_store, "release", lookup)
    manager.openList("update")
    assert calls == []
    assert [
        (item["title"], item["section"], item["note"])
        for item in manager.rows
        if item["title"] in ("browser", "local", "core22")
    ] == [
        ("local", "No update source", "Installed from a local file. Updates are unavailable."),
        ("browser", "", "Updates not checked yet."),
    ]  # Bases and content are not applications: no row, only "Update all".
    manager.refresh()
    assert calls == [("browser", "latest/beta"), ("core22", "")]
    assert row(manager, "browser")["detail"] == "Channel: latest/beta"
    assert row(manager, "browser")["note"] == "Update available: v8 (revision 8)"
    assert manager._snap_checks["runtime:core22"].status == "available"
    assert all(item["title"] != "core22" for item in manager.rows)
    manager.openList("update")
    assert len(calls) == 2
    assert row(manager, "browser")["note"] == "Update available: v8 (revision 8)"

    manager.select(index(manager, "browser"))
    assert manager.canAct
    manager.select(index(manager, "local"))
    assert not manager.canAct
    manager.updateSelected()
    assert not manager.working
    manager._snap_checks.clear()

    monkeypatch.setattr(
        appimage_updates,
        "check",
        lambda *a, **k: {key * 32: Check("current") for key in ("a", "b")},
    )
    manager.updateAll()
    settle()
    assert len(calls) == 2
    assert "Check for updates to include" in manager.status

    for tx in manager.store.transactions():
        if tx.method == "appimage":
            manager.store.remove_directory(tx)
    manager.openList("update")
    assert manager.canUpdateAll


def test_a_snap_update_asks_for_permissions_and_updates_only_the_selected_item(
    manager, monkeypatch
):
    from test_snap_updates import installed_snap, release

    from multipak import snap_refresh, snap_updates

    installed_snap(manager.store, "c", "browser", "7", channel="latest/beta", runtime=("core22=7",))
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda name, *, channel: release("8", channel)
    )
    operation = []

    def refresh(engine, tx, revision, confirm):
        permissions = (
            {"text": "Start a background service: worker", "warning": False},
            {"text": "Accept connections from other computers", "warning": True},
        )
        operation.append((tx.snap_name, revision, confirm(tx.package_name, permissions)))
        tx.snap_revision = revision
        tx.package_version = "v8"
        return tx

    monkeypatch.setattr(snap_refresh, "refresh", refresh)
    messages = []
    manager.snapPermissionsRequested.connect(lambda *request: messages.append(request))
    manager.snapPermissionsRequested.connect(lambda *_: manager.answerSnapPermissions(True))
    manager.refresh()
    manager.select(index(manager, "browser"))
    manager.updateSelected()
    assert operation == [("browser", "8", True)]
    assert messages == [
        (
            "The new version of browser needs 2 new permissions:",
            [
                {"text": "Start a background service: worker", "warning": False},
                {"text": "Accept connections from other computers", "warning": True},
            ],
        )
    ]
    assert manager.status == "browser was updated."
    assert row(manager, "browser")["note"] == "Up to date"


def test_reverting_a_snap_does_not_check_the_store(manager, monkeypatch):
    from test_snap_updates import installed_snap

    from multipak import snap_refresh, snap_updates

    tx = installed_snap(manager.store, "c", "browser", "8", runtime=("core24=8",))
    manager.openList("update")
    manager.select(2)
    assert not manager.hasPreviousSnap
    tx.snap_previous_revision = "7"
    tx.snap_previous_runtime = ["core22=7"]
    manager.store.save(tx)
    manager.openList("update")
    manager.select(2)
    assert manager.hasPreviousSnap
    monkeypatch.setattr(snap_updates.snap_store, "release", lambda *a, **k: pytest.fail("offline"))

    def revert(engine, item):
        item.snap_revision, item.snap_previous_revision = "7", "8"
        item.snap_runtime, item.snap_previous_runtime = ["core22=7"], ["core24=8"]
        engine.store.save(item)
        return item

    monkeypatch.setattr(snap_refresh, "revert", revert)
    manager.revertSelectedSnap()
    assert "revision 7" in manager.status
    assert manager.hasPreviousSnap
    assert manager._items[-1].name == "core22"  # hidden: bases are not applications


@pytest.mark.parametrize("close", [False, True])
def test_update_all_handles_an_open_snap_and_continues_with_the_base(manager, monkeypatch, close):
    from test_snap_updates import installed_snap, release

    from multipak import snap, snap_application, snap_refresh, snap_runtime_updates, snap_updates

    installed_snap(manager.store, "c", "browser", "7", runtime=("core22=7",))
    installed_snap(manager.store, "d", "local", "x1")
    calls = []

    def lookup(name, *, channel):
        calls.append(("check", name))
        return release("8", channel or "latest/stable")

    def refresh(engine, tx, revision, confirm):
        calls.append(("refresh", tx.snap_name))
        if not any(action == "close" for action, _ in calls):
            raise snap.ApplicationRunning(tx)
        tx.snap_revision = revision
        engine.store.save(tx)
        return tx

    def update_runtime(engine, runtime, revision):
        calls.append(("runtime", runtime.name))
        return snap_updates.Runtime(runtime.name, (revision,))

    monkeypatch.setattr(snap_updates.snap_store, "release", lookup)
    monkeypatch.setattr(snap_refresh, "refresh", refresh)
    monkeypatch.setattr(snap_runtime_updates, "update", update_runtime)
    monkeypatch.setattr(
        snap_application, "close", lambda store, tx: calls.append(("close", tx.snap_name)) or True
    )
    monkeypatch.setattr(
        appimage_updates,
        "check",
        lambda *a, **k: {key * 32: Check("current") for key in ("a", "b")},
    )
    manager.refresh()
    assert calls == [("check", "browser"), ("check", "core22")]
    manager.updateAll()
    settle()
    assert manager.working and manager.signals[-1] == "updateClosingRequested"
    assert manager.updateName == "browser"
    if close:
        manager.closeUpdatedApplication()
    else:
        manager.cancelUpdateClosing()
    settle()
    assert not manager.working
    assert calls[:3] == [("check", "browser"), ("check", "core22"), ("refresh", "browser")]
    if close:
        assert calls[3:] == [("close", "browser"), ("refresh", "browser"), ("runtime", "core22")]
        assert manager.status == "Updated: browser, core22"
    else:
        assert calls[3:] == [("runtime", "core22")]
        assert manager.status == "Updated: core22\n\nNot updated: browser"


def test_bases_are_listed_again_after_a_snap_update(manager, monkeypatch):
    from test_snap_updates import installed_snap, release

    from multipak import snap_refresh, snap_updates

    installed_snap(manager.store, "c", "browser", "7", runtime=("core22=7",))
    installed_snap(manager.store, "d", "editor", "3", runtime=("core22=7",))
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda name, *, channel: release("8", channel)
    )

    def refresh(engine, tx, revision, confirm):
        tx.snap_revision, tx.snap_runtime = revision, ["core24=2"]
        engine.store.save(tx)
        return tx

    monkeypatch.setattr(snap_refresh, "refresh", refresh)
    manager.refresh()
    assert [item["title"] for item in manager.rows] == [
        "Alpha",
        "browser",
        "editor",
        "Beta",
    ]
    assert list(bases(manager)) == ["core22"]
    manager.select(index(manager, "browser"))
    manager.updateSelected()
    assert manager.status == "browser was updated."
    assert [(item["title"], item["note"]) for item in manager.rows] == [
        ("Alpha", "Update available: 2.0"),
        ("editor", "Update available: v8 (revision 8)"),
        ("Beta", "The application does not indicate its update address."),
        ("browser", "Up to date"),
    ]
    assert {name: item["note"] for name, item in bases(manager).items()} == {
        "core22": "Update available: v8 (revision 8)",
        "core24": "Updates not checked yet.",
    }
    assert bases(manager)["core22"]["subtitle"] == "Installed revisions: 7"
    # The updated snap stays selected where it moved.
    assert manager.selectedIndex == index(manager, "browser")
    # Reopening the list without checking keeps what the update showed.
    manager.openList("update")
    assert row(manager, "browser")["note"] == "Up to date"


def test_a_downloaded_base_that_some_snaps_still_have_to_use(manager, monkeypatch):
    from test_snap_updates import installed_snap, release

    from multipak import snap_updates

    installed_snap(manager.store, "c", "browser", "7", runtime=("core22=8",))
    installed_snap(manager.store, "d", "editor", "3", runtime=("core22=7",))
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda name, *, channel: release("8", channel)
    )
    manager.refresh()
    assert bases(manager)["core22"]["note"] == "Update available: v8 (revision 8)"
    meta = manager.store.snap_runtime_directory() / "core22/8/meta"
    meta.mkdir(parents=True)
    (meta / "snap.yaml").write_text("name: core22\n")
    manager.openList("update")
    assert (
        bases(manager)["core22"]["note"]
        == "Revision 8 is already downloaded. Still to switch: editor"
    )
    assert all(item["title"] != "core22" for item in manager.rows)
    assert manager.canUpdateAll


def test_update_all_lists_the_bases_each_snap_uses_at_the_end(manager, monkeypatch):
    from test_snap_updates import installed_snap, release

    from multipak import snap_refresh, snap_runtime_updates, snap_updates

    installed_snap(manager.store, "c", "browser", "7", runtime=("core22=7",))
    monkeypatch.setattr(
        snap_updates.snap_store, "release", lambda name, *, channel: release("8", channel)
    )

    def refresh(engine, tx, revision, confirm):
        tx.snap_revision, tx.snap_runtime = revision, ["core24=2"]
        engine.store.save(tx)
        return tx

    def update_runtime(engine, runtime, revision):
        pytest.fail("no snap uses core22 any more")

    monkeypatch.setattr(snap_refresh, "refresh", refresh)
    monkeypatch.setattr(snap_runtime_updates, "update", update_runtime)
    monkeypatch.setattr(
        appimage_updates,
        "check",
        lambda *a, **k: {key * 32: Check("current") for key in ("a", "b")},
    )
    manager.refresh()
    manager.updateAll()
    settle()
    assert not manager.working
    assert "Updated: browser" in manager.status
    assert [row["title"] for row in manager.rows[2:]] == ["browser"]
    assert list(bases(manager)) == ["core24"]

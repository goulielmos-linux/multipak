import os
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from multipak import android_containers as containers
from multipak import android_instances_helper as helper
from multipak.models import Transaction


@pytest.mark.parametrize(
    "packages,expected",
    [
        ("", True),
        ("package:io.github.multipak.gestures", True),
        ("package:org.example.external", False),
        ("package:io.github.multipak.gestures\npackage:org.example.external", False),
        ("Error: package manager unavailable", False),
        ("package:../bad", False),
    ],
)
def test_confirmed_package_list_counts_external_apps_but_not_gesture_helper(packages, expected):
    text = (
        "MULTIPAK_ANDROID_USER_PACKAGES_BEGIN\n" + packages + "\nMULTIPAK_ANDROID_USER_PACKAGES_END"
    )
    assert containers.no_user_apps(text) is expected


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Error",
        "MULTIPAK_ANDROID_USER_PACKAGES_BEGIN",
        "MULTIPAK_ANDROID_USER_PACKAGES_END\nMULTIPAK_ANDROID_USER_PACKAGES_BEGIN",
    ],
)
def test_missing_or_invalid_confirmation_never_offers_removal(output):
    assert not containers.no_user_apps(output)


@pytest.mark.parametrize(
    "packages,expected",
    [
        ("", True),
        ("package:io.github.multipak.gestures", True),
        ("package:org.example.external", False),
        ("Error: unavailable", False),
    ],
)
def test_uninstall_script_reports_real_remaining_user_packages(tmp_path, packages, expected):
    state = tmp_path / "installed"
    state.touch()
    for name, script in {
        "cmd": '#!/bin/sh\ncase "$*" in *" -3 "*) printf "%s\\n" "$USER_PACKAGES";; '
        '*) echo package:android; [ ! -f "$STATE" ] || echo package:org.example.demo;; esac\n',
        "pm": '#!/bin/sh\n[ "$1" = uninstall ] && [ "$2" = -k ] || exit 1\nrm "$STATE"\n',
    }.items():
        path = tmp_path / name
        path.write_text(script)
        path.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", containers.android.REMOVE_ANDROID, "test", "org.example.demo", "check-empty"],
        env=os.environ
        | {
            "PATH": str(tmp_path) + ":/usr/bin:/bin",
            "STATE": str(state),
            "USER_PACKAGES": packages,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert not state.exists()
    assert "MULTIPAK_ANDROID_PACKAGE_REMOVED" in result.stdout
    assert containers.no_user_apps(result.stdout) is expected


@pytest.fixture
def owned(store, monkeypatch):
    tx = Transaction(
        "a" * 32,
        "Android",
        "",
        "Demo",
        "1",
        "apk",
        "hash",
        method="waydroid",
        status="installed",
        android_instance_id="androida",
        android_package_id="org.example.demo",
        android_package_owned=True,
    )
    store.save(tx)
    engine = SimpleNamespace(store=store, runner=object(), _log=lambda _: None)
    calls = []

    def cleanup(engine, tx, *, check_empty=False):
        calls.append((tx.id, check_empty))
        with store.lock():
            store.remove_directory(tx)
        return True

    engine.cleanup = lambda tx: cleanup(engine, tx)
    monkeypatch.setattr(containers.android, "cleanup", cleanup)
    entry = {"id": "androida", "label": "Android", "initialized": True}
    monkeypatch.setattr(containers.android_instances, "discover", lambda _: [entry])
    return engine, tx, calls, entry


def test_offer_after_success_keeps_environment_until_user_confirms(owned, monkeypatch):
    engine, tx, calls, _ = owned
    removed = []
    monkeypatch.setattr(containers.android_instances, "remove", lambda *a, **kw: removed.append(kw))
    candidate = containers.uninstall_application(engine, tx)
    assert candidate == containers.ManagedAndroidContainer("Android", "androida")
    assert calls == [(tx.id, True)] and not removed
    containers.remove_container(engine, candidate)
    assert removed == [{"delete_data": False, "only_empty": True}]


def test_other_registered_installation_blocks_offer(owned):
    engine, tx, _, _ = owned
    engine.store.save(replace(tx, id="b" * 32, android_package_id="org.example.other"))
    assert containers.uninstall_application(engine, tx) is None


@pytest.mark.parametrize("personal", [True, False])
def test_personal_waydroid_and_unowned_package_never_offer(owned, personal):
    engine, tx, calls, _ = owned
    if personal:
        tx.android_instance_id = ""
    else:
        tx.android_package_owned = False
    assert containers.uninstall_application(engine, tx) is True
    assert calls == [(tx.id, False)]


def test_failed_uninstall_never_queries_or_deletes_environment(owned, monkeypatch):
    engine, tx, _, _ = owned

    def failed(*args, **kwargs):
        raise RuntimeError("uninstall failed")

    monkeypatch.setattr(containers.android, "cleanup", failed)
    monkeypatch.setattr(containers.android_instances, "discover", lambda _: pytest.fail("queried"))
    with pytest.raises(RuntimeError, match="uninstall failed"):
        containers.uninstall_application(engine, tx)


@pytest.mark.parametrize("change", ["reference", "name", "missing"])
def test_confirmation_rechecks_references_and_identity(owned, monkeypatch, change):
    engine, tx, _, entry = owned
    candidate = containers.uninstall_application(engine, tx)
    if change == "reference":
        engine.store.save(replace(tx, id="b" * 32))
    elif change == "name":
        entry["label"] = "Changed"
    else:
        monkeypatch.setattr(containers.android_instances, "discover", lambda _: [])
    monkeypatch.setattr(
        containers.android_instances, "remove", lambda *a, **kw: pytest.fail("deleted")
    )
    with pytest.raises(RuntimeError):
        containers.remove_container(engine, candidate, delete_data=True)


@pytest.mark.parametrize("user_packages", ["package:org.example.new", "Error: unavailable"])
def test_privileged_recheck_blocks_removal_before_stopping_service(monkeypatch, user_packages):
    calls = []
    monkeypatch.setattr(helper, "metadata", lambda *args: {})

    def run(argv):
        calls.append(argv)
        if argv[0].endswith("lxc-info"):
            return "FROZEN"
        if "list" in argv:
            return user_packages if "-3" in argv else "package:android"
        return ""

    monkeypatch.setattr(helper, "run", run)
    with pytest.raises(RuntimeError, match="preserved"):
        helper.remove(object(), "androida", True, only_empty=True)
    assert not any("systemctl" in command[0] for command in calls)


def test_privileged_recheck_accepts_only_helper_and_empty_lists(monkeypatch):
    outputs = iter(["RUNNING", "package:android", "package:io.github.multipak.gestures"])
    monkeypatch.setattr(helper, "run", lambda _: next(outputs))
    helper.require_empty("androida")

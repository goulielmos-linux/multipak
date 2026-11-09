import json
from copy import deepcopy

import pytest

from multipak.models import Transaction
from multipak.process import CommandError
from multipak.system import system_removal_states, uninstall_system


def deployment(*names, booted=True, local=()):
    return {
        "booted": booted,
        "requested-packages": list(names),
        "requested-local-packages": list(local),
    }


def status(*deployments, transaction=None):
    return {"deployments": list(deployments), "transaction": transaction}


class SystemRunner:
    def __init__(self, before, after=None, *, fail=False):
        self.before = before
        self.after = after if after is not None else before
        self.calls = []
        self.removed = False
        self.fail = fail

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["rpm-ostree", "status"]:
            return json.dumps(self.after if self.removed else self.before)
        assert argv[:2] == ["rpm-ostree", "uninstall"]
        assert len(argv) == 3
        if self.fail:
            raise CommandError(argv, 1, "Authorization denied")
        self.removed = True
        return ""


@pytest.fixture
def direct(fake_engine, monkeypatch):
    monkeypatch.setattr("multipak.system.os.geteuid", lambda: 1000)
    monkeypatch.setattr("multipak.system.system_install_available", lambda: True)
    tx = Transaction(
        "a" * 32,
        "",
        "",
        "demo-app",
        "1.0-1",
        "rpm",
        "hash",
        method="system",
        status="system_pending",
    )
    fake_engine.store.save(tx)
    return fake_engine, tx


def test_removes_only_registered_request_and_verifies_next_deployment(direct):
    engine, tx = direct
    original = deployment("other-app", local=("demo-app-1.0-1.x86_64",))
    remaining = deployment("other-app", booted=False)
    engine.runner = SystemRunner(status(original), status(remaining, original))
    other = Transaction(
        "b" * 32, "", "", "other-app", "2", "rpm", "", method="system", status="system_pending"
    )
    engine.store.save(other)
    result = uninstall_system(engine, tx)
    assert result.status == "system_remove_pending"
    assert engine.runner.calls == [
        ["rpm-ostree", "status", "--json"],
        ["rpm-ostree", "uninstall", "demo-app"],
        ["rpm-ostree", "status", "--json"],
    ]
    assert engine.store.transactions()[1].to_dict() == other.to_dict()
    assert engine.test_progress[-1][0] == 100
    states = system_removal_states(engine, [result, other])
    assert states == {tx.id: "removal_pending", other.id: "blocked"}
    engine.runner.before = status(deployment("other-app"))
    engine.runner.after = engine.runner.before
    assert system_removal_states(engine, [result])[tx.id] == "absent"


def test_state_uses_only_explicit_requests_not_dependencies_or_similar_names(direct):
    engine, tx = direct
    data = deployment(local=("demo-app-libs-1.0-1.x86_64", "other-1:2.0-3.fc45.noarch"))
    data["packages"] = ["demo-app"]  # A dependency is not an explicit request.
    engine.runner = SystemRunner(status(data))
    assert system_removal_states(engine, [tx])[tx.id] == "absent"
    result = uninstall_system(engine, tx)
    assert result.status == "system_removed"
    assert all(call[1] == "status" for call in engine.runner.calls)


@pytest.mark.parametrize(
    "data, expected",
    [
        (status(deployment("demo-app")), "installed"),
        (status(deployment(local=("demo-app-1:2.0-3.fc45.x86_64",))), "installed"),
        (status(deployment("demo-app", booted=False), deployment()), "install_pending"),
        (status(deployment(booted=False), deployment("demo-app")), "removal_pending"),
        (status(deployment("demo-app", booted=False), deployment("demo-app")), "blocked"),
        (status(deployment("demo-app"), transaction=["upgrade", "client", "address"]), "blocked"),
    ],
)
def test_reconciles_installation_removal_and_pending_updates(direct, data, expected):
    engine, tx = direct
    engine.runner = SystemRunner(data)
    assert system_removal_states(engine, [tx])[tx.id] == expected


@pytest.mark.parametrize(
    "before",
    [
        status(deployment("demo-app", booted=False), deployment("demo-app")),
        status(deployment("demo-app"), transaction=["upgrade"]),
    ],
)
def test_pending_system_changes_block_removal_without_mutating_host(direct, before):
    engine, tx = direct
    engine.runner = SystemRunner(before)
    with pytest.raises(RuntimeError, match="pending"):
        uninstall_system(engine, tx)
    assert engine.runner.calls == [["rpm-ostree", "status", "--json"]]
    assert engine.store.transactions()[0].status == "system_removal_failed"


def test_command_failure_can_be_retried_without_rollback_or_cleanup(direct):
    engine, tx = direct
    original = deployment("demo-app", "other")
    engine.runner = SystemRunner(status(original), fail=True)
    with pytest.raises(CommandError, match="Authorization denied"):
        uninstall_system(engine, tx)
    saved = engine.store.transactions()[0]
    assert saved.status == "system_removal_failed" and saved.error
    engine.runner = SystemRunner(
        status(original), status(deployment("other", booted=False), original)
    )
    assert uninstall_system(engine, saved).status == "system_remove_pending"
    assert all(call[1] in ("status", "uninstall") for call in engine.runner.calls)


def test_success_requires_request_absent_from_next_boot(direct):
    engine, tx = direct
    engine.runner = SystemRunner(status(deployment("demo-app")))
    with pytest.raises(RuntimeError, match="was not confirmed"):
        uninstall_system(engine, tx)
    assert engine.store.transactions()[0].status == "system_removal_failed"
    assert not any(percent == 100 for percent, _ in engine.test_progress)


def test_metadata_only_removal_does_not_claim_pending_reboot(direct):
    engine, tx = direct
    engine.runner = SystemRunner(status(deployment("demo-app")), status(deployment()))
    assert uninstall_system(engine, tx).status == "system_removed"
    assert "Restart" not in engine.test_progress[-1][1]


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "distrobox"},
        {"package_kind": "deb"},
        {"package_name": "--all"},
        {"package_name": "demo; touch /tmp/outside"},
        {"id": "b" * 32},
    ],
)
def test_rejects_untracked_requests_and_command_injection(direct, changes):
    engine, tx = direct
    engine.runner = SystemRunner(status(deployment("demo-app")))
    forged = deepcopy(tx)
    for key, value in changes.items():
        setattr(forged, key, value)
    with pytest.raises(ValueError):
        uninstall_system(engine, forged)
    assert not engine.runner.calls


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"deployments": []},
        status(deployment(booted=False)),
        status({"booted": True}),
        status(deployment(), deployment()),
        status(deployment(local=("unrecognized-local-metadata",))),
    ],
)
def test_invalid_status_never_authorizes_removal(direct, data):
    engine, tx = direct
    engine.runner = SystemRunner(data)
    with pytest.raises((RuntimeError, ValueError)):
        uninstall_system(engine, tx)
    assert not engine.runner.removed


def test_missing_rpm_ostree_does_not_run_commands(direct, monkeypatch):
    engine, tx = direct
    engine.runner = SystemRunner(status(deployment("demo-app")))
    monkeypatch.setattr("multipak.system.system_install_available", lambda: False)
    with pytest.raises(RuntimeError, match="rpm-ostree"):
        uninstall_system(engine, tx)
    assert not engine.runner.calls

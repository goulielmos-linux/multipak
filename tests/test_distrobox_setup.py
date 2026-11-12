import json

import pytest

from multipak.distrobox_setup import (
    DistroboxMissing,
    distrobox_requirement,
    install_distrobox,
    reboot_after_distrobox,
)
from multipak.process import CommandError


def deployment(*packages, booted=True):
    return {
        "booted": booted,
        "requested-packages": list(packages),
        "requested-local-packages": [],
    }


def status(*deployments, transaction=None):
    return {"deployments": list(deployments), "transaction": transaction}


class HostRunner:
    def __init__(self, before, after=None, *, failure=False):
        self.before = before
        self.after = after or before
        self.failure = failure
        self.started = False
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv == ["rpm-ostree", "status", "--json"]:
            return json.dumps(self.after if self.started else self.before)
        if argv == ["rpm-ostree", "install", "distrobox"]:
            if self.failure:
                raise CommandError(argv, 1, "Authorization denied")
            self.started = True
            return ""
        assert argv == ["systemctl", "reboot"]
        return ""


@pytest.fixture
def setup_engine(fake_engine, monkeypatch):
    monkeypatch.setattr("multipak.distrobox_setup.os.geteuid", lambda: 1000)
    monkeypatch.setattr("multipak.distrobox_setup.host.which", lambda _name: None)
    monkeypatch.setattr("multipak.system.system_install_available", lambda: True)
    fake_engine.runner = HostRunner(status(deployment()))
    return fake_engine


def test_preflight_reports_missing_distrobox_before_creating_resources(setup_engine):
    with pytest.raises(DistroboxMissing):
        # The fixture bypasses preflight for unrelated installation tests.
        type(setup_engine).preflight(setup_engine)
    assert setup_engine.tx is None
    assert not setup_engine.runner.calls
    assert not setup_engine.store.transactions()


def test_missing_dependency_explains_system_installation_and_restart(setup_engine):
    result = distrobox_requirement(setup_engine)
    assert result.can_install
    assert not result.restart_required
    assert "rpm-ostree" in result.message
    assert "restart" in result.message
    assert not any("install" in argv for argv in setup_engine.runner.calls)


def test_non_ostree_host_gets_information_without_installation_offer(setup_engine, monkeypatch):
    monkeypatch.setattr("multipak.system.system_install_available", lambda: False)
    result = distrobox_requirement(setup_engine)
    assert not result.can_install
    assert not result.restart_required
    assert not setup_engine.runner.calls


@pytest.mark.parametrize(
    "current",
    [
        status(deployment(), transaction=["Upgrade", "id", "sender"]),
        status(deployment("other", booted=False), deployment()),
    ],
)
def test_pending_system_work_blocks_dependency_installation(setup_engine, current):
    setup_engine.runner = HostRunner(current)
    assert not distrobox_requirement(setup_engine).can_install
    with pytest.raises(RuntimeError):
        install_distrobox(setup_engine)
    assert all(argv == ["rpm-ostree", "status", "--json"] for argv in setup_engine.runner.calls)


def test_already_pending_distrobox_requires_restart_without_reinstall(setup_engine):
    setup_engine.runner = HostRunner(status(deployment("distrobox", booted=False), deployment()))
    result = distrobox_requirement(setup_engine)
    assert result.restart_required
    assert not result.can_install
    assert install_distrobox(setup_engine) is True
    assert all(argv == ["rpm-ostree", "status", "--json"] for argv in setup_engine.runner.calls)


def test_installation_targets_only_distrobox_and_verifies_the_pending_deployment(setup_engine):
    setup_engine.runner = HostRunner(
        status(deployment("keep")),
        status(deployment("keep", "distrobox", booted=False), deployment("keep")),
    )
    assert install_distrobox(setup_engine) is True
    assert setup_engine.runner.calls == [
        ["rpm-ostree", "status", "--json"],
        ["rpm-ostree", "install", "distrobox"],
        ["rpm-ostree", "status", "--json"],
    ]
    assert setup_engine.tx is None
    assert not setup_engine.store.transactions()


def test_confirmation_does_not_bypass_a_new_pending_operation(setup_engine):
    assert distrobox_requirement(setup_engine).can_install
    setup_engine.runner.before = status(deployment(), transaction=["Upgrade"])
    with pytest.raises(RuntimeError, match="in progress"):
        install_distrobox(setup_engine)
    assert ["rpm-ostree", "install", "distrobox"] not in setup_engine.runner.calls


def test_rejected_authorization_never_rolls_back_or_restarts_the_host(setup_engine):
    setup_engine.runner = HostRunner(status(deployment()), failure=True)
    with pytest.raises(CommandError, match="Authorization denied"):
        install_distrobox(setup_engine)
    assert setup_engine.runner.calls == [
        ["rpm-ostree", "status", "--json"],
        ["rpm-ostree", "install", "distrobox"],
    ]


def test_success_exit_code_without_confirmed_package_is_rejected(setup_engine):
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        install_distrobox(setup_engine)


def test_active_request_without_available_command_is_not_reported_as_ready(setup_engine):
    setup_engine.runner = HostRunner(status(deployment()), status(deployment("distrobox")))
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        install_distrobox(setup_engine)


def test_dependency_installed_elsewhere_is_not_installed_again(setup_engine, monkeypatch):
    monkeypatch.setattr("multipak.distrobox_setup.host.which", lambda _name: "/usr/bin/distrobox")
    assert install_distrobox(setup_engine) is False
    assert not setup_engine.runner.calls


def test_explicit_restart_checks_pending_distrobox_and_uses_no_force(setup_engine):
    setup_engine.runner = HostRunner(status(deployment("distrobox", booted=False), deployment()))
    reboot_after_distrobox(setup_engine)
    assert setup_engine.runner.calls == [
        ["rpm-ostree", "status", "--json"],
        ["systemctl", "reboot"],
    ]


@pytest.mark.parametrize(
    "current",
    [
        status(deployment()),
        status(deployment("distrobox", booted=False), deployment(), transaction=["Upgrade"]),
    ],
)
def test_restart_is_not_sent_without_a_confirmed_idle_pending_install(setup_engine, current):
    setup_engine.runner = HostRunner(current)
    with pytest.raises(RuntimeError):
        reboot_after_distrobox(setup_engine)
    assert setup_engine.runner.calls == [["rpm-ostree", "status", "--json"]]

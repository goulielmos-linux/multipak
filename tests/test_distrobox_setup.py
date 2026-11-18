import json

import pytest

from multipak.distrobox_setup import (
    HELP_URLS,
    DistroboxMissing,
    distrobox_requirement,
    host_requirement,
    install_distrobox,
    install_host_package,
    outdated_waydroid,
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
    with pytest.raises(RuntimeError, match="installing an update"):
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


def test_old_distrobox_and_podman_enter_the_distrobox_flow(setup_engine, monkeypatch):
    monkeypatch.setattr("multipak.engine.host.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("multipak.distrobox_setup.host.which", lambda name: f"/usr/bin/{name}")
    versions = {
        ("distrobox", "version"): "distrobox: 1.7.0",
        ("podman", "--version"): "podman version 4.9.3",
    }
    setup_engine.runner.run = lambda argv, **_kwargs: versions[tuple(argv)]
    with pytest.raises(DistroboxMissing, match="Distrobox 1.7.0 and Podman 4.9.3"):
        type(setup_engine).preflight(setup_engine)
    result = distrobox_requirement(setup_engine)
    assert not result.can_install and not result.restart_required
    assert result.update_required
    assert "Podman 4.9.3" in result.message


def test_non_ostree_host_points_to_the_installation_guide(setup_engine, monkeypatch):
    monkeypatch.setattr("multipak.system.system_install_available", lambda: False)
    assert distrobox_requirement(setup_engine).help_url == HELP_URLS["distrobox"]
    waydroid = host_requirement(setup_engine, "waydroid")
    assert waydroid.package == "waydroid"
    assert waydroid.help_url == HELP_URLS["waydroid"]
    assert "Waydroid" in waydroid.message and "Android apps" in waydroid.message


def test_missing_waydroid_is_offered_with_the_purpose_of_each_place(setup_engine):
    installer = host_requirement(setup_engine, "waydroid")
    manager = host_requirement(
        setup_engine, "waydroid", "MultiPak needs Waydroid to create Android containers."
    )
    assert installer.can_install and manager.can_install
    assert "install Android apps" in installer.message
    assert "create Android containers" in manager.message
    assert "rpm-ostree" in manager.message


def test_waydroid_installation_requests_only_waydroid(setup_engine):
    setup_engine.runner = HostRunner(
        status(deployment()), status(deployment("waydroid", booted=False), deployment())
    )
    original = setup_engine.runner.run

    def run(argv, **kwargs):
        if argv == ["rpm-ostree", "install", "waydroid"]:
            setup_engine.runner.started = True
            setup_engine.runner.calls.append(argv)
            return ""
        return original(argv, **kwargs)

    setup_engine.runner.run = run
    assert install_host_package(setup_engine, "waydroid") is True
    assert ["rpm-ostree", "install", "waydroid"] in setup_engine.runner.calls
    assert ["rpm-ostree", "install", "distrobox"] not in setup_engine.runner.calls


def test_pending_waydroid_asks_for_a_restart(setup_engine):
    setup_engine.runner = HostRunner(status(deployment("waydroid", booted=False), deployment()))
    result = host_requirement(setup_engine, "waydroid")
    assert result.restart_required and not result.can_install


@pytest.mark.parametrize(
    ("lxc", "gbinder", "ok"),
    [("7.0.0", "ok", True), ("2.1.1", "ok", False), ("7.0.0", "", False), ("", "ok", False)],
)
def test_waydroid_needs_lxc_3_and_gbinder(lxc, gbinder, ok):
    class Runner:
        def run(self, argv, **_kwargs):
            return lxc if argv[0] == "lxc-info" else gbinder

    message = outdated_waydroid(Runner())
    assert (message == "") is ok


def test_old_waydroid_parts_offer_the_guide_instead_of_an_install(setup_engine, monkeypatch):
    monkeypatch.setattr("multipak.distrobox_setup.host.which", lambda name: f"/usr/bin/{name}")
    setup_engine.runner.run = lambda argv, **_kwargs: "2.0.0" if argv[0] == "lxc-info" else ""
    result = host_requirement(setup_engine, "waydroid")
    assert result.update_required and not result.can_install
    assert result.help_url == HELP_URLS["waydroid"]

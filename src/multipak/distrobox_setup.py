"""Prepare a fixed host dependency (Distrobox or Waydroid) through the host's rpm-ostree service."""

import os
import re
from dataclasses import dataclass

from . import host
from .localization import tr

HELP_URLS = {
    "distrobox": "https://distrobox.it/#installation",
    "waydroid": "https://docs.waydro.id/usage/install-on-desktops",
}
LABELS = {"distrobox": "Distrobox", "waydroid": "Waydroid"}


class DistroboxMissing(RuntimeError):
    """Container installation needs Distrobox before any resources are created."""


class WaydroidMissing(RuntimeError):
    """Android needs the system's Waydroid package before any resources are created."""


@dataclass(frozen=True)
class DistroboxRequirement:
    can_install: bool
    restart_required: bool
    message: str
    update_required: bool = False
    package: str = "distrobox"
    # Where the person can read how to install or update the package by hand.
    help_url: str = ""


def _version(text: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match[1]), int(match[2])) if match else None


def outdated_components(runner) -> str:
    """Explain how to update Distrobox or Podman when they are older than MultiPak needs.

    MultiPak relies on Distrobox 1.8 and on ``podman pull --policy``, added in Podman 5.
    """
    found = {
        name: runner.run([name, "version" if name == "distrobox" else "--version"], timeout=30)
        for name in ("distrobox", "podman")
    }
    distrobox, podman = (_version(found[name]) or (99, 0) for name in found)
    if distrobox >= (1, 8) and podman >= (5, 0):
        return ""
    return tr(
        "MultiPak needs Distrobox 1.8 and Podman 5 or newer. This system has Distrobox {0} "
        "and Podman {1}. Update them on your system to continue."
    ).format(*(found[name].split()[-1] if found[name].split() else "?" for name in found))


def outdated_waydroid(runner) -> str:
    """Explain what to update when the parts Waydroid brings are missing or too old.

    The bundled runtime uses the system's LXC (3 or newer: older releases name the
    options differently) and its Python gbinder module.
    """

    def probe(argv):
        try:
            return str(runner.run(argv, check=False, timeout=30)).strip()
        except (OSError, RuntimeError, TimeoutError):
            return ""

    lxc = probe(["lxc-info", "--version"])
    gbinder = probe(["/usr/bin/python3", "-I", "-c", "import gbinder; print('ok')"])
    if (_version(lxc + ".0") or (0, 0)) >= (3, 0) and gbinder.endswith("ok"):
        return ""
    return tr(
        "Waydroid on this system is incomplete or too old: MultiPak needs LXC 3 or newer and "
        "the Python gbinder module, which come with Waydroid. Update Waydroid on your system "
        "to continue."
    )


def _purpose(package, purpose):
    if purpose:
        return purpose
    if package == "waydroid":
        return tr("MultiPak needs Waydroid to install Android apps.")
    return tr("MultiPak needs Distrobox 1.8 or newer to install applications in containers.")


def host_requirement(engine, package, purpose=""):
    """What stands between MultiPak and a working ``package`` on this host, and what to do."""
    from .system import _requested_packages, _system_status, system_install_available

    label = LABELS[package]
    purpose = _purpose(package, purpose)
    help_url = HELP_URLS[package]

    def requirement(can_install, restart_required, message, **extra):
        return DistroboxRequirement(
            can_install, restart_required, message, package=package, **extra
        )

    if host.which(package):
        outdated = outdated_components if package == "distrobox" else outdated_waydroid
        if message := outdated(engine.runner):
            return requirement(False, False, message, update_required=True, help_url=help_url)
        return requirement(False, False, tr("{0} is available. You can continue.").format(label))
    if not system_install_available():
        return requirement(
            False,
            False,
            purpose + " " + tr("Install it on your system, then try again."),
            help_url=help_url,
        )
    status = _system_status(engine)
    target = status["deployments"][0]
    if status.get("transaction"):
        return requirement(
            False,
            False,
            tr(
                "Your system is installing an update right now. Wait for it to finish, "
                "then try again."
            ),
        )
    if not target["booted"]:
        if package in _requested_packages(target):
            return requirement(
                False,
                True,
                tr("{0} is scheduled for installation. Restart your computer to finish.").format(
                    label
                ),
            )
        return requirement(
            False,
            False,
            tr(
                "Your system has an update or change waiting for a restart. Restart your "
                "computer, then try again."
            ),
        )
    return requirement(
        True,
        False,
        purpose
        + " "
        + tr(
            "Do you want to install it now with rpm-ostree? You will need to restart your "
            "computer to finish."
        ),
    )


def distrobox_requirement(engine):
    return host_requirement(engine, "distrobox")


def install_host_package(engine, package, purpose=""):
    """Request ``package`` with rpm-ostree; True when a restart is needed to finish."""
    from .system import _requested_packages, _system_status

    label = LABELS[package]
    if os.geteuid() == 0:
        raise RuntimeError(tr("Run MultiPak as a regular user."))
    if engine.tx is not None:
        raise RuntimeError(
            tr("Finish the current application operation before installing {0}.").format(label)
        )
    with engine.store.lock():
        if host.which(package):
            return False
        requirement = host_requirement(engine, package, purpose)
        if requirement.restart_required:
            return True
        if not requirement.can_install:
            raise RuntimeError(requirement.message)
        engine.progress(-1, tr("Installing {0} using rpm-ostree…").format(label))
        # Authorize through the daemon's polkit agent; no downloaded/root script is used.
        engine.runner.run(["rpm-ostree", "install", package], timeout=7200)
        engine.progress(-1, tr("Verifying the {0} installation…").format(label))
        status = _system_status(engine)
        target = status["deployments"][0]
        if (
            status.get("transaction")
            or package not in _requested_packages(target)
            or (target["booted"] and not host.which(package))
        ):
            raise RuntimeError(
                tr(
                    "{0} installation could not be confirmed. Check the rpm-ostree status "
                    "before trying again."
                ).format(label)
            )
        return not target["booted"]


def install_distrobox(engine):
    return install_host_package(engine, "distrobox")


def reboot_after_host_package(engine, package):
    from .system import _requested_packages, _system_status

    with engine.store.lock():
        status = _system_status(engine)
        target = status["deployments"][0]
        if status.get("transaction"):
            raise RuntimeError(
                tr("A system operation is in progress. Wait for it to finish before restarting.")
            )
        if target["booted"] or package not in _requested_packages(target):
            raise RuntimeError(
                tr(
                    "No pending {0} installation was found. Check the system status before "
                    "restarting."
                ).format(LABELS[package])
            )
        engine.progress(-1, tr("Requesting a system restart…"))
        engine.runner.run(["systemctl", "reboot"], timeout=60)


def reboot_after_distrobox(engine):
    reboot_after_host_package(engine, "distrobox")

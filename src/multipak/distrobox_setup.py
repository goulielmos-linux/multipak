"""Prepare the fixed Distrobox dependency through the host's rpm-ostree service."""

import os
import re
from dataclasses import dataclass

from . import host
from .localization import tr


class DistroboxMissing(RuntimeError):
    """Container installation needs Distrobox before any resources are created."""


@dataclass(frozen=True)
class DistroboxRequirement:
    can_install: bool
    restart_required: bool
    message: str
    update_required: bool = False


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


def distrobox_requirement(engine):
    from .system import _requested_packages, _system_status, system_install_available

    if host.which("distrobox"):
        if message := outdated_components(engine.runner):
            return DistroboxRequirement(False, False, message, update_required=True)
        return DistroboxRequirement(
            False, False, tr("Distrobox is available. You can continue installing the application.")
        )
    if not system_install_available():
        return DistroboxRequirement(
            False,
            False,
            tr(
                "MultiPak needs Distrobox 1.8 or newer to install applications in "
                "containers. Install it on your system and open the package again."
            ),
        )
    status = _system_status(engine)
    target = status["deployments"][0]
    if status.get("transaction"):
        return DistroboxRequirement(
            False,
            False,
            tr(
                "A system operation is in progress. Wait for it to finish before "
                "installing Distrobox."
            ),
        )
    if not target["booted"]:
        if "distrobox" in _requested_packages(target):
            return DistroboxRequirement(
                False,
                True,
                tr(
                    "Distrobox is scheduled for installation. Restart your computer, then "
                    "reopen the package to continue."
                ),
            )
        return DistroboxRequirement(
            False,
            False,
            tr("A system change is pending. Restart your computer before installing Distrobox."),
        )
    return DistroboxRequirement(
        True,
        False,
        tr(
            "MultiPak needs Distrobox 1.8 or newer to install applications in containers. "
            "Do you want to install it now with rpm-ostree? System authorization may be "
            "required, and you will need to restart your computer to finish."
        ),
    )


def install_distrobox(engine):
    from .system import _requested_packages, _system_status

    if os.geteuid() == 0:
        raise RuntimeError(tr("Run MultiPak as a regular user."))
    if engine.tx is not None:
        raise RuntimeError(
            tr("Finish the current application operation before installing Distrobox.")
        )
    with engine.store.lock():
        if host.which("distrobox"):
            return False
        requirement = distrobox_requirement(engine)
        if requirement.restart_required:
            return True
        if not requirement.can_install:
            raise RuntimeError(requirement.message)
        engine.progress(-1, tr("Installing Distrobox using rpm-ostree…"))
        # Authorize through the daemon's polkit agent; no downloaded/root script is used.
        engine.runner.run(["rpm-ostree", "install", "distrobox"], timeout=7200)
        engine.progress(-1, tr("Verifying the Distrobox installation…"))
        status = _system_status(engine)
        target = status["deployments"][0]
        if (
            status.get("transaction")
            or "distrobox" not in _requested_packages(target)
            or (target["booted"] and not host.which("distrobox"))
        ):
            raise RuntimeError(
                tr(
                    "Distrobox installation could not be confirmed. Check the rpm-ostree "
                    "status before trying again."
                )
            )
        return not target["booted"]


def reboot_after_distrobox(engine):
    from .system import _requested_packages, _system_status

    with engine.store.lock():
        status = _system_status(engine)
        target = status["deployments"][0]
        if status.get("transaction"):
            raise RuntimeError(
                tr("A system operation is in progress. Wait for it to finish before restarting.")
            )
        if target["booted"] or "distrobox" not in _requested_packages(target):
            raise RuntimeError(
                tr(
                    "No pending Distrobox installation was found. Check the system status "
                    "before restarting."
                )
            )
        engine.progress(-1, tr("Requesting a system restart…"))
        engine.runner.run(["systemctl", "reboot"], timeout=60)

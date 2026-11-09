"""Direct RPM installation and removal through rpm-ostree."""

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from multipak.localization import tr

from . import host
from .engine import Engine
from .environments import parse_os_release
from .metadata import inspect_package
from .models import Package, Transaction

SYSTEM_REMOVABLE_STATUSES = frozenset(
    {
        "system_pending",
        "system_installed",
        "system_removing",
        "system_removal_failed",
        "system_remove_pending",
    }
)


def _requested_packages(deployment: dict) -> set[str]:
    """Use explicit requests, never the full RPM database or dependency list."""
    result = set()
    for key in ("requested-packages", "requested-local-packages"):
        values = deployment.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(tr("Could not retrieve the list of layered system packages."))
        for value in values:
            if key == "requested-local-packages":
                # Local requests use RPM NEVRA: name-version-release.arch.
                match = re.fullmatch(
                    r"([A-Za-z0-9][A-Za-z0-9+._-]*)-[^-\s/]+-[^-\s/]+"
                    r"\.[A-Za-z0-9_]+",
                    value,
                )
                if not match:
                    raise ValueError(tr("Unrecognized local package ID: ") + value)
                value = match[1]
            result.add(value)
    return result


def _system_status(engine: Engine) -> dict:
    if not system_install_available():
        raise RuntimeError(tr("This operation requires a system booted with rpm-ostree."))
    status = json.loads(engine.runner.run(["rpm-ostree", "status", "--json"], timeout=30))
    deployments = status.get("deployments", []) if isinstance(status, dict) else []
    if (
        not isinstance(deployments, list)
        or not deployments
        or any(not isinstance(item, dict) for item in deployments)
        or any(not isinstance(item.get("booted"), bool) for item in deployments)
        or sum(item.get("booted") is True for item in deployments) != 1
    ):
        raise RuntimeError(tr("Could not identify the active deployment."))
    return status


def system_removal_states(engine: Engine, transactions: list[Transaction]) -> dict[str, str]:
    """Read current and next-boot requests without changing the operating system."""
    status = _system_status(engine)
    target = status["deployments"][0]
    booted = next(item for item in status["deployments"] if item.get("booted"))
    current_requests, next_requests = _requested_packages(booted), _requested_packages(target)
    states = {}
    for tx in transactions:
        current, upcoming = tx.package_name in current_requests, tx.package_name in next_requests
        if current and not upcoming:
            state = "removal_pending"
        elif not current and upcoming:
            state = "install_pending"
        elif not current:
            state = "absent"
        elif status.get("transaction") or not target.get("booted"):
            state = "blocked"
        else:
            state = "installed"
        states[tx.id] = state
    return states


def uninstall_system(engine: Engine, transaction: Transaction) -> Transaction:
    """Remove one registered explicit request using rpm-ostree's dependency resolution."""
    if (
        transaction.method != "system"
        or transaction.package_kind != "rpm"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", transaction.package_name)
    ):
        raise ValueError(tr("Select an RPM that MultiPak installed directly on your system."))
    if os.geteuid() == 0:
        raise RuntimeError(tr("Run MultiPak as a regular user."))
    with engine.store.lock():
        tx = next(
            (
                item
                for item in engine.store.transactions()
                if item.id == transaction.id
                and item.method == "system"
                and item.package_kind == "rpm"
                and item.package_name == transaction.package_name
                and item.status in SYSTEM_REMOVABLE_STATUSES
            ),
            None,
        )
        if tx is None:
            raise ValueError(tr("This system package cannot be uninstalled through MultiPak."))
        engine.tx = tx
        try:
            before = _system_status(engine)
            if before.get("transaction") or not before["deployments"][0].get("booted"):
                raise RuntimeError(
                    tr(
                        "A system operation or change is pending. Complete it and restart before "
                        "uninstalling."
                    )
                )
            if tx.package_name not in _requested_packages(before["deployments"][0]):
                tx.status, tx.error = "system_removed", ""
                engine.store.save(tx)
                engine.progress(
                    100, tr("The package is no longer requested as a direct installation.")
                )
                return tx
            tx.status, tx.error = "system_removing", ""
            engine.store.save(tx)
            engine._stage(10, tr("Preparing to uninstall the system package…"))
            # The session's polkit agent authorizes the daemon, just as in the terminal.
            engine.runner.run(["rpm-ostree", "uninstall", tx.package_name], timeout=7200)
            engine._stage(95, tr("Verifying uninstallation…"))
            after = _system_status(engine)
            target = after["deployments"][0]
            if tx.package_name in _requested_packages(target):
                raise RuntimeError(
                    tr(
                        "The command finished, but package removal was not confirmed. Reload the "
                        "list to check the system status."
                    )
                )
            tx.status = "system_removed" if target.get("booted") else "system_remove_pending"
            engine.store.save(tx)
            engine.progress(
                100,
                tr("Restart to complete the uninstallation.")
                if tx.status == "system_remove_pending"
                else tr("Direct installation request removed."),
            )
            return tx
        except Exception as exc:
            tx.status, tx.error = "system_removal_failed", str(exc)
            engine.store.save(tx)
            raise


def current_system_family() -> str:
    for path in (Path("/run/host/etc/os-release"), Path("/etc/os-release")):
        try:
            family = parse_os_release(path.read_text(encoding="utf-8")).get("ID", "")
        except (OSError, ValueError):
            continue
        if family:
            return family
    return "linux"


def system_install_available() -> bool:
    if not host.which("rpm-ostree"):
        return False
    if host.is_flatpak():
        try:
            return host.run(["test", "-e", "/run/ostree-booted"], timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return Path("/run/ostree-booted").exists()


def install_system(engine: Engine, package: Package) -> Transaction:
    """Must only be called after the explicit warning has been accepted in the UI."""
    if package.kind != "rpm":
        raise ValueError(tr("Direct installation only supports RPMs compatible with the system."))
    if os.geteuid() == 0:
        raise RuntimeError(tr("Run MultiPak as a regular user."))
    if not system_install_available():
        raise RuntimeError(tr("Direct installation requires a system booted with rpm-ostree."))
    with engine.store.lock():
        # Never combine this install with an existing pending deployment.
        before = json.loads(engine.runner.run(["rpm-ostree", "status", "--json"]))
        deployments = before.get("deployments", [])
        if not deployments or not any(d.get("booted") for d in deployments):
            raise RuntimeError(tr("Could not identify the active deployment."))
        if before.get("transaction") or not deployments[0].get("booted"):
            raise RuntimeError(
                tr(
                    "A system operation or deployment is pending. Complete it and restart "
                    "before trying again."
                )
            )
        token = uuid.uuid4().hex
        engine.tx = Transaction(
            token,
            "",
            "",
            package.name,
            package.version,
            "rpm",
            "",
            method="system",
        )
        tx = engine.tx
        engine.store.save(tx)
        try:
            engine._stage(5, tr("Preparing to install directly on the system…"))
            staged = engine.store.directory(token) / "package.rpm"
            engine._copy_package(package.path, staged)
            copied = inspect_package(staged, engine.runner)
            if (copied.name, copied.version, copied.architecture) != (
                package.name,
                package.version,
                package.architecture,
            ):
                raise ValueError(tr("The file has changed. Open the package again."))
            with staged.open("rb") as stream:
                tx.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            tx.status = "system_installing"
            engine.store.save(tx)
            engine._stage(15, tr("Preparing a new system deployment…"))
            # rpm-ostree delegates authorization to the active session's polkit agent.
            # Do not wrap the whole GUI in sudo/pkexec.
            engine.runner.run(["rpm-ostree", "install", str(staged)], timeout=7200)
            engine._stage(95, tr("Verifying the pending deployment…"))
            after = json.loads(engine.runner.run(["rpm-ostree", "status", "--json"]))
            if not after.get("deployments") or after["deployments"][0].get("booted"):
                raise RuntimeError(
                    tr(
                        "The command finished, but no pending deployment was found. Check "
                        "rpm-ostree status in the system details."
                    )
                )
            tx.status = "system_pending"
            engine.store.save(tx)
            staged.unlink(missing_ok=True)
            engine.progress(100, tr("Restart to complete the installation."))
            return tx
        except Exception as exc:
            tx.status, tx.error = "system_failed", str(exc)
            engine.store.save(tx)
            raise

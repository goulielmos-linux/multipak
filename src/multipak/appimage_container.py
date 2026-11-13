"""Run AppImages that need extra system components inside one shared Fedora container."""

import json
import re
import threading
import uuid
from pathlib import Path

from multipak.localization import tr

from .appimage_dependencies import Dependency, startup_failures
from .container_progress import ImageProgress, PackageProgress
from .containers import ManagedContainer, _read_record, _record_path, _save_record
from .environment_choices import APPIMAGE_CONTAINER as CONTAINER_NAME
from .environment_choices import free_container_name
from .environments import OWNER_LABEL
from .models import ENVIRONMENTS, ExistingEnvironment
from .process import CommandError, Runner

APPIMAGE_LABEL = "io.github.multipak.appimage-container"
IMAGE = next(item.image for item in ENVIRONMENTS if item.id == "fedora-latest")
CHECK_SECONDS = 15
# Exit status of timeout(1) when the time ran out: the application was still running.
STILL_RUNNING = 124
CHECK_ROUNDS = 3
# Package names and RPM capabilities such as libfoo.so.1()(64bit) or python3dist(foo).
PACKAGE_SPEC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+()-]{0,199}")


def _enter(name, *arguments):
    return ["distrobox", "enter", "--name", name, "--no-tty", "--no-workdir", "--", *arguments]


def _inspect(runner, reference):
    data = json.loads(runner.run(["podman", "inspect", reference], timeout=30))
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


def verify_name(runner, container):
    """The container that answers to this name must still be the recorded one."""
    try:
        current = runner.run(
            ["podman", "container", "inspect", "--format", "{{.Id}}", "--", container.name],
            timeout=30,
        ).strip()
    except CommandError as error:
        raise RuntimeError(
            tr("The container for this AppImage is unavailable. Install the AppImage again.")
        ) from error
    if current != container.container_id:
        raise RuntimeError(
            tr("The AppImage container identity or ownership has changed. It was not used.")
        )


def _owned(runner, record):
    """The recorded container, if it still exists with the same ID and ownership label."""
    try:
        runner.run(["podman", "container", "exists", record.container_id], timeout=30)
    except CommandError as error:
        if error.code == 1:
            return None
        raise
    data = _inspect(runner, record.container_id) or {}
    labels = (data.get("Config") or {}).get("Labels") or {}
    if (
        data.get("Id") != record.container_id
        or labels.get(OWNER_LABEL) != record.owner_id
        or labels.get(APPIMAGE_LABEL) != "1"
    ):
        raise RuntimeError(
            tr("The AppImage container identity or ownership has changed. It was not used.")
        )
    return data


def ensure_container(engine):
    """Reuse MultiPak's AppImage container or create it. Returns its ManagedContainer."""
    runner = engine.runner
    engine.preflight()
    record = find_record(engine.store)
    if record is not None and _owned(runner, record) is not None:
        verify_name(runner, record)
        return record
    rows = json.loads(runner.run(["podman", "ps", "--all", "--format", "json"], timeout=30))
    # A container MultiPak did not create may use the default name: take the lowest
    # free C2-, C3-... name instead, without asking.
    name = free_container_name(
        CONTAINER_NAME, {item for row in rows for item in row.get("Names") or []}
    )
    engine.progress(-1, tr("Fetching the container image…"))
    ImageProgress(engine.progress).run(
        runner, ["podman", "pull", "--policy=missing", IMAGE], terminal_output=True
    )
    owner = uuid.uuid4().hex
    engine.progress(-1, tr("Creating the container for AppImages…"))
    # No custom home: like any AppImage, the application uses the user's own folders.
    runner.run(
        [
            "distrobox",
            "create",
            "--yes",
            "--name",
            name,
            "--image",
            IMAGE,
            "--no-entry",
            "--additional-packages",
            "python3",
            "--additional-flags",
            f"--label={OWNER_LABEL}={owner} --label={APPIMAGE_LABEL}=1"
            " --label=org.opencontainers.image.description=MultiPak",
        ]
    )
    data = _inspect(runner, name) or {}
    labels = (data.get("Config") or {}).get("Labels") or {}
    if labels.get(OWNER_LABEL) != owner or not re.fullmatch(r"[0-9a-f]{64}", data.get("Id", "")):
        raise RuntimeError(tr("The container was not created."))
    # A record for an older container that no longer exists is replaced.
    for stale in _record_names(engine.store):
        if stale != name:
            _record_path(engine.store, stale).unlink(missing_ok=True)
    record = ManagedContainer(name, data["Id"], owner, False)
    _save_record(engine.store, record)
    engine.progress(-1, tr("Initializing the container…"))
    PackageProgress(engine.progress).run(runner, _enter(name, "true"))
    return record


def install_packages(engine, container, specs):
    """Install what can be found; return the specs no package provides."""
    specs = [spec for spec in dict.fromkeys(specs) if PACKAGE_SPEC.fullmatch(spec)]
    if not specs:
        return []
    verify_name(engine.runner, container)
    progress = PackageProgress(engine.progress, install_label=tr("Installing the dependencies…"))
    # A guessed name that no package provides must not stop the others.
    progress.run(
        engine.runner,
        _enter(
            container.name,
            "sudo",
            "-n",
            "dnf",
            "-y",
            "install",
            "--skip-unavailable",
            "--setopt=install_weak_deps=False",
            "--",
            *specs,
        ),
    )
    output = engine.runner.run(
        _enter(container.name, "rpm", "-q", "--whatprovides", "--", *specs), check=False
    )
    return [
        line.removeprefix("no package provides ").strip()
        for line in output.splitlines()
        if line.startswith("no package provides ")
    ]


def _cleanup_runner(runner):
    """A runner that still works after the installation was cancelled."""
    if isinstance(runner, Runner):
        return Runner(threading.Event(), runner.log)
    return runner


def isolated_check(engine, container, appdir: Path, transaction_id: str):
    """Start the application briefly in a throwaway copy of the container: no network,
    no access to the user's files, no display.

    Returns (missing components it recognized, outcome). The outcome is "running" when
    the application was still running at the time limit, "exited" when it ended without
    an error, and "unknown" when it failed for a reason the check does not recognize.
    """
    runner = engine.runner
    image = f"localhost/multipak-appimage-check:{transaction_id[:12]}"
    name = f"multipak-appimage-check-{transaction_id[:12]}"
    mount = "/opt/multipak-appimage"
    verify_name(runner, container)
    try:
        runner.run(
            ["podman", "commit", "--pause=false", container.container_id, image], timeout=600
        )
        command = [
            "podman",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            "--security-opt=label=disable",
            "--userns=keep-id",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/run",
            # A backstop: timeout(1) inside the container normally stops it first.
            f"--timeout={CHECK_SECONDS + 15}",
            "--volume",
            f"{appdir}:{mount}:ro",
            "--env",
            "HOME=/tmp",
            "--env",
            f"APPDIR={mount}",
            "--env",
            f"APPIMAGE={mount}/AppRun",
            "--env",
            "QT_QPA_PLATFORM=offscreen",
            "--entrypoint",
            "/usr/bin/timeout",
            image,
            "--signal=TERM",
            "--kill-after=5",
            str(CHECK_SECONDS),
            f"{mount}/AppRun",
        ]
        try:
            output, code = runner.run(command, timeout=CHECK_SECONDS + 60), 0
        except CommandError as error:
            output, code = error.output, error.code
    finally:
        # The installation may have been cancelled: clean up regardless.
        cleanup = _cleanup_runner(runner)
        cleanup.run(["podman", "rm", "--force", name], check=False, timeout=60)
        cleanup.run(["podman", "image", "rm", "--force", image], check=False, timeout=120)
    missing = startup_failures(output)
    if missing:
        return missing, "unknown"
    if code == STILL_RUNNING:
        return (), "running"
    return (), "exited" if code == 0 else "unknown"


def verify_and_complete(engine, container, appdir: Path, transaction_id: str):
    """Repeat the isolated check while it finds installable components. Returns warnings."""
    warnings, installed, unresolved = [], [], []
    outcome = "unknown"
    for _ in range(CHECK_ROUNDS):
        reported, outcome = isolated_check(engine, container, appdir, transaction_id)
        found = [
            item for item in reported if item.package not in installed and item not in unresolved
        ]
        if not found:
            break
        installable = [item for item in found if item.package]
        unresolved.extend(item for item in found if not item.package)
        if not installable:
            break
        engine.progress(-1, tr("Installing the dependencies found by the check…"))
        missing = install_packages(engine, container, [item.package for item in installable])
        unresolved.extend(item for item in installable if item.package in missing)
        installed.extend(item.package for item in installable if item.package not in missing)
    if installed:
        warnings.append(
            tr("The check installed additional dependencies: {0}.").format(", ".join(installed))
        )
    if unresolved:
        warnings.append(
            tr("These dependencies could not be installed automatically: {0}.").format(
                ", ".join(item.label for item in _unique(unresolved))
            )
        )
    elif outcome == "unknown":
        # Not proof of a problem: without a display, many applications cannot start here.
        warnings.append(
            tr(
                "The check could not confirm that the application starts correctly. "
                "Applications that need a display, for example, cannot be checked this way."
            )
        )
    return warnings


def _unique(items):
    seen, result = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def launch_command(container_name, entry: Path, arguments, environment):
    """Command that starts the extracted AppRun inside the AppImage container."""
    assignments = [f"{key}={value}" for key, value in environment.items()]
    return _enter(container_name, "env", *assignments, str(entry), *arguments)


def _record_names(store):
    """Names of recorded AppImage containers: the default one or C2-, C3-..."""
    directory = store.root / "environments"
    if not directory.is_dir():
        return []
    pattern = re.compile(r"(?:C[0-9]+-)?" + re.escape(CONTAINER_NAME))
    return sorted(
        path.name
        for path in directory.iterdir()
        if pattern.fullmatch(path.name) and (path / "container.json").is_file()
    )


def find_record(store):
    """MultiPak's AppImage container record, whatever free name it was given."""
    for name in _record_names(store):
        record = _read_record(store, name)
        if record is not None and record.owner_id:
            return record
    return None


def update_entry(store):
    """The AppImage container as a row for the manager's update list."""
    try:
        record = find_record(store)
    except (OSError, TypeError, ValueError):
        return None
    if record is None:
        return None
    return ExistingEnvironment(record.name, record.container_id, "fedora", "")


__all__ = [
    "CONTAINER_NAME",
    "Dependency",
    "ensure_container",
    "install_packages",
    "isolated_check",
    "launch_command",
    "update_entry",
    "verify_and_complete",
    "verify_name",
]

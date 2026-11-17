"""Run AppImages that need extra system components inside one shared Fedora container."""

import json
import os
import re
import shutil
import tarfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from multipak.localization import tr

from .appimage_dependencies import Dependency, startup_failures
from .container_progress import ImageProgress, PackageProgress
from .containers import ManagedContainer, read_record, record_path, save_record
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
# The capability MultiPak requests for a Python module: python3dist(<project name>).
PYTHON_DISTRIBUTION = re.compile(r"python3dist\(([a-z0-9][a-z0-9._-]{0,99})\)")
MAX_WHEEL = 512 * 1024 * 1024


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
    if record is not None:
        data = _owned(runner, record)
        if data is not None:
            current = (data.get("Name") or "").removeprefix("/")
            if current == record.name:
                return record
            # Renamed outside MultiPak: take it back instead of creating a second one.
            return _reclaim(engine, record, current)
        # Deleted: a new one is created and the old record is dropped once it exists.
    # A container MultiPak did not create may use the default name: take the lowest
    # free C2-, C3-... name instead, without asking.
    name = free_container_name(CONTAINER_NAME, _container_names(runner))
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
            record_path(engine.store, stale).unlink(missing_ok=True)
    record = ManagedContainer(name, data["Id"], owner, False)
    save_record(engine.store, record)
    engine.progress(-1, tr("Initializing the container…"))
    PackageProgress(engine.progress).run(runner, _enter(name, "true"))
    return record


def _container_names(runner):
    rows = json.loads(runner.run(["podman", "ps", "--all", "--format", "json"], timeout=30))
    return {item for row in rows for item in row.get("Names") or []}


def _reclaim_name(runner, current):
    """The name a renamed AppImage container gets back: the default or the lowest free."""
    return free_container_name(CONTAINER_NAME, _container_names(runner) - {current})


def _reclaim(engine, record, current):
    name = _reclaim_name(engine.runner, current)
    engine.progress(-1, tr("Restoring the container name…"))
    engine.runner.run(["podman", "rename", record.container_id, name], timeout=60)
    reclaimed = replace(record, name=name)
    save_record(engine.store, reclaimed)
    if record.name != name:
        old = record_path(engine.store, record.name)
        old.unlink(missing_ok=True)
        try:
            old.parent.rmdir()
        except OSError:
            pass
    _retarget(engine.store, reclaimed)
    return reclaimed


def _retarget(store, record):
    """Point every AppImage in this container at its current name."""
    for tx in store.transactions():
        if (
            tx.method == "appimage"
            and tx.appimage_mode == "container"
            and tx.container_id == record.container_id
            and tx.container != record.name
        ):
            tx.container = record.name
            store.save(tx)


def _installed_in_container(store):
    return [
        tx
        for tx in store.transactions()
        if tx.method == "appimage" and tx.appimage_mode == "container" and tx.status == "installed"
    ]


def needing_repair(engine):
    """AppImages whose container was renamed or deleted. Nothing is reported when Podman
    cannot be queried."""
    candidates = _installed_in_container(engine.store)
    if not candidates:
        return []
    try:
        rows = json.loads(
            engine.runner.run(["podman", "ps", "--all", "--format", "json"], timeout=30)
        )
    except (CommandError, OSError, TimeoutError, ValueError) as error:
        engine._log(tr("Could not check the containers: {0}").format(error))
        return []
    if not isinstance(rows, list):
        return []
    names = {
        str(row.get("Id", "")).removeprefix("sha256:"): set(row.get("Names") or [])
        for row in rows
        if isinstance(row, dict)
    }
    return [tx for tx in candidates if tx.container not in names.get(tx.container_id, set())]


def _transaction(store, transaction_id):
    tx = next((item for item in store.transactions() if item.id == transaction_id), None)
    if tx is None or tx.method != "appimage" or tx.appimage_mode != "container":
        raise RuntimeError(tr("This AppImage installation is unavailable."))
    return tx


def repair_plan(engine, transaction_id):
    """What repairing this AppImage will do, for the confirmation.

    kind is "system" (the system now has every dependency: no container is needed),
    "renamed" (the container is taken back and renamed), "install" (its
    dependencies go into MultiPak's current AppImage container), "recreate" (a new
    container is created) or "relink" (nothing to confirm: the name is already right).
    """
    tx = _transaction(engine.store, transaction_id)
    runner = engine.runner
    plan = {"application": tx.package_name, "old": tx.container, "current": "", "target": ""}
    try:
        runner.run(["podman", "container", "exists", tx.container_id], timeout=30)
        plan["deleted"] = False
    except CommandError as error:
        if error.code != 1:
            raise
        plan["deleted"] = True
    record = find_record(engine.store)
    data = _owned(runner, record) if record is not None else None
    current = (data.get("Name") or "").removeprefix("/") if data is not None else ""
    if data is not None and record.container_id == tx.container_id and current == record.name:
        return plan | {"kind": "relink", "current": current, "target": current}
    # Without a container, if the system itself now has everything, none is needed.
    if _system_has_everything(engine, tx):
        return plan | {"kind": "system"}
    if data is None:
        return plan | {
            "kind": "recreate",
            "target": free_container_name(CONTAINER_NAME, _container_names(runner)),
        }
    target = record.name if current == record.name else _reclaim_name(runner, current)
    if record.container_id != tx.container_id:
        return plan | {"kind": "install", "current": current, "target": target}
    return plan | {"kind": "renamed", "current": current, "target": target}


def _system_has_everything(engine, tx):
    """Whether the system itself now provides what the AppImage needs, for example
    because the user installed the dependencies after the container was gone."""
    from .appimage import Archive
    from .appimage_dependencies import analyze

    application = engine.store.appimage_directory(tx.id) / "application.AppImage"
    if application.is_symlink() or not application.is_file():
        return False
    try:
        report = analyze(
            Archive(application, engine.runner), engine.runner, scratch=engine.store.root
        )
    except Exception as error:
        engine._log(tr("Could not check the AppImage dependencies: {0}").format(error))
        return False
    return not report.incomplete and not report.required


def repair(engine, transaction_id, *, on_system=False):
    """Make the AppImage run again: in MultiPak's AppImage container or, with on_system,
    directly on the system, which already provides its dependencies."""
    with engine.store.lock():
        tx = _transaction(engine.store, transaction_id)
        if tx.status != "installed":
            raise RuntimeError(tr("This AppImage installation is unavailable."))
        if on_system:
            # The extracted application is already in place; the shortcut calls the
            # transaction, not the container, so it keeps working unchanged.
            tx.appimage_mode, tx.appimage_container = "extracted", False
            tx.container, tx.container_id = "", ""
            engine.store.save(tx)
            return tx
        container = ensure_container(engine)
        tx = _transaction(engine.store, transaction_id)
        if tx.container_id != container.container_id:
            engine.progress(-1, tr("Installing the dependencies in the container…"))
            missing = install_packages(engine, container, tx.appimage_packages)
            if missing:
                engine._log(
                    tr("These dependencies could not be installed automatically: {0}.").format(
                        ", ".join(missing)
                    )
                )
            tx.container_id = container.container_id
        tx.container = container.name
        engine.store.save(tx)
        return tx


def install_packages(engine, container, specs, from_pypi=None):
    """Install what can be found; return the specs no package provides.

    Python modules Fedora does not package are looked up on PyPI. from_pypi, when
    given, receives the names installed from there."""
    specs = [spec for spec in dict.fromkeys(specs) if PACKAGE_SPEC.fullmatch(spec)]
    if not specs:
        return []
    missing = _install_fedora_packages(engine, container, specs)
    remaining = []
    for spec in missing:
        match = PYTHON_DISTRIBUTION.fullmatch(spec)
        if match and install_from_pypi(engine, container, match[1]):
            if from_pypi is not None:
                from_pypi.append(match[1])
        else:
            remaining.append(spec)
    return remaining


def _install_fedora_packages(engine, container, specs):
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


def _normalized(name):
    """Python project and module names compare without case and separators."""
    return re.sub(r"[-_.]+", "_", name).lower()


def wheel_modules(path):
    """Top-level modules a wheel installs, read from its file list. Nothing in it runs."""
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
    modules = set()
    for name in names:
        first, _, rest = name.partition("/")
        if not first or first.endswith((".dist-info", ".data")):
            continue
        if rest:
            modules.add(first)
        elif first.endswith(".py"):
            modules.add(first[:-3])
        elif first.endswith(".so"):
            modules.add(first.split(".", 1)[0])
    return modules


def _pip(container, *arguments, root=False):
    command = ("sudo", "-n", "python3") if root else ("python3",)
    return _enter(
        container.name,
        *command,
        "-m",
        "pip",
        # General options go first: everything after "--" names packages.
        "--disable-pip-version-check",
        "--no-input",
        *arguments,
    )


# What in a source package means it must be compiled: C, C++, Cython, Fortran, Rust
# and the build systems that drive them. Such packages are not built here.
COMPILED_SUFFIXES = (
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm", ".pyx", ".pxd", ".pxi",
    ".f", ".f90", ".f95", ".for", ".rs", ".go", ".cu",
)  # fmt: skip
COMPILED_FILES = ("Cargo.toml", "CMakeLists.txt", "meson.build", "build.zig")
MAX_SOURCE = 64 * 1024 * 1024


def _download(container, project, directory, *, source=False):
    return _pip(
        container,
        "download",
        "--no-deps",
        "--no-binary=:all:" if source else "--only-binary=:all:",
        "--dest",
        str(directory),
        "--",
        project,
    )


def source_members(path):
    """Names in a source package (.tar.gz or .zip), read without extracting it."""
    path = Path(path)
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:*") as archive:
        return archive.getnames()


def pure_python_source(path, module):
    """Whether a source package holds only Python code, and the module the app uses.

    Raises ValueError, with the reason, when it does not."""
    names = source_members(path)
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if base.lower().endswith(COMPILED_SUFFIXES) or base in COMPILED_FILES:
            raise ValueError(
                tr(
                    "PyPI only offers the source code of {0}, which needs to be compiled; "
                    "MultiPak builds only packages made of Python code."
                ).format(module)
            )
    wanted = _normalized(module)
    found = any(
        _normalized(part.removesuffix(".py")) == wanted
        and (index < len(parts) - 1 or part.endswith(".py"))
        for name in names
        for parts in [name.split("/")]
        for index, part in enumerate(parts)
        if index > 0  # The first part is the package's own folder.
    )
    if not found:
        raise ValueError(
            tr("The package {0} on PyPI does not contain the module the application uses.").format(
                module
            )
        )


# pip's default for packages that do not say how they are built (PEP 517/518); wheel
# is added for setuptools versions that ask for it while building.
DEFAULT_BUILD_REQUIRES = ("setuptools>=40.8.0",)
REQUIREMENT = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"  # Name.
    r"(?:\[[A-Za-z0-9._, -]*\])?"  # Extras.
    r"\s*(?:[<>=!~]=?\s*[A-Za-z0-9.*+!_-]+\s*(?:,\s*[<>=!~]=?\s*[A-Za-z0-9.*+!_-]+\s*)*)?"
    r"(?:;[A-Za-z0-9._ <>=!~'\"()-]*)?"  # Environment markers.
)
BUILD_SECONDS = 600


def _source_file(path, name):
    """A file at the top of a source package, such as pyproject.toml, or None."""
    path = Path(path)
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                parts = info.filename.split("/")
                if len(parts) == 2 and parts[1] == name and info.file_size <= 1024**2:
                    return archive.read(info).decode("utf-8")
        return None
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            parts = member.name.split("/")
            if len(parts) == 2 and parts[1] == name and member.isfile():
                if member.size > 1024**2:
                    return None
                return archive.extractfile(member).read().decode("utf-8")
    return None


def build_requirements(path):
    """What a source package declares it needs to be built (pyproject.toml), read as
    text: nothing in it runs. Requirements found only while building are not looked
    for, since finding them runs the package's code."""
    import tomllib

    text = _source_file(path, "pyproject.toml")
    requires = list(DEFAULT_BUILD_REQUIRES)
    if text is not None:
        try:
            system = tomllib.loads(text).get("build-system") or {}
        except tomllib.TOMLDecodeError as error:
            raise ValueError(str(error)) from error
        if "requires" in system:
            requires = system["requires"]
    requires = _valid_requirements(requires)
    if any(
        _normalized(re.split(r"[\s\[<>=!~;]", item, maxsplit=1)[0]) == "setuptools"
        for item in requires
    ):
        requires.append("wheel")
    return list(dict.fromkeys(requires))


@contextmanager
def _throwaway(engine, container, volumes):
    """A throwaway copy of the container, running while the block runs: no network, no
    access to the user's files or the session, only volumes (host, inside, writable).
    Commands run in it with podman exec. It is removed afterwards, even when
    cancelled. Yields its name."""
    runner = engine.runner
    tag = uuid.uuid4().hex[:12]
    image = f"localhost/multipak-pypi-build:{tag}"
    name = f"multipak-pypi-build-{tag}"
    verify_name(runner, container)
    mounts = []
    for host_path, inside, writable in volumes:
        mounts += ["--volume", f"{host_path}:{inside}" + ("" if writable else ":ro")]
    try:
        runner.run(
            ["podman", "commit", "--pause=false", container.container_id, image], timeout=600
        )
        runner.run(
            [
                "podman",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--network=none",
                "--cap-drop=all",
                "--security-opt=no-new-privileges",
                "--security-opt=label=disable",
                "--userns=keep-id",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
                f"--timeout={BUILD_SECONDS * (DISCOVERY_ROUNDS + 2)}",
                *mounts,
                "--env",
                "HOME=/tmp",
                "--entrypoint",
                "/usr/bin/sleep",
                image,
                str(BUILD_SECONDS * (DISCOVERY_ROUNDS + 2)),
            ],
            timeout=120,
        )
        yield name
    finally:
        cleanup = _cleanup_runner(runner)
        cleanup.run(["podman", "rm", "--force", name], check=False, timeout=60)
        cleanup.run(["podman", "image", "rm", "--force", image], check=False, timeout=120)


# Asks the package's build system, inside the throwaway container, what more it needs
# to build a wheel (PEP 517 get_requires_for_build_wheel), with pyproject_hooks, the
# public library pip itself uses, downloaded by MultiPak as a wheel like the build
# tools. It prints the answer as JSON; MultiPak checks it before downloading anything.
DISCOVERY = r"""
import json, os, subprocess, sys, tarfile, tempfile, tomllib, zipfile
source, tools = sys.argv[1], sys.argv[2]

def pip_install(target, *requirements):
    subprocess.run(
        [sys.executable, "-m", "pip", "--disable-pip-version-check", "--no-input",
         "--no-cache-dir", "install", "--no-index", "--find-links", tools,
         "--target", target, "--", *requirements],
        check=True, stdout=sys.stderr,
    )

work = tempfile.mkdtemp()
if source.endswith(".zip"):
    zipfile.ZipFile(source).extractall(work)
else:
    tarfile.open(source).extractall(work, filter="data")
(top,) = os.listdir(work)
root = os.path.join(work, top)
config = {}
if os.path.exists(os.path.join(root, "pyproject.toml")):
    with open(os.path.join(root, "pyproject.toml"), "rb") as stream:
        config = tomllib.load(stream).get("build-system", {})
requires = config.get("requires", ["setuptools>=40.8.0", "wheel"])
backend = config.get("build-backend", "setuptools.build_meta:__legacy__")
hooks_library = tempfile.mkdtemp()
pip_install(hooks_library, "pyproject_hooks")
sys.path.insert(0, hooks_library)
from pyproject_hooks import BuildBackendHookCaller
environment = tempfile.mkdtemp()
pip_install(environment, *requires)

def runner(command, cwd=None, extra_environ=None):
    env = dict(os.environ, **(extra_environ or {}), PYTHONPATH=environment)
    subprocess.run(command, cwd=cwd, env=env, check=True, stdout=sys.stderr)

hooks = BuildBackendHookCaller(root, backend, config.get("backend-path"), runner=runner)
print("MULTIPAK-REQUIRES " + json.dumps(hooks.get_requires_for_build_wheel()))
"""
DISCOVERY_ROUNDS = 3


def _valid_requirements(items):
    """Requirements a build system asks for, as MultiPak accepts them: names with
    versions and markers; never links, local paths or repositories."""
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError(tr("The source code does not say clearly how it is built."))
    items = [item.strip() for item in items]
    if any(not REQUIREMENT.fullmatch(item) or len(item) > 200 for item in items):
        raise ValueError(tr("The source code does not say clearly how it is built."))
    return items


def build_isolated(engine, container, source, tools, output, requires):
    """Build source into a wheel offline, in one throwaway copy of the container.

    The build tools are downloaded beforehand by MultiPak, as wheels only. What the
    build system asks for besides them is found inside the copy, checked, downloaded
    the same way into the folder the copy sees, and asked again, a few rounds at most.
    Then the wheel is built there, still offline."""
    runner = engine.runner
    helper = output.parent / "helper"
    helper.mkdir(mode=0o700, exist_ok=True)
    (helper / "discover.py").write_text(DISCOVERY, encoding="utf-8")
    runner.run(
        _pip(container, "download", "--only-binary=:all:", "--dest", str(tools), "--")
        + ["pyproject_hooks"],
        timeout=600,
    )
    volumes = [
        (source.parent, "/opt/source", False),
        (tools, "/opt/tools", False),
        (helper, "/opt/helper", False),
        (output, "/opt/output", True),
    ]
    inside = f"/opt/source/{source.name}"
    wanted = list(requires)
    with _throwaway(engine, container, volumes) as name:
        for _round in range(DISCOVERY_ROUNDS):
            try:
                text = runner.run(
                    ["podman", "exec", name, "/usr/bin/python3", "/opt/helper/discover.py"]
                    + [inside, "/opt/tools"],
                    timeout=BUILD_SECONDS,
                )
            except CommandError as error:
                engine._log(tr("The build system could not say what it needs: {0}").format(error))
                break
            line = next(
                (item for item in text.splitlines() if item.startswith("MULTIPAK-REQUIRES ")),
                "",
            )
            try:
                asked = _valid_requirements(json.loads(line.removeprefix("MULTIPAK-REQUIRES ")))
            except ValueError:
                break
            new = [item for item in asked if item not in wanted]
            if not new:
                break
            engine._log(tr("The build also needs: {0}").format(", ".join(new)))
            wanted += new
            runner.run(
                _pip(container, "download", "--only-binary=:all:", "--dest", str(tools), "--")
                + new,
                timeout=600,
            )
        runner.run(
            [
                "podman",
                "exec",
                name,
                "/usr/bin/python3",
                "-m",
                "pip",
                "--disable-pip-version-check",
                "--no-input",
                "--no-cache-dir",
                "wheel",
                "--no-deps",
                "--no-index",
                "--find-links",
                "/opt/tools",
                "--wheel-dir",
                "/opt/output",
                "--",
                inside,
            ],
            timeout=BUILD_SECONDS,
        )


def _single(directory, suffixes, limit):
    files = [
        path
        for path in directory.iterdir()
        if path.name.endswith(suffixes) and path.is_file() and not path.is_symlink()
    ]
    return files[0] if len(files) == 1 and files[0].stat().st_size <= limit else None


def install_from_pypi(engine, container, project):
    """Install a Python module Fedora does not package from PyPI, in the container only.

    A ready-made wheel is used when PyPI has one. Otherwise its source code is used,
    but only when it is made of Python code alone, and it is built in a throwaway copy
    of the container without network or access to the user's files (build_isolated);
    anything to be compiled is left out. Either way the package is installed only if it
    contains the module the application imports, so a project that merely has a similar
    name is never used.
    Returns whether it was installed."""
    runner = engine.runner
    directory = engine.store.root / "pypi" / uuid.uuid4().hex
    try:
        verify_name(runner, container)
        engine.progress(-1, tr("Installing {0} from PyPI…").format(project))
        try:
            runner.run(_pip(container, "--version"), timeout=60)
        except CommandError:
            # Fedora ships pip separately from Python.
            _install_fedora_packages(engine, container, ["python3-pip"])
        directory.mkdir(parents=True, mode=0o700)
        try:
            runner.run(_download(container, project, directory), timeout=600)
        except CommandError:
            wheel = None
        else:
            wheel = _single(directory, (".whl",), MAX_WHEEL)
            if wheel is None:
                raise ValueError(
                    tr("PyPI did not provide a ready-made package for {0}.").format(project)
                )
        if wheel is None:
            # No wheel at all: the source code, if it is Python only.
            sources = directory / "source"
            sources.mkdir(mode=0o700)
            runner.run(_download(container, project, sources, source=True), timeout=600)
            source = _single(sources, (".tar.gz", ".tgz", ".tar.bz2", ".zip"), MAX_SOURCE)
            if source is None:
                raise ValueError(
                    tr("PyPI did not provide a ready-made package for {0}.").format(project)
                )
            pure_python_source(source, project)
            # The build tools it declares, as ready-made wheels only: downloading a
            # wheel runs nothing. The build itself runs without network.
            tools = directory / "tools"
            tools.mkdir(mode=0o700)
            requires = build_requirements(source)
            runner.run(
                _pip(container, "download", "--only-binary=:all:", "--dest", str(tools), "--")
                + requires,
                timeout=600,
            )
            built = directory / "built"
            built.mkdir(mode=0o700)
            engine.progress(-1, tr("Building {0} in an isolated environment…").format(project))
            build_isolated(engine, container, source, tools, built, requires)
            # A package made of Python code builds a wheel for any computer.
            wheel = _single(built, ("-none-any.whl",), MAX_WHEEL)
            if wheel is None:
                raise ValueError(
                    tr("The source code of {0} could not be built into a package.").format(project)
                )
        modules = {_normalized(module) for module in wheel_modules(wheel)}
        if _normalized(project) not in modules:
            raise ValueError(
                tr(
                    "The package {0} on PyPI does not contain the module the application uses."
                ).format(project)
            )
        # Installed for the container's administrator, in /usr/local: Fedora keeps its own
        # packages in /usr, and the host system never sees either.
        runner.run(
            _pip(
                container,
                "install",
                "--only-binary=:all:",
                "--root-user-action=ignore",
                "--break-system-packages",
                "--",
                str(wheel),
                root=True,
            ),
            timeout=1200,
        )
        return True
    except (CommandError, OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
        engine._log(tr("Could not install {0} from PyPI: {1}").format(project, error))
        return False
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _cleanup_runner(runner):
    """A runner that still works after the installation was cancelled."""
    if isinstance(runner, Runner):
        return Runner(threading.Event(), runner.log)
    return runner


XVFB = "xorg-x11-server-Xvfb"
# A virtual display for the check, so that graphical applications get past their
# start: Xvfb on :99, then the application under timeout(1). $0 is the time limit,
# $1 the application.
DISPLAY_SCRIPT = r"""
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99
n=0
while [ ! -S /tmp/.X11-unix/X99 ] && [ "$n" -lt 50 ]; do sleep 0.1; n=$((n + 1)); done
exec /usr/bin/timeout --signal=TERM --kill-after=5 "$0" "$1"
"""


def _virtual_display(engine, container):
    """Whether the container has Xvfb, installing it the first time. Without it, the
    check runs without a display, as before."""

    def present():
        output = engine.runner.run(_enter(container.name, "rpm", "-q", XVFB), check=False)
        return output.strip().startswith(XVFB + "-")

    try:
        if present():
            return True
        _install_fedora_packages(engine, container, [XVFB])
        return present()
    except (CommandError, OSError) as error:
        engine._log(tr("Could not prepare a virtual display for the check: {0}").format(error))
        return False


def isolated_check(engine, container, appdir: Path, transaction_id: str):
    """Start the application briefly in a throwaway copy of the container: no network,
    no access to the user's files, and a virtual display when Xvfb can be installed.

    Returns (missing components it recognized, outcome). The outcome is "running" when
    the application was still running at the time limit, "exited" when it ended without
    an error, and "unknown" when it failed for a reason the check does not recognize.
    """
    runner = engine.runner
    image = f"localhost/multipak-appimage-check:{transaction_id[:12]}"
    name = f"multipak-appimage-check-{transaction_id[:12]}"
    mount = "/opt/multipak-appimage"
    verify_name(runner, container)
    display = _virtual_display(engine, container)
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
        ]
        if display:
            # Xvfb keeps its compiled keymaps here; the rest stays read-only.
            command[command.index("--volume") : command.index("--volume")] = [
                "--tmpfs",
                "/var/lib/xkb",
            ]
            command += [
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                DISPLAY_SCRIPT,
                str(CHECK_SECONDS),
                f"{mount}/AppRun",
            ]
        else:
            command += [
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


def verify_and_complete(
    engine, container, appdir: Path, transaction_id: str, packages=None, missing=None
):
    """Repeat the isolated check while it finds installable components. Returns warnings.

    packages, when given, receives what the check installed, so a repair can install
    the same components in a new container; missing receives what it could not install."""
    warnings, installed, unresolved, from_pypi = [], [], [], []
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
        absent = install_packages(
            engine, container, [item.package for item in installable], from_pypi
        )
        unresolved.extend(item for item in installable if item.package in absent)
        installed.extend(item.package for item in installable if item.package not in absent)
    if packages is not None:
        packages.extend(item for item in installed if item not in packages)
    if installed:
        warnings.append(
            tr("The check installed additional dependencies: {0}.").format(", ".join(installed))
        )
    if from_pypi:
        warnings.append(pypi_note(from_pypi))
    if unresolved:
        labels = [item.label for item in _unique(unresolved)]
        if missing is not None:
            missing.extend(label for label in labels if label not in missing)
        warnings.append(
            tr("These dependencies could not be installed automatically: {0}.").format(
                ", ".join(labels)
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


def pypi_note(projects):
    return tr("These dependencies were installed from PyPI, the Python package index: {0}.").format(
        ", ".join(projects)
    )


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
        record = read_record(store, name)
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
    "needing_repair",
    "repair",
    "repair_plan",
    "update_entry",
    "verify_and_complete",
    "verify_name",
]

"""Installation state machine. Container lifecycle and journals stay outside the UI."""

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from multipak.localization import tr

from . import guest, host
from .container_progress import ImageProgress, PackageProgress
from .desktop import export_desktops, register_container_icon, remove_container_icon
from .distrobox_setup import DistroboxMissing
from .environment_choices import container_base, default_environment, next_container_name
from .environments import (
    CUSTOM_LABEL,
    OWNER_LABEL,
    SHARED_LABEL,
    discover,
    parse_os_release,
    read_os_release,
)
from .metadata import inspect_package
from .models import (
    Environment,
    ExistingEnvironment,
    Package,
    Transaction,
    reserved_environment_name,
    valid_environment_name,
)
from .process import Cancelled, CommandError, Runner
from .storage import record_install_failure, require_copy_space
from .store import Store


class Engine:
    def install_appimage(self, package: Package, **options) -> Transaction:
        from . import appimage

        if package.kind != "appimage":
            raise ValueError(tr("Select an AppImage."))
        return appimage.install(self, package, **options)

    def __init__(
        self,
        store: Store,
        cancelled: threading.Event,
        progress: Callable[[int, str], None] = lambda *_: None,
        log: Callable[[str], None] = lambda _: None,
    ):
        self.store, self.cancelled = store, cancelled
        self.progress, self.notify_log = progress, log
        self.tx: Transaction | None = None
        self.runner = Runner(cancelled, self._log)

    def _log(self, message: str) -> None:
        self.notify_log(message)
        if self.tx is not None:
            path = self.store.directory(self.tx.id) / "installation.log"
            # Keep logs bounded independently from process capture.
            if path.parent.exists() and (not path.exists() or path.stat().st_size < 8_000_000):
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(message + "\n")

    def _stage(self, percent: int, label: str) -> None:
        if self.cancelled.is_set():
            raise Cancelled
        assert self.tx is not None
        self.tx.stage = label
        self.store.save(self.tx)
        self.progress(percent, label)

    def _enter(self, *args: str) -> list[str]:
        assert self.tx is not None
        return [
            "distrobox",
            "enter",
            "--name",
            self.tx.container,
            "--no-tty",
            "--no-workdir",
            "--",
            *args,
        ]

    def preflight(self) -> None:
        executables = (
            ("distrobox", "podman", "setsid") if host.is_flatpak() else ("distrobox", "podman")
        )
        for executable in executables:
            if not host.which(executable):
                if executable == "distrobox":
                    raise DistroboxMissing(tr("Install Distrobox on the system to continue."))
                raise RuntimeError(tr("Install {0} on the system to continue.").format(executable))
        # Require the session user's local rootless Podman store.
        info = self.runner.run(["podman", "info", "--format", "json"], timeout=30)
        data = json.loads(info)
        if not data.get("host", {}).get("security", {}).get("rootless", False):
            raise RuntimeError(tr("Run as a regular user with rootless Podman."))
        if data.get("host", {}).get("serviceIsRemote", False):
            raise RuntimeError(tr("Remote Podman connections are not supported in this version."))

    def install(
        self,
        package: Package,
        environment: Environment,
        existing: ExistingEnvironment | None = None,
        *,
        container_name: str = "",
        force_new_environment: bool = False,
    ) -> Transaction:
        if container_name and (not valid_environment_name(container_name) or existing is not None):
            raise ValueError(tr("Choose a valid name for the new environment."))
        if container_name and reserved_environment_name(container_name):
            raise ValueError(
                tr("Choose a custom name. The MultiPak prefix is reserved for the application.")
            )
        if package.kind != environment.kind:
            raise ValueError(tr("The selected environment does not match the package format."))
        with self.store.lock():
            self.preflight()
            if existing is None and not container_name and not force_new_environment:
                existing = default_environment(discover(self.runner, (environment,)), environment)
            if existing and not existing.matches(environment):
                raise ValueError(
                    tr(
                        "The existing environment does not match the selected distribution and "
                        "version."
                    )
                )
            token = uuid.uuid4().hex
            self.tx = Transaction(
                token,
                existing.name
                if existing
                else container_name or f"multipak-pending-{environment.id}-{token[:8]}",
                environment.image,
                package.name,
                package.version,
                package.kind,
                "",
                container_id=existing.container_id if existing else "",
                container_created=existing is None,
                managed_home=existing.managed_home if existing else True,
                environment_id=(
                    environment.id.partition("-")[0] + "-" + environment.resolved_version
                    if environment.resolved_version
                    else environment.id
                ),
                custom_container_name=bool(container_name),
                force_new_environment=force_new_environment,
            )
            self.runner.defer_cancellation = existing is not None
            directory = self.store.directory(token)
            try:
                self.store.save(self.tx)
                self._stage(2, tr("Preparing a copy of the package…"))
                staged = directory / f"package.{package.kind}"
                self._copy_package(package.path, staged)
                copied = inspect_package(staged, self.runner)
                if (copied.kind, copied.name, copied.version, copied.architecture) != (
                    package.kind,
                    package.name,
                    package.version,
                    package.architecture,
                ):
                    raise ValueError(tr("The file has changed. Open the package again."))
                with staged.open("rb") as stream:
                    self.tx.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
                self.store.save(self.tx)
                self._create(environment, directory)
                return self._finish_install(staged)
            except Cancelled:
                self.tx.status = "interrupted"
                self.store.save(self.tx)
                raise
            except Exception as exc:
                reported = record_install_failure(self.store, self.tx, exc)
                if reported is not exc:
                    raise reported from exc
                raise

    def _copy_package(self, source: Path, target: Path) -> None:
        with source.open("rb") as incoming:
            require_copy_space(target.parent, os.fstat(incoming.fileno()).st_size)
            with target.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    if self.cancelled.is_set():
                        raise Cancelled
                    outgoing.write(chunk)

    def _create(self, environment: Environment, directory: Path) -> None:
        assert self.tx is not None
        if not self.tx.container_created:
            container = self._owned_container(self.runner)
            if container is None:
                raise RuntimeError(tr("The selected environment no longer exists."))
            release = read_os_release(self.runner, self.tx.container)
            family, _, version = environment.id.partition("-")
            version = environment.resolved_version or version
            if release.get("ID") != family or (
                version not in ("latest", "stable", "rolling")
                and release.get("VERSION_ID") != version
            ):
                raise RuntimeError(tr("The environment distribution or version has changed."))
            self.tx.image_id = container.get("Image", "")
            self.store.save(self.tx)
            self._stage(25, tr("Preparing the existing container {0}…").format(self.tx.container))
            self.runner.run(self._enter("python3", "--version"))
            self._stage(35, tr("Copying the package to the existing container…"))
            self._prepare_guest_directory(self.runner)
            self._copy_into_guest(Path(guest.__file__), self.guest_helper)
            self._copy_into_guest(directory / f"package.{self.tx.package_kind}", self.guest_package)
            return
        self._stage(-1, tr("Fetching the container image…"))
        if not self.tx.images_scanned:
            self.tx.images_before = [self._image_id(row) for row in self._images(self.runner)]
            self.tx.images_scanned = True
            self.store.save(self.tx)
        # Fetch once, with Podman's transfer output enabled, before creating the container.
        ImageProgress(self.progress).run(
            self.runner,
            ["podman", "pull", "--policy=missing", environment.image],
            terminal_output=True,
        )
        # Resolve floating image versions before choosing a stable, readable name.
        # Keep a persisted canonical name on retries, including partially created containers.
        if (
            not self.tx.custom_container_name
            and self.tx.container.startswith("multipak-pending-")
            and not self.tx.container_id
        ):
            family, _, version = environment.id.partition("-")
            if version in ("latest", "stable"):
                release = parse_os_release(
                    self.runner.run(
                        [
                            "podman",
                            "run",
                            "--rm",
                            "--entrypoint",
                            "/usr/bin/cat",
                            environment.image,
                            "/usr/lib/os-release",
                        ]
                    )
                )
                if release.get("ID") != family or not re.fullmatch(
                    r"[0-9]+(?:\.[0-9]+)*", release.get("VERSION_ID", "")
                ):
                    raise RuntimeError(tr("Could not identify the image version."))
                version = release["VERSION_ID"]
            if not (family == "arch" and version == "rolling") and (
                family not in ("fedora", "ubuntu", "debian")
                or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
            ):
                raise ValueError(tr("Invalid distribution or version for Kontainer."))
            base = container_base(environment, resolved_version=version)
            rows = json.loads(self.runner.run(["podman", "ps", "--all", "--format", "json"]))
            names = {name for row in rows for name in row.get("Names", [])}
            if base in names and not self.tx.force_new_environment:
                raise RuntimeError(
                    tr("The default container already exists. Reopen the package to reuse it.")
                )
            name = next_container_name(base, names, new=self.tx.force_new_environment)
            self.tx.container = name
            self.tx.environment_id = f"{family}-{version}"
            self.store.save(self.tx)
        if self.tx.custom_container_name and not self.tx.container_id:
            rows = json.loads(self.runner.run(["podman", "ps", "--all", "--format", "json"]))
            if any(self.tx.container in row.get("Names", []) for row in rows):
                data = json.loads(self.runner.run(["podman", "inspect", self.tx.container]))[0]
                labels = data.get("Config", {}).get("Labels") or {}
                if labels.get(OWNER_LABEL) != self.tx.id:
                    raise ValueError(
                        tr("A container with that name already exists. Choose another name.")
                    )
        # Podman uses a cached image if available. No forced pull on every installation.
        self._stage(-1, tr("Creating the Distrobox container…"))
        home = self.environment_home
        home.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            home.mkdir(mode=0o700)
        except FileExistsError:
            if home.is_symlink() or not home.is_dir():
                raise ValueError(
                    tr("The container's home folder could not be verified. It will be kept.")
                ) from None
        else:
            self.tx.home_created = True
            self.store.save(self.tx)
        self.tx.status = "creating"
        self.store.save(self.tx)
        if self._owned_container(self.runner) is None:
            self.runner.run(
                [
                    "distrobox",
                    "create",
                    "--yes",
                    "--name",
                    self.tx.container,
                    "--image",
                    environment.image,
                    "--home",
                    str(home),
                    "--no-entry",
                    "--additional-packages",
                    "python" if environment.kind == "arch" else "python3",
                    "--additional-flags",
                    f"--label={OWNER_LABEL}={self.tx.id}"
                    # distrobox-list 1.8 splits formatted labels on newlines. Ubuntu's
                    # inherited multiline description otherwise becomes a phantom row.
                    + " --label=org.opencontainers.image.description=MultiPak"
                    + (f" --label={SHARED_LABEL}=1" if self.tx.managed_home else "")
                    + (f" --label={CUSTOM_LABEL}=1" if self.tx.custom_container_name else ""),
                ]
            )
        container = self._owned_container(self.runner)
        if container is None:
            raise RuntimeError(tr("The container was not created."))
        self.tx.container_id = container["Id"]
        self.tx.image_id = container.get("Image", "")
        self.store.save(self.tx)
        from .containers import remember_container

        remember_container(self.store, self.tx)
        try:
            register_container_icon(self.store, self.tx)
        except OSError as exc:
            self._log(tr("Could not register the icon in Kontainer: {0}").format(exc))
        self._stage(-1, tr("Initializing the container…"))
        PackageProgress(self.progress).run(self.runner, self._enter("true"))
        self._stage(35, tr("Copying the package to the container…"))
        self._prepare_guest_directory(self.runner)
        self._copy_into_guest(Path(guest.__file__), self.guest_helper)
        self._copy_into_guest(directory / f"package.{self.tx.package_kind}", self.guest_package)
        self.tx.status = "installing"
        self.store.save(self.tx)

    # Distrobox shares /tmp with the host. Keep every temporary file in a private
    # per-installation folder (mode 700) instead of directly in the shared /tmp.
    @property
    def guest_directory(self) -> str:
        assert self.tx is not None
        return f"/tmp/multipak-{self.tx.id}"

    @property
    def guest_package(self) -> str:
        return f"{self.guest_directory}/package.{self.tx.package_kind}"

    @property
    def guest_helper(self) -> str:
        return f"{self.guest_directory}/guest.py"

    @property
    def guest_marker(self) -> str:
        return f"{self.guest_directory}/running.json"

    def _legacy_guest_paths(self) -> list[str]:
        # Paths used by 1.0.0 Alpha 1; removed too when an old operation is cleaned up.
        assert self.tx is not None
        prefix = f"/tmp/multipak-{self.tx.id}"
        return [f"{prefix}.{self.tx.package_kind}", f"{prefix}-guest.py", f"{prefix}-running.json"]

    def _remove_host_guest_files(self, runner: Runner) -> None:
        """Distrobox shares /tmp with the host, so the private folder can outlive a
        deleted container. Its files belong to the container's user namespace, so remove
        them from inside that namespace. Best effort: /tmp is also cleared at reboot."""
        try:
            runner.run(
                [
                    "podman",
                    "unshare",
                    "rm",
                    "-rf",
                    "--",
                    self.guest_directory,
                    *self._legacy_guest_paths(),
                ],
                check=False,
                timeout=60,
            )
        except (OSError, TimeoutError) as exc:
            self._log(tr("Some temporary environment files could not be removed.") + f" {exc}")

    def _prepare_guest_directory(self, runner: Runner) -> None:
        """Create the private folder, or accept it on retry only if it is still a real
        directory owned by the user with mode 700. Only plain arguments are passed through
        distrobox enter: no shell script whose quoting it could change."""
        directory = self.guest_directory
        error = RuntimeError(tr("Could not prepare a private temporary folder in the environment."))
        try:
            # Fails harmlessly when the folder already exists; verified below.
            runner.run(self._enter("mkdir", "-m", "700", "--", directory), check=False)
            # %f is the raw mode in hexadecimal: independent of the container's language.
            state = runner.run(self._enter("stat", "-c", "%u:%f", "--", directory))
            uid = runner.run(self._enter("id", "-u"))
        except CommandError as exc:
            raise error from exc

        def last_line(text):
            lines = [line.strip() for line in str(text).splitlines() if line.strip()]
            return lines[-1] if lines else ""

        user = last_line(uid)
        # 41c0 = a real directory (not a symbolic link) with mode 700.
        if not user.isdigit() or last_line(state) != f"{user}:41c0":
            raise error

    @property
    def environment_home(self) -> Path:
        assert self.tx is not None
        return self.store.environment_directory(self.tx.container) / "home"

    @property
    def shared_environment(self) -> bool:
        assert self.tx is not None
        return not self.tx.container_created or bool(self.store.other_references(self.tx))

    def _copy_into_guest(self, source: Path, destination: str) -> None:
        assert self.tx is not None
        self._copy_with_runner(self.runner, source, destination)

    def retry(self, tx: Transaction) -> Transaction:
        """Resume creation, initialization or package work using the retained local snapshot."""
        if tx.method == "appimage" and tx.package_kind == "appimage":
            from . import appimage

            return appimage.retry(self, tx)
        if tx.method != "distrobox":
            raise ValueError(tr("Use system management for rpm-ostree operations."))
        with self.store.lock():
            self.tx = tx
            self.runner.defer_cancellation = self.shared_environment
            self.preflight()
            tx.error = ""
            tx.status = "installing"
            try:
                self.store.save(tx)
                staged = self.store.directory(tx.id) / f"package.{tx.package_kind}"
                with staged.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if not tx.sha256 or digest != tx.sha256:
                    raise RuntimeError(
                        tr("The copy is incomplete or has changed. Cancel and reopen the package.")
                    )
                environment = Environment(
                    tx.environment_id or "resume",
                    tr("Original environment"),
                    tx.package_kind,
                    tx.image,
                )
                if tx.package_install_started and self._owned_container(self.runner):
                    self._ensure_guest_idle(self.runner)
                self._create(environment, self.store.directory(tx.id))
                return self._finish_install(staged)
            except Exception as exc:
                reported = record_install_failure(self.store, tx, exc)
                if reported is not exc:
                    raise reported from exc
                raise

    def _finish_install(self, staged: Path) -> Transaction:
        assert self.tx is not None
        if not self.tx.container_created and not self.tx.package_install_started:
            state = self._guest_result(
                self.runner.run(
                    self._enter(
                        "python3",
                        self.guest_helper,
                        "--state",
                        self.tx.package_kind,
                        self.tx.package_name,
                    )
                )
            )
            if state["present"]:
                raise RuntimeError(
                    tr(
                        "This package is already installed in the selected environment. "
                        "MultiPak will not overwrite the existing installation."
                    )
                )
        self.tx.package_install_started = True
        self.tx.warnings.clear()
        self.store.save(self.tx)
        if self.tx.package_kind == "deb":
            self._install_deb()
        elif self.tx.package_kind == "rpm":
            self._install_rpm()
        elif self.tx.package_kind == "arch":
            self._install_arch()
        else:
            raise ValueError(tr("Unsupported package type."))
        self._stage(92, tr("Verifying the installation…"))
        result = self.runner.run(
            self._enter(
                "python3",
                self.guest_helper,
                self.tx.package_kind,
                self.tx.package_name,
            )
        )
        payload = self._guest_result(result)
        self._stage(96, tr("Adding application shortcuts…"))
        count = export_desktops(self.store, self.tx, payload["desktops"], log=self._log)
        self._log(tr("Shortcuts created: {0}").format(count))
        try:
            self._remove_guest_temporary(self.runner)
        except (CommandError, TimeoutError) as exc:
            self.tx.warnings.append(tr("Some temporary environment files could not be removed."))
            self._log(str(exc))
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            self.tx.warnings.append(
                tr("The temporary local copy of the package could not be removed.")
            )
            self._log(str(exc))
        self._stage(99, tr("Finalizing the installation record…"))
        self.tx.status, self.tx.error = "installed", ""
        self.store.save(self.tx)
        self.progress(100, tr("Installation completed."))
        return self.tx

    @staticmethod
    def _guest_result(output: str) -> dict:
        lines = [line for line in output.splitlines() if line.startswith(guest.MARKER)]
        if len(lines) != 1:
            raise RuntimeError(tr("Could not verify the package status in the environment."))
        return json.loads(lines[0][len(guest.MARKER) :])

    def _remove_guest_temporary(self, runner: Runner) -> None:
        assert self.tx is not None
        paths = [self.guest_directory, *self._legacy_guest_paths()]
        runner.run(self._enter("sudo", "-n", "rm", "-f", "-r", "--", *paths))

    def _ensure_guest_idle(self, runner: Runner) -> None:
        self._prepare_guest_directory(runner)
        self._copy_with_runner(runner, Path(guest.__file__), self.guest_helper)
        busy = self._guest_result(
            runner.run(
                self._enter(
                    "sudo",
                    "-n",
                    "python3",
                    self.guest_helper,
                    "--busy",
                    self.guest_marker,
                    self._legacy_guest_paths()[-1],
                )
            )
        )
        if busy["busy"]:
            raise RuntimeError(
                tr("The operation in the environment is still running. Wait and try again.")
            )

    def _package_command(self, arguments: list[str], *, check: bool = True) -> str:
        assert self.tx is not None
        progress = PackageProgress(self.progress, install_label=tr("Installing the application…"))
        if not self.shared_environment:
            return progress.run(self.runner, self._enter(*arguments), check=check)
        # Keep the shared environment's package transaction running until it finishes.
        # Its PID marker also prevents cleanup after an exceptional client disconnect.
        if arguments[:2] != ["sudo", "-n"]:
            raise ValueError(tr("Package operations must use sudo only inside the environment."))
        return progress.run(
            self.runner,
            self._enter(
                "sudo",
                "-n",
                "python3",
                "-u",
                self.guest_helper,
                "--run",
                self.guest_marker,
                *arguments[2:],
            ),
            check=check,
        )

    def _install_deb(self) -> None:
        self._stage(-1, tr("Downloading components and installing the application…"))
        # Clear an interrupted dpkg configuration before asking apt to resume.
        # Unresolved dependencies may make configure return 1; apt --fix-broken then resolves them.
        if not self.shared_environment:
            self._package_command(
                [
                    "sudo",
                    "-n",
                    "env",
                    "DEBIAN_FRONTEND=noninteractive",
                    "dpkg",
                    "--force-confold",
                    "--configure",
                    "-a",
                ],
                check=False,
            )
        apt = [
            "sudo",
            "-n",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "-y",
            "-o",
            "Dpkg::Use-Pty=0",
            # APT marks its status FD close-on-exec before starting dpkg.
            # Using stdout (FD 1) breaks maintainer scripts that write there.
            # Keep normal stdout/stderr logs instead of reserving a standard FD.
            "-o",
            "Dpkg::Options::=--force-confold",
        ]
        if not self.shared_environment:
            apt.append("--fix-broken")
        try:
            self._package_command([*apt, "install", self.guest_package])
        except CommandError as exc:
            # Refresh stale indexes only for acquisition/resolution failures. Never
            # rerun an installation automatically after a dpkg or maintainer-script error.
            if not any(
                hint in exc.output.lower()
                for hint in (
                    "could not resolve",
                    "temporary failure resolving",
                    "failed to fetch",
                    "unable to locate package",
                    "has no installation candidate",
                )
            ) or any(hint in exc.output.lower() for hint in ("unpacking ", "setting up ", "dpkg:")):
                raise
            self._package_command([*apt, "-o", "APT::Update::Error-Mode=any", "update"])
            self._package_command([*apt, "install", self.guest_package])

    def _install_rpm(self) -> None:
        self._stage(-1, tr("Resolving dependencies and downloading components…"))
        self._package_command(
            [
                "sudo",
                "-n",
                "dnf",
                "-y",
                "install",
                "--downloadonly",
                self.guest_package,
            ]
        )
        self._stage(-1, tr("Installing the application\u2026"))
        self._package_command(["sudo", "-n", "dnf", "-y", "install", self.guest_package])

    def _install_arch(self) -> None:
        self._stage(-1, tr("Updating the Arch Linux environment\u2026"))
        # Refreshing indexes without upgrading would create an unsupported partial upgrade.
        self._package_command(["sudo", "-n", "pacman", "-Syu", "--noconfirm"])
        self._stage(-1, tr("Resolving dependencies and installing the application…"))
        self._package_command(["sudo", "-n", "pacman", "-U", "--noconfirm", self.guest_package])

    def _owned_container(self, runner: Runner) -> dict | None:
        assert self.tx is not None
        # First use ps to distinguish 'absent' from a broken engine/inspect failure.
        rows = json.loads(runner.run(["podman", "ps", "--all", "--format", "json"]))
        matches = [row for row in rows if self.tx.container in row.get("Names", [])]
        if not matches:
            return None
        data = json.loads(runner.run(["podman", "inspect", self.tx.container]))[0]
        labels = data.get("Config", {}).get("Labels", {}) or {}
        if self.tx.container_created and labels.get(OWNER_LABEL) != self.tx.id:
            raise RuntimeError(
                tr("The container does not belong to this installation; cleanup was blocked.")
            )
        if not self.tx.container_created and labels.get("manager") != "distrobox":
            raise RuntimeError(
                tr("The selected environment is no longer a Distrobox; the operation was blocked.")
            )
        if self.tx.container_id and data["Id"] != self.tx.container_id:
            raise RuntimeError(tr("The container was replaced; cleanup was blocked."))
        return data

    @staticmethod
    def _image_id(row: dict) -> str:
        return row.get("Id", row.get("ID", "")).removeprefix("sha256:")

    @staticmethod
    def _images(runner: Runner) -> list[dict]:
        return json.loads(runner.run(["podman", "images", "--no-trunc", "--format", "json"]))

    def _remove_new_image(self, runner: Runner) -> None:
        assert self.tx is not None
        tx = self.tx
        if not tx.images_scanned:
            return
        rows = self._images(runner)
        # Creation can fail after pulling but before the container ID was recorded.
        candidates = [row for row in rows if tx.image in (row.get("Names") or [])]
        image_id = tx.image_id.removeprefix("sha256:")
        if not image_id and len(candidates) == 1:
            image_id = self._image_id(candidates[0])
        if not image_id or image_id in tx.images_before:
            return
        if image_id not in [self._image_id(row) for row in rows]:
            return
        # No force/prune: another container/image may now depend on these layers.
        runner.run(["podman", "image", "rm", "--no-prune", image_id])
        if image_id in [self._image_id(row) for row in self._images(runner)]:
            raise RuntimeError(tr("The new image still exists; cleanup is incomplete."))

    def cleanup(self, tx: Transaction, *, preserve_environment: bool = False) -> None:
        """Destroy only owned resources. Preserve the journal if any deletion fails."""
        if tx.method == "appimage" and tx.package_kind == "appimage":
            from . import appimage

            return appimage.cleanup(self, tx)
        if tx.method != "distrobox":
            raise ValueError(tr("Container cleanup cannot undo a system installation."))
        with self.store.lock():
            self.tx = tx
            tx.preserve_environment_on_cleanup |= preserve_environment
            tx.status = "cleaning"
            self.store.save(tx)
            # Cancellation must never interrupt cleanup itself.
            runner = Runner(threading.Event(), self._log)
            try:
                owned = self._owned_container(runner)
                preserve = tx.preserve_environment_on_cleanup or self.shared_environment
                if owned and tx.package_install_started:
                    self._ensure_guest_idle(runner)
                if owned and preserve:
                    if tx.package_install_started:
                        state = self._guest_result(
                            runner.run(
                                self._enter(
                                    "sudo",
                                    "-n",
                                    "python3",
                                    self.guest_helper,
                                    "--remove",
                                    tx.package_kind,
                                    tx.package_name,
                                )
                            )
                        )
                        if state["present"]:
                            raise RuntimeError(
                                tr("The package still exists in the shared environment.")
                            )
                    self._remove_guest_temporary(runner)
                elif owned:
                    if not tx.image_id:
                        tx.image_id = owned.get("Image", "")
                        self.store.save(tx)
                    runner.run(["podman", "rm", "--force", "--time", "2", owned["Id"]])
                    self._remove_host_guest_files(runner)
                if not preserve and self._owned_container(runner) is not None:
                    raise RuntimeError(tr("The container still exists."))
                for name in tx.launchers:
                    path = self.store.launcher(tx, name)
                    path.unlink(missing_ok=True)
                    path.with_suffix(".tmp").unlink(missing_ok=True)
                if not preserve:
                    remove_container_icon(self.store, tx)
                    self._remove_new_image(runner)
                    if tx.managed_home:
                        self._remove_created_environment_files(runner, tx)
                self.store.remove_directory(tx)
                self.tx = None
            except Exception as exc:
                tx.status, tx.error = "cleanup_failed", str(exc)
                self.store.save(tx)
                raise

    def _remove_created_environment_files(self, runner: Runner, tx: Transaction) -> None:
        from .containers import (
            _check_home_references,
            _home_path,
            _read_record,
            _record_path,
            remove_tree,
        )

        directory = self.store.environment_directory(tx.container)
        if tx.home_created is True:
            try:
                home = _home_path(self.store, SimpleNamespace(name=tx.container))
                if home.exists():
                    _check_home_references(runner, tx, home)
            except (RuntimeError, ValueError) as exc:
                self._log(tr("The container's home folder was kept: {0}").format(exc))
            else:
                if home.exists():
                    remove_tree(runner, home)
        try:
            record = _read_record(self.store, tx.container)
        except (OSError, TypeError, ValueError):
            record = None
        if record is not None and record.owner_id == tx.id:
            _record_path(self.store, tx.container).unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass

    def _copy_with_runner(self, runner: Runner, source: Path, destination: str) -> None:
        assert self.tx is not None
        with host.accessible_file(source) as visible:
            runner.run(["podman", "cp", str(visible), f"{self.tx.container_id}:{destination}"])

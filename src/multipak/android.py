"""Manage Android APKs through the host Waydroid installation."""

import configparser
import hashlib
import os
import re
import shlex
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from . import host
from .android_apk import valid_package_id
from .android_bundle import extract_parts, inspect_android, select_parts
from .android_progress import AndroidProgress, AndroidStartupOutput
from .android_translation import install_translation, matches_abis, needs_arm, supported_abis
from .desktop import quote_exec
from .localization import tr
from .models import Transaction
from .process import Cancelled, Runner

WAYDROID = "/usr/bin/waydroid"
CONFIG = "/var/lib/waydroid/waydroid.cfg"
ROOTFS = "/var/lib/waydroid/rootfs"
SESSION_UNIT = "multipak-waydroid-session"
FAILURE = re.compile(
    r"^(?:\[[^\]\r\n]*\]\s*)*(?:ERROR\b|Traceback\b|"
    r"WayDroid session is stopped\b|Failed to access\b|WayDroid is not initialized\b)",
    re.I | re.M,
)

# Run one Android-side PackageInstaller session under one PolicyKit authorization.
# Only generated paths and integer sizes are passed as positional arguments.
SPLIT_INSTALL = r"""
session=
abandon() {
    if [ -n "$session" ]; then pm install-abandon "$session"; fi
}
trap abandon EXIT
trap 'exit 1' HUP INT TERM
created=$(pm install-create --user 0 -S "$1") || { echo "$created"; exit 1; }
echo "$created"
case "$created" in 'Success: created install session ['*']') ;; *) exit 1 ;; esac
session=${created##*[}
session=${session%]}
case "$session" in ''|*[!0-9]*) session=; exit 1 ;; esac
shift
index=0
while [ "$#" -gt 0 ]; do
    written=$(pm install-write -S "$1" "$session" "$index.apk" "$2") || {
        echo "$written"; exit 1;
    }
    echo "$written"
    case "$written" in Success*) ;; *) exit 1 ;; esac
    shift 2
    index=$((index + 1))
done
committed=$(pm install-commit "$session") || { echo "$committed"; exit 1; }
echo "$committed"
case "$committed" in Success*) ;; *) exit 1 ;; esac
session=
echo MULTIPAK_SPLITS_INSTALLED
"""


# Confirm removal through Android's package manager, which excludes retained-data entries.
REMOVE_ANDROID = r"""
set -eu
package=$1
list_packages() {
    packages=$(cmd package list packages --user 0) || exit 1
    printf '%s\n' "$packages" | grep -Fqx 'package:android' || exit 1
}
list_packages
if printf '%s\n' "$packages" | grep -Fqx "package:$package"; then
    pm uninstall -k "$package"
fi
list_packages
if printf '%s\n' "$packages" | grep -Fqx "package:$package"; then
    exit 1
fi
echo MULTIPAK_ANDROID_PACKAGE_REMOVED
if [ "${2-}" = check-empty ]; then
    if user_packages=$(cmd package list packages -3 --user 0); then
        echo MULTIPAK_ANDROID_USER_PACKAGES_BEGIN
        printf '%s\n' "$user_packages"
        echo MULTIPAK_ANDROID_USER_PACKAGES_END
    fi
fi
"""


class Waydroid:
    def __init__(self, runner):
        self.runner = runner

    def command(self, *args, timeout=60):
        output = self.runner.run([WAYDROID, *args], timeout=timeout)
        if FAILURE.search(output):
            raise RuntimeError(
                tr("Waydroid could not complete the operation: {0}").format(output[-4000:])
            )
        return output

    def privileged(self, *args, timeout=1800):
        if not host.which("pkexec"):
            raise RuntimeError(tr("System authorization requires PolicyKit (pkexec)."))
        from .privileged import call

        if args and args[0] == WAYDROID:
            output = call(self.runner, "waydroid", "personal", *args[1:], timeout=timeout)
        elif (
            len(args) == 3
            and args[0] == "/usr/bin/systemctl"
            and args[1] in ("start", "stop")
            and args[2] == "waydroid-container.service"
        ):
            output = call(self.runner, "service", "personal", args[1], timeout=timeout)
        else:
            raise ValueError("Unsupported privileged Waydroid operation")
        if FAILURE.search(output):
            raise RuntimeError(
                tr("Waydroid could not complete the operation: {0}").format(output[-4000:])
            )
        return output

    def _initialize_command(self, arguments, progress):
        if not host.which("pkexec"):
            raise RuntimeError(tr("System authorization requires PolicyKit (pkexec)."))
        from .privileged import call

        with (
            TemporaryDirectory(
                prefix="multipak-init-", dir=host.temporary_directory()
            ) as directory,
        ):
            watch_file = Path(directory) / "running"
            watch_file.touch(mode=0o600)
            output = call(
                self.runner,
                "initialize",
                str(watch_file),
                *arguments,
                timeout=1800,
                on_output=AndroidProgress(progress).feed,
                cancel_file=watch_file,
            )
        if FAILURE.search(output):
            raise RuntimeError(
                tr("Waydroid could not complete the operation: {0}").format(output[-4000:])
            )

    def initialize(self, progress=lambda *_: None):
        self._initialize_command(["init", "-s", "VANILLA"], progress)

    def ready(self, progress=lambda *_: None, *, initialize=True):
        if not host.which("waydroid"):
            raise RuntimeError(tr("Waydroid is not installed on this system."))
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError(tr("Android applications require a Wayland desktop session."))
        try:
            self.runner.run(["test", "-s", CONFIG])
            self.runner.run(["test", "-d", ROOTFS])
            initialized = True
        except RuntimeError:
            initialized = False
        if not initialized and not initialize:
            return False
        if not initialized:
            progress(10, tr("Downloading and preparing Android…"))
            self.initialize(progress)
            self.runner.run(["test", "-s", CONFIG])
            self.runner.run(["test", "-d", ROOTFS])
        progress(35, tr("Starting the Android environment…"))
        state = self.runner.run(
            ["systemctl", "is-active", "waydroid-container.service"], check=False
        )
        if state.strip() != "active":
            self.privileged(
                "/usr/bin/systemctl", "start", "waydroid-container.service", timeout=120
            )
        status = self.command("status")
        if not re.search(r"Session:\s+RUNNING", status):
            if not host.which("systemd-run"):
                raise RuntimeError(tr("Starting Android in the background requires systemd-run."))
            unit = self.runner.run(
                ["systemctl", "--user", "is-active", SESSION_UNIT], check=False
            ).strip()
            if unit not in ("active", "activating"):
                if unit == "failed":
                    self.runner.run(
                        ["systemctl", "--user", "reset-failed", SESSION_UNIT], check=False
                    )
                self.runner.run(
                    [
                        "systemd-run",
                        "--user",
                        "--collect",
                        f"--unit={SESSION_UNIT}",
                        "--service-type=exec",
                        f"--setenv=WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}",
                        "--",
                        WAYDROID,
                        "session",
                        "start",
                    ],
                    timeout=30,
                )
        with AndroidStartupOutput(self.runner, progress):
            deadline = time.monotonic() + 180
            last_error = ""
            while time.monotonic() < deadline:
                try:
                    status = self.command("status")
                    if re.search(r"Session:\s+RUNNING", status):
                        self.apps()
                        break
                except (RuntimeError, TimeoutError) as error:
                    last_error = str(error)
                if self.runner.cancelled.wait(1):
                    raise Cancelled
            else:
                raise RuntimeError(
                    tr(
                        "Android did not become ready. Check the Waydroid graphics and "
                        "system configuration. {0}"
                    ).format(last_error)
                )
        if not initialized:
            self.command("prop", "set", "persist.waydroid.multi_windows", "true")
        return True

    def apps(self):
        output = self.command("app", "list")
        result = {}
        name = ""
        for line in output.splitlines():
            if line.startswith("Name: "):
                name = line[6:].strip()
            elif line.startswith("packageName: "):
                package_id = line[13:].strip()
                if not valid_package_id(package_id):
                    raise RuntimeError(tr("Waydroid returned an invalid application identifier."))
                result[package_id] = name or package_id
        if not result and "Failed to get service waydroidplatform" in output:
            raise RuntimeError(tr("Android is still starting…"))
        return result

    def compatible(self, package):
        sdk = self.command("prop", "get", "ro.build.version.sdk").strip()
        if not sdk.isdigit():
            raise RuntimeError(tr("Could not identify the Android version."))
        if package.android_min_sdk > int(sdk):
            raise RuntimeError(
                tr("This application requires a newer Android version (API {0}).").format(
                    package.android_min_sdk
                )
            )

        abis = supported_abis(self)
        if not matches_abis(package, abis):
            raise RuntimeError(
                tr(
                    "This APK requires {0}, but the Android environment supports {1}. "
                    "A compatible ARM translation layer may be required."
                ).format(package.architecture, ", ".join(sorted(abis)))
            )

    def staging_directory(self):
        return host.directories()[0] / "waydroid/data/waydroid_tmp"

    def install_bundle(self, package, staged, *, install_started=lambda: None):
        abis = self.command("prop", "get", "ro.product.cpu.abilist").strip().split(",")
        density = self.privileged(WAYDROID, "shell", "--", "wm", "density", timeout=30)
        values = re.findall(r"(?:Physical|Override) density:\s*(\d+)", density)
        if not values:
            density = self.command("prop", "get", "ro.sf.lcd_density").strip()
            values = [density] if density.isdigit() else []
        if not values or not 0 < int(values[-1]) <= 10000:
            raise RuntimeError(tr("Could not identify the Android display density."))
        selected = select_parts(package, abis, int(values[-1]))
        sdk = int(self.command("prop", "get", "ro.build.version.sdk").strip())
        if any(part.min_sdk > sdk for part in selected):
            raise RuntimeError(tr("A part of this bundle requires a newer Android version."))
        from . import android_staging

        with android_staging.directory(self, "multipak-") as directory:

            def checkpoint():
                if self.runner.cancelled.is_set() and not self.runner.defer_cancellation:
                    raise Cancelled

            files = extract_parts(staged, selected, directory, checkpoint=checkpoint)
            android_staging.publish(self, directory)
            args = [str(sum(part.size for part in selected))]
            for path, part in zip(files, selected, strict=True):
                args += [str(part.size), f"/data/waydroid_tmp/{path.parent.name}/{path.name}"]
            checkpoint()
            install_started()
            output = self.privileged(
                WAYDROID,
                "shell",
                "--",
                "sh",
                "-c",
                SPLIT_INSTALL,
                "multipak-splits",
                *args,
                timeout=600,
            )
            if "MULTIPAK_SPLITS_INSTALLED" not in output.splitlines():
                raise RuntimeError(
                    tr("Android could not install the bundle: {0}").format(output[-4000:])
                )

    def remove_package(self, package_id, *, check_empty=False):
        if not valid_package_id(package_id):
            raise ValueError(tr("Select an Android APK."))
        output = self.privileged(
            WAYDROID,
            "shell",
            "--",
            "sh",
            "-c",
            REMOVE_ANDROID,
            "multipak-remove",
            package_id,
            *(["check-empty"] if check_empty else []),
            timeout=120,
        )
        if "MULTIPAK_ANDROID_PACKAGE_REMOVED" not in output.splitlines():
            raise RuntimeError(tr("Waydroid did not confirm the application change."))
        if check_empty:
            from .android_containers import no_user_apps

            return no_user_apps(output)

    def wait_for_app(self, package_id, *, present):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            apps = self.apps()
            if (package_id in apps) == present:
                return apps
            if self.runner.cancelled.wait(0.5) and not self.runner.defer_cancellation:
                raise Cancelled
        raise RuntimeError(tr("Waydroid did not confirm the application change."))


def transaction_waydroid(engine, tx, runner=None):
    if tx.android_instance_id:
        from .android_instances import InstanceWaydroid

        return InstanceWaydroid(runner or engine.runner, tx.android_instance_id)
    return Waydroid(runner or engine.runner)


def _launcher(engine, tx, package):
    if tx.android_instance_id:
        from .android_instances import CLI

        expected_argv = [
            CLI,
            "--instance",
            tx.android_instance_id,
            "app",
            "launch",
            tx.android_package_id,
        ]
    else:
        expected_argv = ["waydroid", "app", "launch", tx.android_package_id]
    from .android_menu import register_shortcut

    name = (
        f"multipak-waydroid.{tx.android_instance_id}.{tx.android_package_id}.desktop"
        if tx.android_instance_id
        else f"multipak-{tx.id}-android.desktop"
    )
    # Reuse existing shortcuts and include them in our Android menu.
    paths = (
        [engine.store.launcher(tx, name)]
        if tx.android_instance_id
        else engine.store.applications.glob("*.desktop")
    )
    for path in paths:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
            entry = parser["Desktop Entry"]
            argv = shlex.split(entry.get("Exec", ""))
            if (
                len(argv) == len(expected_argv)
                and entry.get("Type") == "Application"
                and os.path.basename(argv[0]) == os.path.basename(expected_argv[0])
                and argv[1:] == expected_argv[1:]
                and entry.get("Hidden", "false").lower() != "true"
                and entry.get("NoDisplay", "false").lower() != "true"
            ):
                if tx.android_instance_id:
                    tx.launchers = [name]
                    engine.store.save(tx)
                register_shortcut(engine.store.applications, path.name)
                return
        except (OSError, UnicodeError, ValueError, configparser.Error, KeyError):
            continue
    path = engine.store.launcher(tx, name)
    tx.launchers = [name]
    engine.store.save(tx)
    # Desktop string values cannot contain unescaped newlines or backslashes.
    label = re.sub(r"[\x00-\x1f\x7f]", " ", package.name).replace("\\", "\\\\")
    icon = "waydroid"
    if package.icon_data:
        path_icon = engine.store.directory(tx.id) / "icon.png"
        path_icon.write_bytes(package.icon_data)
        icon = str(path_icon).replace("\\", "\\\\")
    if tx.android_instance_id:
        prefix = " ".join(quote_exec(arg) for arg in expected_argv[:3])
        label += " — " + re.sub(r"[\x00-\x1f\x7f]", " ", tx.android_environment_label).replace(
            "\\", "\\\\"
        )
    else:
        prefix = quote_exec(WAYDROID)
    text = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={label}\n"
        f"Exec={prefix} app launch {quote_exec(tx.android_package_id)}\n"
        f"Icon={icon}\nTerminal=false\nCategories=X-MultiPak-Android;\n"
        f"StartupWMClass=waydroid.{tx.android_package_id}\n"
        f"X-MultiPak-Android-Package={tx.android_package_id}\n"
    )
    temporary = path.with_suffix(f".{tx.id}.tmp")
    temporary.write_text(text)
    if tx.android_instance_id:
        # Android exports asynchronously. Use its filename, without overwriting a
        # name and icon that may have arrived while this fallback was prepared.
        try:
            os.link(temporary, path)
        except FileExistsError:
            from .android_menu import matches_shortcut

            if not matches_shortcut(path, expected_argv):
                raise ValueError(
                    tr("This shortcut does not belong to this installation.")
                ) from None
        finally:
            temporary.unlink(missing_ok=True)
    else:
        temporary.replace(path)
    register_shortcut(engine.store.applications, name)


def install(
    engine,
    package,
    *,
    allow_translation=False,
    instance_id="",
    android_profile="",
    android_label="",
):
    if package.kind != "apk" or not valid_package_id(package.desktop_entry):
        raise ValueError(tr("Select an Android APK."))
    if os.geteuid() == 0:
        raise RuntimeError(tr("Run MultiPak as a regular user."))
    with engine.store.lock():
        tx = Transaction(
            uuid.uuid4().hex,
            android_label or "Waydroid",
            "",
            package.name,
            package.version,
            "apk",
            "",
            method="waydroid",
            container_created=False,
            android_package_id=package.desktop_entry,
            android_translation_allowed=bool(allow_translation),
            android_instance_id=instance_id,
            android_profile=android_profile,
            android_environment_label=android_label,
        )
        engine.tx = tx
        engine.store.save(tx)
        try:
            engine._stage(2, tr("Preparing a copy of the package…"))
            staged = engine.store.directory(tx.id) / "package.apk"
            engine._copy_package(package.path, staged)
            copied = inspect_android(staged)
            if copied != replace(package, path=staged):
                raise ValueError(tr("The file has changed. Open the package again."))
            with staged.open("rb") as stream:
                tx.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            engine.store.save(tx)
            return _finish(engine, tx, copied, staged)
        except Exception as error:
            tx.status, tx.error = "failed", str(error)
            engine.store.save(tx)
            raise


def _finish(engine, tx, package, staged):
    waydroid = transaction_waydroid(engine, tx)
    if tx.android_instance_id:
        from .android_instances import create, discover

        if not any(e["id"] == tx.android_instance_id for e in discover(engine.runner)):
            create(
                engine.runner,
                tx.android_profile,
                tx.android_environment_label,
                instance_id=tx.android_instance_id,
                progress=engine._stage,
            )
    waydroid.ready(engine._stage)
    abis = supported_abis(waydroid)
    if package.android_variants:
        from .android_bundle import select_variant
        from .android_translation import ARM_ABIS

        sdk = int(waydroid.command("prop", "get", "ro.build.version.sdk").strip())
        output = waydroid.privileged(WAYDROID, "shell", "--", "wm", "density", timeout=30)
        densities = re.findall(r"(?:Physical|Override) density:\s*(\d+)", output)
        if not densities:
            raise RuntimeError(tr("Could not identify the Android display density."))
        density = int(densities[-1])
        try:
            package = select_variant(package, sdk, abis, density)
        except ValueError:
            package = select_variant(package, sdk, list(abis) + list(ARM_ABIS), density)
    if needs_arm(package, abis):
        if not tx.android_translation_allowed:
            raise RuntimeError(
                tr(
                    "This application requires an ARM translation layer. "
                    "Reopen the package and allow its installation in the review screen."
                )
            )
        # Reject an incompatible Android version before downloading a bridge.
        sdk = waydroid.command("prop", "get", "ro.build.version.sdk").strip()
        if not sdk.isdigit() or package.android_min_sdk > int(sdk):
            waydroid.compatible(package)
        install_translation(waydroid, engine._stage)
    waydroid.compatible(package)
    if tx.android_package_id in waydroid.apps():
        if not tx.android_package_owned:
            raise RuntimeError(
                tr(
                    "This application is already installed in Android. "
                    "The existing installation was preserved."
                )
            )
    else:
        engine._stage(65, tr("Installing the Android application…"))

        def install_started():
            tx.android_package_owned = tx.package_install_started = True
            engine.store.save(tx)
            engine.runner.defer_cancellation = True

        try:
            if package.android_parts:
                waydroid.install_bundle(package, staged, install_started=install_started)
            else:
                install_started()
                with host.accessible_file(staged) as accessible:
                    waydroid.command("app", "install", str(accessible), timeout=180)
            waydroid.wait_for_app(tx.android_package_id, present=True)
        finally:
            engine.runner.defer_cancellation = False
    if package.android_expansions:
        from .android_expansions import install_expansions

        engine._stage(85, tr("Copying additional Android application data…"))
        engine.runner.defer_cancellation = True
        try:
            install_expansions(waydroid, package, staged)
        finally:
            engine.runner.defer_cancellation = False
    engine._stage(90, tr("Adding the application to the launcher…"))
    _launcher(engine, tx, package)
    tx.status, tx.stage, tx.error = "installed", tr("Installation completed"), ""
    engine.store.save(tx)
    engine.progress(100, tx.stage)
    return tx


def retry(engine, tx):
    if tx.method != "waydroid" or not valid_package_id(tx.android_package_id):
        raise ValueError(tr("Select an Android installation managed by MultiPak."))
    with engine.store.lock():
        engine.tx = tx
        staged = engine.store.directory(tx.id) / "package.apk"
        with staged.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if not tx.sha256 or digest != tx.sha256:
            raise RuntimeError(
                tr("The copy is incomplete or has changed. Cancel and reopen the package.")
            )
        package = inspect_android(staged)
        if package.desktop_entry != tx.android_package_id:
            raise ValueError(tr("The file has changed. Open the package again."))
        try:
            return _finish(engine, tx, package, staged)
        except Exception as error:
            tx.status, tx.error = "failed", str(error)
            engine.store.save(tx)
            raise


def cleanup(engine, transaction, *, check_empty=False):
    empty = False
    with engine.store.lock():
        tx = next(
            (
                item
                for item in engine.store.transactions()
                if item.id == transaction.id
                and item.method == "waydroid"
                and item.android_package_id == transaction.android_package_id
            ),
            None,
        )
        if tx is None or not valid_package_id(tx.android_package_id):
            raise ValueError(tr("Select an Android installation managed by MultiPak."))
        engine.tx = tx
        try:
            if tx.android_package_owned:
                engine.progress(10, tr("Starting the Android environment…"))
                waydroid = transaction_waydroid(engine, tx, Runner(threading.Event(), engine._log))
                if waydroid.ready(engine.progress, initialize=False):
                    if check_empty:
                        empty = waydroid.remove_package(tx.android_package_id, check_empty=True)
                    else:
                        waydroid.remove_package(tx.android_package_id)
            for name in tx.launchers:
                engine.store.launcher(tx, name).unlink(missing_ok=True)
            engine.store.remove_directory(tx)
        except Exception as error:
            tx.status, tx.error = "cleanup_failed", str(error)
            engine.store.save(tx)
            raise
    return empty

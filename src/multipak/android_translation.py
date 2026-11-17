"""Read-only Android installation planning and opt-in native bridge setup."""

import hashlib
import platform
import re
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from . import host
from .android_translation_helper import ARCHIVE_SHA256, ARCHIVE_URL, MAX_ARCHIVE
from .localization import tr
from .models import AndroidPlan
from .process import Cancelled, Runner

ARM_ABIS = ("arm64-v8a", "armeabi-v7a", "armeabi")
NATIVE_ABIS = {
    "x86_64": ("x86_64", "x86"),
    "i686": ("x86",),
    "aarch64": ("arm64-v8a", "armeabi-v7a", "armeabi"),
    "armv7l": ("armeabi-v7a", "armeabi"),
}


def matches_abis(package, abis):
    if package.android_variants:
        from dataclasses import replace

        return any(
            matches_abis(replace(package, android_variants=(), android_parts=v["parts"]), abis)
            for v in package.android_variants
        )
    if package.android_parts:
        parts = package.android_parts
        fixed = [p for p in parts if p.kind not in ("abi", "density")]
        groups = {p.target for p in parts if p.kind == "abi"}
        return any(
            all(not part.abis or abi in part.abis for part in fixed)
            and all(
                any(p.kind == "abi" and p.target == g and p.qualifier == abi for p in parts)
                for g in groups
            )
            for abi in abis
        )
    return package.architecture == "any" or bool(set(package.architecture.split(",")) & set(abis))


def needs_arm(package, abis):
    return not matches_abis(package, abis) and matches_abis(package, ARM_ABIS)


def supported_abis(waydroid):
    abis = waydroid.command("prop", "get", "ro.product.cpu.abilist").strip().split(",")
    native = NATIVE_ABIS.get(platform.machine(), ())
    if any(abi in ARM_ABIS and abi not in native for abi in abis):
        bridge = waydroid.command("prop", "get", "ro.dalvik.vm.native.bridge").strip()
        if bridge in ("", "0"):
            return [abi for abi in abis if abi in native]
    return abis


def preview_plan(package):
    native = NATIVE_ABIS.get(platform.machine(), ())
    return AndroidPlan(translation="check" if needs_arm(package, native) else "native")


def inspect_plan(package, runner):
    """Never initialize, start or authorize Android while inspecting a package."""
    from .android import CONFIG, ROOTFS, Waydroid

    fallback = preview_plan(package)
    try:
        runner.run(["test", "-s", CONFIG])
        runner.run(["test", "-d", ROOTFS])
    except RuntimeError:
        return fallback
    try:
        waydroid = Waydroid(runner)
        if not re.search(r"Session:\s+RUNNING", waydroid.command("status", timeout=8)):
            return AndroidPlan(initialized=True, translation=fallback.translation)
        abis = (
            waydroid.command("prop", "get", "ro.product.cpu.abilist", timeout=8).strip().split(",")
        )
        sdk = waydroid.command("prop", "get", "ro.build.version.sdk", timeout=8).strip()
        bridge = waydroid.command("prop", "get", "ro.dalvik.vm.native.bridge", timeout=8).strip()
        native = NATIVE_ABIS.get(platform.machine(), ())
        # A property advertising ARM alone does not confirm a native bridge.
        usable = abis if bridge not in ("", "0") else [a for a in abis if a in native]
        if matches_abis(package, usable):
            translated = not matches_abis(package, native)
            return AndroidPlan(True, "available" if translated else "native", bridge)
        if needs_arm(package, usable):
            if bridge not in ("", "0"):
                return AndroidPlan(
                    True,
                    "unsupported",
                    bridge,
                    tr(
                        "The existing ARM translation layer does not provide the architecture "
                        "required by this application. It will be preserved."
                    ),
                )
            if sdk != "33" or platform.machine() != "x86_64":
                return AndroidPlan(
                    True,
                    "unsupported",
                    explanation=tr(
                        "Automatic ARM translation setup currently supports Android 13 on x86_64."
                    ),
                )
            return AndroidPlan(True, "required", "libndk_translation")
        return AndroidPlan(
            True,
            "unsupported",
            explanation=tr("This application has no compatible Android architecture."),
        )
    except (OSError, RuntimeError, TimeoutError):
        return AndroidPlan(initialized=True, translation=fallback.translation)


def download(path, runner, progress, *, opener=urlopen):
    progress(-1, tr("Connecting to the ARM translation download…"))
    digest, received = hashlib.sha256(), 0
    with opener(ARCHIVE_URL, timeout=30) as response, Path(path).open("xb") as output:
        if not response.geturl().startswith("https://"):
            raise ValueError(tr("The ARM translation download requires HTTPS."))
        total = int(response.headers.get("Content-Length", "0"))
        if total > MAX_ARCHIVE:
            raise ValueError(tr("The ARM translation download exceeds the size limit."))
        while chunk := response.read(256 * 1024):
            if runner.cancelled.is_set():
                raise Cancelled
            received += len(chunk)
            if received > MAX_ARCHIVE:
                raise ValueError(tr("The ARM translation download exceeds the size limit."))
            digest.update(chunk)
            output.write(chunk)
            percent = min(100, received * 100 // total) if total > 0 else -1
            progress(
                percent,
                tr("Downloading the ARM translation layer…")
                + "\n"
                + tr("{0:.1f} MB downloaded").format(received / (1024 * 1024)),
            )
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise ValueError(tr("The ARM translation download failed its integrity check."))


def restart_android(waydroid, progress):
    from .android import SESSION_UNIT, Waydroid

    # A cancellation after the authorized change must not leave shared Android
    # stopped. Finish the restart with a separate runner, then honour cancellation.
    runner = Runner(threading.Event(), waydroid.runner.log)
    if getattr(waydroid, "instance_id", ""):
        from .android_instances import InstanceWaydroid

        restarting = InstanceWaydroid(runner, waydroid.instance_id)
    else:
        restarting = Waydroid(runner)
    session_unit = getattr(restarting, "session_unit", SESSION_UNIT)

    def report(*args):
        try:
            progress(*args)
        except Cancelled:
            pass

    unit = restarting.runner.run(
        ["systemctl", "--user", "is-active", session_unit], check=False
    ).strip()
    if unit in ("active", "activating", "deactivating"):
        restarting.runner.run(["systemctl", "--user", "stop", session_unit], timeout=60)
    restarting.ready(report, initialize=False)
    bridge = restarting.command("prop", "get", "ro.dalvik.vm.native.bridge").strip()
    if bridge != "libndk_translation.so":
        raise RuntimeError(tr("The ARM translation layer did not become active."))


def install_translation(waydroid, progress):

    runner = waydroid.runner
    sdk = waydroid.command("prop", "get", "ro.build.version.sdk").strip()
    bridge = waydroid.command("prop", "get", "ro.dalvik.vm.native.bridge").strip()
    if sdk != "33" or platform.machine() != "x86_64":
        raise RuntimeError(
            tr("Automatic ARM translation setup currently supports Android 13 on x86_64.")
        )
    if bridge not in ("", "0"):
        raise RuntimeError(tr("An existing Android native bridge was preserved."))
    if "sse4_2" not in Path("/proc/cpuinfo").read_text():
        raise RuntimeError(tr("This processor is not supported by the ARM translation installer."))
    from .privileged import call

    with TemporaryDirectory(prefix="multipak-arm-", dir=host.temporary_directory()) as temporary:
        archive = Path(temporary) / "translation.zip"
        download(archive, runner, progress)
        if runner.cancelled.is_set():
            raise Cancelled
        progress(-1, tr("Installing the ARM translation layer and restarting Android…"))
        with host.accessible_file(archive) as accessible:
            # Complete/roll back this short shared-environment change before cancelling.
            runner.defer_cancellation = True
            try:
                output = call(
                    runner,
                    "arm",
                    str(accessible),
                    *([waydroid.instance_id] if getattr(waydroid, "instance_id", "") else []),
                    timeout=600,
                )
                if "MULTIPAK_ARM_TRANSLATION_INSTALLED" not in output.splitlines():
                    raise RuntimeError(tr("Waydroid did not confirm the ARM translation setup."))
                restart_android(waydroid, progress)
            finally:
                runner.defer_cancellation = False
        if runner.cancelled.is_set():
            raise Cancelled

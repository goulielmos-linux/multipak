# Modified by MultiPak on 2026-11-10.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Input preferences confined to the explicitly selected MultiPak instance."""

import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from multipak_instance import current as instance

TOUCH = "persist.waydroid.fake_touch"
KEYBOARD = "show_ime_with_hard_keyboard"
SECURE = 0  # id.waydro.waydroid.Platform.SETTINGS_SECURE
GLOBAL = 2
POLICY = "policy_control"
ARROWS = "multipak_arrow_packages"
MAPPER = "io.github.multipak.gestures"
SERVICE = MAPPER + "/.ArrowService"
ACCESSIBILITY = "enabled_accessibility_services"


def ensure_mapper(platform, manager):
    source = Path(__file__).parent / "assets/multipak-gestures.apk"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with preferences() as path:
        values = load_preferences(path)
        if MAPPER in manager.ListPackages() and values.get("gestures_apk_sha256") == digest:
            return
        shared = instance().exchange / "apks"
        shared.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="multipak-gestures-", dir=shared) as directory:
            os.chmod(directory, 0o755)
            target = Path(directory) / "gestures.apk"
            shutil.copyfile(source, target)
            result = platform.installApp(f"/data/waydroid_tmp/{target.parent.name}/{target.name}")
            if result != 0 or MAPPER not in manager.ListPackages():
                raise RuntimeError("Could not install the Android gesture component.")
        values["gestures_apk_sha256"] = digest
        save_preferences(path, values)


def set_arrows(platform, package, enabled):
    validate_package(package)
    packages = set(filter(None, (platform.settingsGetString(SECURE, ARROWS) or "").split(",")))
    packages.discard(package)
    if enabled:
        packages.add(package)
    services = list(filter(None, (platform.settingsGetString(SECURE, ACCESSIBILITY) or "").split(":")))
    # Preserve other accessibility services and every other application's profile.
    services = [s for s in services if s not in (SERVICE, MAPPER + "/" + MAPPER + ".ArrowService")]
    if packages:
        services.append(SERVICE)
    values = {ARROWS: ",".join(sorted(packages)), ACCESSIBILITY: ":".join(services)}
    if packages:
        values["accessibility_enabled"] = "1"
    # Package-added processing can reset accessibility immediately after the
    # first install. Require stable readback while Android registers the service.
    stable = 0
    for _ in range(12):
        for key, value in values.items():
            if platform.settingsGetString(SECURE, key) != value:
                platform.settingsPutString(SECURE, key, value)
                stable = 0
        time.sleep(0.2)
        if all(platform.settingsGetString(SECURE, key) == value for key, value in values.items()):
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
    raise RuntimeError("Android did not confirm the arrow gesture preference.")


def validate_package(package):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
        raise ValueError("Invalid Android package ID")


@contextmanager
def preferences():
    directory = instance().user_state
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "input-preferences.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory / "input-preferences.json"


def load_preferences(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid Android input preferences")
    return value


def save_preferences(path, values):
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(values, output)
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def initialize_keyboard(platform):
    # Initialize only once. Later changes made inside Android remain respected.
    with preferences() as path:
        if not path.exists():
            set_keyboard(platform, False)
            save_preferences(path, {"keyboard": False})


def launch_policy(platform, multiwin):
    with preferences() as path:
        values = load_preferences(path)
        if "navigation" in values:
            del values["navigation"]
            save_preferences(path, values)
    # The experimental navigation toggle was removed. Restore Waydroid's
    # launch policy instead of carrying its saved override into future apps.
    value = "immersive.status=*" if multiwin == "false" else "immersive.full=*"
    platform.settingsPutString(GLOBAL, POLICY, value)


def set_keyboard(platform, enabled):
    value = "1" if enabled else "0"
    platform.settingsPutString(SECURE, KEYBOARD, value)
    if platform.settingsGetString(SECURE, KEYBOARD) != value:
        raise RuntimeError("Android did not confirm the keyboard preference.")


def touch_value(previous, package, enabled):
    validate_package(package)
    entries = [item for item in previous.split(",") if item]
    inherited = any(item != package and fnmatch.fnmatchcase(package, item) for item in entries)
    if not enabled and inherited:
        raise ValueError("A shared mouse-as-touch rule already includes this application.")
    entries = [item for item in entries if item != package]
    if enabled and not inherited:
        entries.append(package)
    value = ",".join(entries)
    if len(value.encode()) > 91:
        raise ValueError("The mouse-as-touch application list has reached Android's limit.")
    return value


def read(platform, package):
    validate_package(package)
    value = platform.getprop(TOUCH, "")
    if value is None:
        raise RuntimeError("Android did not return its input preferences.")
    return {
        "touch": any(fnmatch.fnmatchcase(package, item) for item in value.split(",") if item),
        "keyboard": platform.settingsGetString(SECURE, KEYBOARD) == "1",
        "navigation": platform.settingsGetString(GLOBAL, POLICY) != "immersive.full=*",
        "arrows": (
            package in (platform.settingsGetString(SECURE, ARROWS) or "").split(",")
            and any(s in (SERVICE, MAPPER + "/" + MAPPER + ".ArrowService") for s in
                    (platform.settingsGetString(SECURE, ACCESSIBILITY) or "").split(":"))
            and platform.settingsGetString(SECURE, "accessibility_enabled") == "1"
        ),
    }


def apply(platform, package, touch, keyboard, navigation=None, arrows=None):
    validate_package(package)
    with preferences() as path:
        previous = platform.getprop(TOUCH, "")
        if previous is None:
            raise RuntimeError("Android did not return its input preferences.")
        updated = touch_value(previous, package, touch)
        platform.setprop(TOUCH, updated)
        if platform.getprop(TOUCH, "") != updated:
            raise RuntimeError("Android did not confirm the mouse preference.")
        set_keyboard(platform, keyboard)
        values = load_preferences(path)
        values["keyboard"] = keyboard
        if navigation is not None:
            policy = "immersive.status=*" if navigation else "immersive.full=*"
            platform.settingsPutString(GLOBAL, POLICY, policy)
            if platform.settingsGetString(GLOBAL, POLICY) != policy:
                raise RuntimeError("Android did not confirm the navigation preference.")
            values["navigation"] = navigation
        save_preferences(path, values)
        if arrows is not None:
            set_arrows(platform, package, arrows)
    return {**read(platform, package), "touch_changed": previous != updated}


def companion(package):
    validate_package(package)
    if os.getuid() != instance().owner or os.environ.get("MULTIPAK_NO_ANDROID_SETTINGS"):
        return
    command = Path(instance().home) / ".local/share/multipak-runtime/bin/multipak-android-settings"
    if command.is_file():
        subprocess.Popen(
            [str(command), "--instance", instance().id, "--package", package],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def run(args):
    import tools.helpers.ipc
    from tools.interfaces import IPlatform

    validate_package(args.PACKAGE)
    tools.helpers.ipc.DBusSessionService()
    manager = tools.helpers.ipc.DBusContainerService()
    frozen = manager.GetSession().get("state") == "FROZEN"
    manager.Unfreeze()
    try:
        platform = IPlatform.get_service(args)
        if not platform:
            raise RuntimeError("Android is not ready. Open the application and try again.")
        if args.mode == "get":
            result = read(platform, args.PACKAGE)
        else:
            navigation = None if args.navigation is None else args.navigation == "on"
            arrows = None if args.arrows is None else args.arrows == "on"
            if arrows:
                ensure_mapper(platform, manager)
            result = apply(platform, args.PACKAGE, args.touch == "on", args.keyboard == "on", navigation, arrows)
    finally:
        if frozen:
            manager.Freeze()
    print(json.dumps(result))

# Modified by MultiPak on 2026-11-10.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Input preferences confined to the explicitly selected MultiPak instance."""

import fcntl
import fnmatch
import hashlib
import json
import logging
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
# The MultiPak Android Controller app: arrow gestures, links to Linux and media controls.
MAPPER = "io.github.goulielmos_linux.multipak.controller"
SERVICE = MAPPER + "/.ArrowService"
# Its ID in earlier test builds; ensure_mapper() moves a container from it to MAPPER.
OLD_MAPPER = "io.github.multipak.gestures"
APK_DIGEST = "controller_apk_sha256"
ACCESSIBILITY = "enabled_accessibility_services"
# Read by the helper app: links from these apps stay in Android.
LINKS_ANDROID = "multipak_links_android"


def ensure_mapper(platform, manager):
    """Install or update the controller app; True when it changed. A container that still
    has the app under its old ID gets the new one, keeps its arrow gestures, and loses
    the old app."""
    source = Path(__file__).parent / "assets/multipak-controller.apk"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with preferences() as path:
        values = load_preferences(path)
        packages = manager.ListPackages()
        current = MAPPER in packages and values.get(APK_DIGEST) == digest
        if current and OLD_MAPPER not in packages:
            return False
        if not current:
            shared = instance().exchange / "apks"
            shared.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="multipak-controller-", dir=shared) as directory:
                os.chmod(directory, 0o755)
                target = Path(directory) / "controller.apk"
                shutil.copyfile(source, target)
                result = platform.installApp(f"/data/waydroid_tmp/{target.parent.name}/{target.name}")
                # A new app reaches the package list a moment after installApp returns.
                if result != 0 or not _listed(manager, MAPPER, True):
                    raise RuntimeError("Could not install the MultiPak Android Controller.")
        if OLD_MAPPER in packages:
            arrows = [p for p in (platform.settingsGetString(SECURE, ARROWS) or "").split(",") if p]
            if arrows:
                set_arrows(platform, arrows[0], True)  # Points accessibility at the new app.
            # Waydroid's removeApp leaves it on Android 13; the root service runs pm's own.
            manager.RemoveOldHelper()
            if _listed(manager, OLD_MAPPER, False):
                raise RuntimeError("Could not remove the old MultiPak Android component.")
        values[APK_DIGEST] = digest
        values.pop("gestures_apk_sha256", None)
        save_preferences(path, values)
    return True


def _listed(manager, package, wanted, attempts=20):
    """Whether package is listed, after waiting up to 10 seconds for it to be listed
    (wanted=True) or to leave the list (wanted=False): Android finishes installing and
    removing apps a moment after the call returns."""
    for attempt in range(attempts):
        if (package in manager.ListPackages()) == wanted:
            return wanted
        if attempt < attempts - 1:
            time.sleep(0.5)
    return not wanted


def _arrow_services():
    # Android writes a component either short or in full; the old ID never comes back.
    return {
        f"{app}/{name}" for app in (MAPPER, OLD_MAPPER) for name in (".ArrowService", app + ".ArrowService")
    }


def set_arrows(platform, package, enabled):
    validate_package(package)
    packages = set(filter(None, (platform.settingsGetString(SECURE, ARROWS) or "").split(",")))
    packages.discard(package)
    if enabled:
        packages.add(package)
    services = list(filter(None, (platform.settingsGetString(SECURE, ACCESSIBILITY) or "").split(":")))
    # Preserve other accessibility services and every other application's profile.
    services = [s for s in services if s not in _arrow_services()]
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


def links_in_android(platform):
    return set(filter(None, (platform.settingsGetString(SECURE, LINKS_ANDROID) or "").split(",")))


def set_links_in_android(platform, package, enabled):
    validate_package(package)
    packages = links_in_android(platform)
    packages.discard(package)
    if enabled:
        packages.add(package)
    value = ",".join(sorted(packages))
    platform.settingsPutString(SECURE, LINKS_ANDROID, value)
    if (platform.settingsGetString(SECURE, LINKS_ANDROID) or "") != value:
        raise RuntimeError("Android did not confirm the links preference.")


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


def keyboard_wanted():
    """The owner's choice from the container's menu; off until chosen."""
    with preferences() as path:
        return load_preferences(path).get("keyboard") is True


def apply_keyboard(platform):
    # Each app launch: the container's menu decides, not a change made inside Android.
    set_keyboard(platform, keyboard_wanted())


def media_volume_max(output):
    # "volume is 4 in range [0..15]" (Android 13 --get) or
    # "invalid volume index 999 for stream 3 (should be in [0..15])" (refused --set).
    match = re.search(r"\[\d+\.\.(\d+)\]", output)
    return int(match.group(1)) if match else None


def initialize_volume(manager):
    # Once per container, like the keyboard: later changes made inside Android remain respected.
    with preferences() as path:
        values = load_preferences(path)
        if values.get("media_volume"):
            return False
        manager.MediaVolumeMax()
        values["media_volume"] = True
        save_preferences(path, values)
    return True


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
        "links_android": package in links_in_android(platform),
    }


def apply(platform, package, touch, keyboard=None, navigation=None, arrows=None, links_android=None):
    validate_package(package)
    with preferences() as path:
        previous = platform.getprop(TOUCH, "")
        if previous is None:
            raise RuntimeError("Android did not return its input preferences.")
        updated = touch_value(previous, package, touch)
        platform.setprop(TOUCH, updated)
        if platform.getprop(TOUCH, "") != updated:
            raise RuntimeError("Android did not confirm the mouse preference.")
        values = load_preferences(path)
        if keyboard is not None:
            set_keyboard(platform, keyboard)
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
        if links_android is not None:
            set_links_in_android(platform, package, links_android)
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
            links = getattr(args, "links_android", None)
            links = None if links is None else links == "on"
            keyboard = None if args.keyboard is None else args.keyboard == "on"
            result = apply(
                platform, args.PACKAGE, args.touch == "on", keyboard,
                navigation, arrows, links,
            )
    finally:
        if frozen:
            manager.Freeze()
    print(json.dumps(result))


def keyboard_run(args):
    """keyboard get | on | off: the container's virtual keyboard, saved for the next app
    launch and applied at once when Android is running. Prints {"keyboard": ...}."""
    import tools.helpers.ipc
    from tools.interfaces import IPlatform

    if args.mode != "get":
        with preferences() as path:
            values = load_preferences(path)
            values["keyboard"] = args.mode == "on"
            save_preferences(path, values)
        try:
            manager = tools.helpers.ipc.DBusContainerService()
            state = manager.GetSession().get("state")
            if state in ("RUNNING", "FROZEN"):
                manager.Unfreeze()
                try:
                    platform = IPlatform.get_service(args)
                    if platform:
                        apply_keyboard(platform)
                finally:
                    if state == "FROZEN":
                        manager.Freeze()
        except Exception as error:
            # Saved all the same: the next app launch applies it.
            logging.warning("The keyboard will change when an app opens: %s", error)
    print(json.dumps({"keyboard": keyboard_wanted()}))

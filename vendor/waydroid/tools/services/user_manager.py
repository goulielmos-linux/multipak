# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import threading
import tools.config
import tools.helpers.net
import tools.helpers.ipc
from pathlib import Path
from contextlib import suppress
from tools.interfaces import IUserMonitor
from tools.interfaces import IPlatform
from gi.repository import GLib

stopping = False


def start(args, session, unlocked_cb=None):

    apps_dir = Path(session["xdg_data_home"]) / "applications"
    apps_dir.mkdir(0o700, exist_ok=True)

    waydroid_user_state_dir = Path(session["waydroid_user_state"])
    waydroid_data_icons_dir = instance().exchange / "icons"

    system_apps = [
        "com.android.calculator2",
        "com.android.camera2",
        "com.android.contacts",
        "com.android.deskclock",
        "com.android.documentsui",
        "com.android.email",
        "com.android.gallery3d",
        "com.android.inputmethod.latin",
        "com.android.settings",
        "com.google.android.gms",
        "org.lineageos.aperture",
        "org.lineageos.eleven",
        "org.lineageos.etar",
        "org.lineageos.jelly",
        "org.lineageos.recorder"
    ]

    def prepend_list(old_list, new_list):
        for s in reversed(new_list):
            if s not in old_list:
                old_list.insert(0, s)

    def glib_key_file_get_string_list(key_file, group, key):
        try:
            return key_file.get_string_list(group, key)
        except GLib.GError:
            return []

    def glib_key_file_prepend_string_list(key_file, group, key, new_list):
        lst = glib_key_file_get_string_list(key_file, group, key)
        prepend_list(lst, new_list)
        key_file.set_string_list(group, key, lst)

    def glib_key_file_has_value(key_file, group, key):
        try:
            key_file.get_value(group, key)
            return True
        except GLib.GError:
            return False

    # Migrate waydroid user configs after upgrade
    def user_migration():
        pass

    # Creates, deletes, or updates desktop file
    def updateDesktopFile(appInfo):
        if appInfo is None:
            return

        packageName = appInfo["packageName"]
        desktop_file_path = apps_dir / instance().desktop_name(packageName)

        showApp = False
        for cat in appInfo["categories"]:
            if cat.strip() == "android.intent.category.LAUNCHER":
                showApp = True
                break

        if not showApp:
            # Remove the desktop file if it exists and do not create a new one.
            desktop_file_path.unlink(missing_ok=True)
            return

        desktop_file = GLib.KeyFile()
        with suppress(GLib.GError):
            flags = GLib.KeyFileFlags.KEEP_COMMENTS | GLib.KeyFileFlags.KEEP_TRANSLATIONS
            desktop_file.load_from_file(str(desktop_file_path), flags)

        desktop_file.set_string("Desktop Entry", "Type", "Application")
        desktop_file.set_string("Desktop Entry", "Name", appInfo["name"] + " — " + instance().label)
        desktop_file.set_string("Desktop Entry", "Exec", instance().command("app", "launch", packageName))
        desktop_file.set_string("Desktop Entry", "Icon", str(waydroid_data_icons_dir / f"{packageName}.png"))
        glib_key_file_prepend_string_list(desktop_file, "Desktop Entry", "Categories", ["X-WayDroid-App"])
        desktop_file.set_string_list("Desktop Entry", "X-Purism-FormFactor", ["Workstation", "Mobile"])
        glib_key_file_prepend_string_list(desktop_file, "Desktop Entry", "Actions", ["app-settings"])
        if packageName in system_apps and not glib_key_file_has_value(desktop_file, "Desktop Entry", "NoDisplay"):
            desktop_file.set_boolean("Desktop Entry", "NoDisplay", True)

        desktop_file.set_string("Desktop Action app-settings", "Name", "App Settings")
        desktop_file.set_string("Desktop Action app-settings", "Exec", instance().command("app", "intent", "android.settings.APPLICATION_DETAILS_SETTINGS", "package:" + packageName))
        desktop_file.set_string("Desktop Action app-settings", "Icon", str(waydroid_data_icons_dir / "com.android.settings.png"))

        desktop_file.save_to_file(str(desktop_file_path))

    def userUnlocked(uid):
        cfg = tools.config.load(args)
        logging.info("Android with user {} is ready".format(uid))

        user_migration()

        if cfg["waydroid"]["auto_adb"] == "True":
            with suppress(RuntimeError):
                tools.helpers.net.adb_connect(args)

        platformService = IPlatform.get_service(args)
        if platformService:
            installed = set(tools.helpers.ipc.DBusContainerService().ListPackages())
            appsList = [a for a in platformService.getAppsInfo() if a["packageName"] in installed]
            for app in appsList:
                updateDesktopFile(app)
            for existing in apps_dir.glob(instance().desktop_glob):
                if existing.name not in map(lambda appInfo: instance().desktop_name(appInfo['packageName']), appsList):
                    existing.unlink()
        if unlocked_cb:
            unlocked_cb()

    def packageStateChanged(mode, packageName, uid):
        platformService = IPlatform.get_service(args)
        if platformService:
            desktop_file_path = apps_dir / instance().desktop_name(packageName)
            installed = set(tools.helpers.ipc.DBusContainerService().ListPackages())
            if mode == IUserMonitor.PACKAGE_REMOVED or packageName not in installed:
                desktop_file_path.unlink(missing_ok=True)
            else:
                appInfo = platformService.getAppInfo(packageName)
                updateDesktopFile(appInfo)

    def service_thread():
        while not stopping:
            IUserMonitor.add_service(args, userUnlocked, packageStateChanged)

    global stopping
    stopping = False
    args.user_manager = threading.Thread(target=service_thread)
    args.user_manager.start()


def stop(args):
    global stopping
    stopping = True
    try:
        if args.userMonitorLoop:
            args.userMonitorLoop.quit()
    except AttributeError:
        logging.debug("UserMonitor service is not even started")

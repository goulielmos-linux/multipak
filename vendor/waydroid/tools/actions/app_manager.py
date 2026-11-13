# Modified by MultiPak on 2026-11-10 for named Android environments.
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import time
import tools.config
import tools.helpers.props
import tools.helpers.ipc
from tools.interfaces import IPlatform
from tools.interfaces import IStatusBarService
import dbus

def install(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        from multipak_instance import current as instance
        tmp_dir = str(instance().exchange / "apks")
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)

        shutil.copyfile(args.PACKAGE, tmp_dir + "/base.apk")
        platformService = IPlatform.get_service(args)
        if platformService:
            platformService.installApp("/data/waydroid_tmp/base.apk")
        else:
            logging.error("Failed to access IPlatform service")
        os.remove(tmp_dir + "/base.apk")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except (dbus.DBusException, KeyError):
        logging.error("WayDroid session is stopped")

def remove(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        platformService = IPlatform.get_service(args)
        if platformService:
            ret = platformService.removeApp(args.PACKAGE)
            if ret != 0:
                logging.error("Failed to uninstall package: {}".format(args.PACKAGE))
        else:
            logging.error("Failed to access IPlatform service")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except dbus.DBusException:
        logging.error("WayDroid session is stopped")

def maybeLaunchLater(args, launchNow):
    try:
        tools.helpers.ipc.DBusSessionService()
        try:
            tools.helpers.ipc.DBusContainerService().Unfreeze()
        except Exception:
            logging.error("Failed to unfreeze container. Trying to launch anyways...")
        launchNow()
    except dbus.DBusException:
        logging.error("Starting waydroid session")
        tools.actions.session_manager.start(args, launchNow, background=False)

def launch(args):
    def justLaunch():
        platformService = IPlatform.get_service(args)
        if platformService:
            import multipak_input
            try:
                multipak_input.initialize_keyboard(platformService)
            except Exception as error:
                logging.warning("Could not initialize input preferences: %s", error)
            platformService.setprop("waydroid.active_apps", args.PACKAGE)
            platformService.launchApp(args.PACKAGE)
            multiwin = platformService.getprop(
                "persist.waydroid.multi_windows", "false")
            multipak_input.launch_policy(platformService, multiwin)
            try:
                if not getattr(args, "no_settings", False):
                    multipak_input.companion(args.PACKAGE)
            except OSError as error:
                logging.warning("Could not open MultiPak input settings: %s", error)
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justLaunch)

def list(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        platformService = IPlatform.get_service(args)
        if platformService:
            appsList = platformService.getAppsInfo()
            installed = set(cm.ListPackages())
            for app in appsList:
                if app["packageName"] not in installed:
                    continue
                print("Name: " + app["name"])
                print("packageName: " + app["packageName"])
                print("categories:")
                for cat in app["categories"]:
                    print("\t" + cat)
        else:
            logging.error("Failed to access IPlatform service")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except dbus.DBusException:
        logging.error("WayDroid session is stopped")

def showFullUI(args):
    def justShow():
        platformService = IPlatform.get_service(args)
        if platformService:
            platformService.setprop("waydroid.active_apps", "Waydroid")
            platformService.settingsPutString(2, "policy_control", "null*")
            # HACK: Refresh display contents
            statusBarService = IStatusBarService.get_service(args)
            if statusBarService:
                statusBarService.expand()
                time.sleep(0.5)
                statusBarService.collapse()
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justShow)

def intent(args):
    def justLaunch():
        platformService = IPlatform.get_service(args)
        if platformService:
            ret = platformService.launchIntent(args.ACTION, args.URI)
            if ret == "":
                return
            pkg = ret if ret != "android" else "Waydroid"
            platformService.setprop("waydroid.active_apps", pkg)
            multiwin = platformService.getprop(
                "persist.waydroid.multi_windows", "false")
            import multipak_input
            multipak_input.launch_policy(platformService, multiwin)
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justLaunch)

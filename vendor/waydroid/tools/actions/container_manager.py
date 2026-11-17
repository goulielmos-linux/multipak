# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import glob
import signal
import subprocess
import re
import tools.config
from contextlib import suppress
from shutil import which
from tools import helpers
from tools import services
from tools import actions
import dbus
import dbus.service
import dbus.exceptions
from gi.repository import GLib

class DbusContainerManager(dbus.service.Object):
    def __init__(self, looper, bus, object_path, args):
        self.args = args
        self.looper = looper
        dbus.service.Object.__init__(self, bus, object_path)

    def _authorize(self, sender, conn):
        bus = dbus.Interface(conn.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus/Bus", False), "org.freedesktop.DBus")
        if int(bus.GetConnectionUnixUser(sender)) not in (0, instance().owner):
            raise PermissionError("Android instance belongs to another user")

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='a{ss}', out_signature='', sender_keyword="sender", connection_keyword="conn")
    def Start(self, session, sender, conn):
        dbus_info = dbus.Interface(conn.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus/Bus", False), "org.freedesktop.DBus")
        uid = dbus_info.GetConnectionUnixUser(sender)
        pid = dbus_info.GetConnectionUnixProcessID(sender)
        if "session" in self.args:
            raise RuntimeError("Already tracking a session")
        import multipak_session
        if int(uid) == 0:
            uid, pid = instance().owner, int(session.get("pid", "0"))
        trusted = multipak_session.prepare(session, int(uid), int(pid))
        try:
            do_start(self.args, trusted)
        except BaseException:
            multipak_session.release_sockets()
            raise

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='as',
                         sender_keyword="sender", connection_keyword="conn")
    def ListPackages(self, sender, conn):
        # Read-only, fixed command scoped to this environment. No arbitrary
        # shell arguments or root commands are exposed through this method.
        bus = dbus.Interface(conn.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus/Bus", False), "org.freedesktop.DBus")
        if int(bus.GetConnectionUnixUser(sender)) not in (0, instance().owner):
            raise PermissionError("Android instance belongs to another user")
        state = helpers.lxc.status(self.args)
        if state == "FROZEN":
            helpers.lxc.unfreeze(self.args)
        elif state != "RUNNING":
            raise RuntimeError("Android is not running")
        command = ["/usr/bin/lxc-attach", "-P", tools.config.defaults["lxc"],
                   "-n", instance().id, "--clear-env", "--", "/system/bin/cmd",
                   "package", "list", "packages", "--user", "0"]
        output = subprocess.run(command, check=True, capture_output=True,
                                text=True, timeout=8).stdout
        packages = []
        for line in output.splitlines():
            if line.startswith("package:"):
                package = line.removeprefix("package:").strip()
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
                    packages.append(package)
        if not packages:
            raise RuntimeError("Android package manager is not ready")
        return packages

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='b', out_signature='', sender_keyword="sender", connection_keyword="conn")
    def Stop(self, quit_session, sender, conn):
        self._authorize(sender, conn)
        stop(self.args, quit_session)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='', sender_keyword="sender", connection_keyword="conn")
    def Freeze(self, sender, conn):
        self._authorize(sender, conn)
        if not actions.initializer.is_initialized(self.args):
            raise RuntimeError("Waydroid is not initialized")
        freeze(self.args)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='', sender_keyword="sender", connection_keyword="conn")
    def Unfreeze(self, sender, conn):
        self._authorize(sender, conn)
        if not actions.initializer.is_initialized(self.args):
            raise RuntimeError("Waydroid is not initialized")
        unfreeze(self.args)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='a{ss}', sender_keyword="sender", connection_keyword="conn")
    def GetSession(self, sender, conn):
        self._authorize(sender, conn)
        if not actions.initializer.is_initialized(self.args):
            raise RuntimeError("Waydroid is not initialized")
        try:
            session = self.args.session
            session["state"] = helpers.lxc.status(self.args)
            return session
        except AttributeError:
            return {}

def set_permissions(args, perm_list=None, mode="777"):
    def chmod(path, mode):
        if os.path.exists(path):
            command = ["chmod", mode, "-R", path]
            tools.helpers.run.user(args, command, check=False)

    # Nodes list
    if not perm_list:
        perm_list = [
            "/dev/ashmem",

            # sw_sync for HWC
            "/dev/sw_sync",
            "/sys/kernel/debug/sync/sw_sync",

            # Media
            "/dev/Vcodec",
            "/dev/MTK_SMI",
            "/dev/mdp_sync",
            "/dev/mtk_cmdq",

            # Graphics
            "/dev/graphics",
            "/dev/pvr_sync",
            "/dev/ion",
        ]

        # DRM render nodes
        perm_list.extend(glob.glob("/dev/dri/renderD*"))
        # Framebuffers
        perm_list.extend(glob.glob("/dev/fb*"))
        # Videos
        perm_list.extend(glob.glob("/dev/video*"))
        # DMA-BUF Heaps
        perm_list.extend(glob.glob("/dev/dma_heap/*"))

    for path in perm_list:
        chmod(path, mode)

def start(args):
    mainloop = GLib.MainLoop()

    def sigint_handler(data):
        with suppress(Exception):
            stop(args)
        mainloop.quit()

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, sigint_handler, None)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, sigint_handler, None)

    # Initialization is performed only by the installed privileged helper.
    _container_manager = DbusContainerManager(mainloop, dbus.SystemBus(), '/ContainerManager', args)

    try:
        _name = dbus.service.BusName(instance().container_bus, dbus.SystemBus(), do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        logging.error("Container service is already running")
        return

    mainloop.run()


prepared_drivers = False
def prepare_drivers_once(args):
    global prepared_drivers
    if prepared_drivers:
        return

    # Load binder and ashmem drivers
    cfg = tools.config.load(args)
    if cfg["waydroid"]["vendor_type"] == "MAINLINE":
        if helpers.drivers.probeBinderDriver(args) != 0:
            logging.error("Failed to load Binder driver")
        helpers.drivers.probeAshmemDriver(args)
    helpers.drivers.loadBinderNodes(args)
    set_permissions(args, [
        "/dev/" + args.BINDER_DRIVER,
        "/dev/" + args.VNDBINDER_DRIVER,
        "/dev/" + args.HWBINDER_DRIVER
    ], "666")
    prepared_drivers = True

def do_start(args, session):
    if int(session["user_id"]) != instance().owner:
        raise PermissionError("Android instance belongs to another user")
    if not actions.initializer.is_initialized(args):
        raise RuntimeError("Waydroid is not initialized")

    if "session" in args:
        raise RuntimeError("Already tracking a session")

    prepare_drivers_once(args)

    logging.info("Starting up container for a new session")

    # Networking
    command = [tools.config.tools_src +
               "/data/scripts/waydroid-net.sh", "start", instance().id, str(instance().network_slot)]
    tools.helpers.run.user(args, command)

    # Set permissions
    set_permissions(args)

    # Create session-specific LXC config file
    helpers.lxc.generate_session_lxc_config(args, session)
    # Backwards compatibility
    with open(tools.config.defaults["lxc"] + "/" + instance().id + "/config") as f:
        if "config_session" not in f.read():
            helpers.mount.bind(args, session["waydroid_data"],
                               tools.config.defaults["data"])

    # Mount rootfs
    cfg = tools.config.load(args)
    helpers.images.mount_rootfs(args, cfg["waydroid"]["images_path"], session)

    helpers.protocol.set_aidl_version(args)

    helpers.lxc.start(args)
    services.hardware_manager.start(args)

    args.session = session

def stop(args, quit_session=True):
    if not actions.initializer.is_initialized(args):
        raise RuntimeError("Waydroid is not initialized")

    logging.info("Stopping container")

    try:
        services.hardware_manager.stop(args)
        status = helpers.lxc.status(args)
        if status != "STOPPED":
            helpers.lxc.stop(args)
            while helpers.lxc.status(args) != "STOPPED":
                pass

        # Networking
        command = [tools.config.tools_src +
                   "/data/scripts/waydroid-net.sh", "stop", instance().id, str(instance().network_slot)]
        tools.helpers.run.user(args, command, check=False)

        # Umount rootfs
        helpers.images.umount_rootfs(args)
        import multipak_session
        multipak_session.release_sockets()

        # Backwards compatibility
        with suppress(Exception):
            helpers.mount.umount_all(args, tools.config.defaults["data"])

        if "session" in args:
            if quit_session:
                logging.info("Terminating session because the container was stopped")
                with suppress(OSError):
                    multipak_session.notify_stopped(args.session)
            del args.session
    except Exception as e:
        logging.debug("Error while stopping container: %s", e)

def restart(args):
    status = helpers.lxc.status(args)
    if status == "RUNNING":
        helpers.lxc.stop(args)
        helpers.lxc.start(args)
    else:
        logging.error("WayDroid container is {}".format(status))

def freeze(args):
    status = helpers.lxc.status(args)
    if status == "RUNNING":
        helpers.lxc.freeze(args)
        while helpers.lxc.status(args) == "RUNNING":
            pass
    else:
        logging.error("WayDroid container is {}".format(status))

def unfreeze(args):
    status = helpers.lxc.status(args)
    if status == "FROZEN":
        helpers.lxc.unfreeze(args)
        while helpers.lxc.status(args) == "FROZEN":
            pass

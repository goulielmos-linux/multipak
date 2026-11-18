# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
import multipak_input
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import glob
import signal
import subprocess
import re
import threading
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
        try:
            import multipak_outbox
            self.args.outbox = multipak_outbox.watch(self.LinksWaiting)
        except Exception as error:
            logging.warning("Android links will stay unopened: %s", error)
        try:
            import multipak_files
            self.args.sent = multipak_files.watch(self.LinksWaiting)
        except Exception as error:
            logging.warning("Files shared with Linux will stay in Android: %s", error)

    @dbus.service.signal("id.waydro.ContainerManager", signature="")
    def LinksWaiting(self):
        # Carries no data: the owner's session fetches the links with TakeRequests,
        # then the media state with MediaState.
        pass

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='as',
                         sender_keyword="sender", connection_keyword="conn")
    def TakeRequests(self, sender, conn):
        # Checked links from the helper's outbox; the files are deleted once read.
        self._authorize(sender, conn)
        import multipak_outbox
        return multipak_outbox.take()

    def _cmd(self, *arguments, program="/system/bin/cmd"):
        # Runs a fixed Android "cmd" (or another fixed program) in this environment.
        # Callers pass only constants; nothing from the D-Bus caller reaches the command line.
        state = helpers.lxc.status(self.args)
        if state == "FROZEN":
            unfreeze(self.args)
        elif state != "RUNNING":
            raise RuntimeError("Android is not running")
        # Programs other than cmd start Android's Java runtime, which needs its environment.
        options = [] if program == "/system/bin/cmd" else helpers.lxc.android_env_attach_options(self.args)
        command = ["/usr/bin/lxc-attach", "-P", tools.config.defaults["lxc"],
                   "-n", instance().id, "--clear-env", *options, "--", program, *arguments]
        result = subprocess.run(command, check=True, capture_output=True,
                                text=True, timeout=8)
        return result.stdout + result.stderr

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='hst', out_signature='s',
                         sender_keyword="sender", connection_keyword="conn",
                         async_callbacks=("reply", "error"))
    def SendFile(self, pipe, name, size, sender, conn, reply, error):
        # The owner writes a file into the pipe; multipak_files checks the pipe and the name,
        # and Android writes it to its Download folder. Answered when the file is complete.
        self._authorize(sender, conn)
        descriptor = pipe.take()

        def work():
            try:
                result = send_file(self.args, descriptor, str(name), int(size))
                GLib.idle_add(lambda: reply(result) and False)
            except Exception as problem:
                logging.warning("Could not send a file to Android: %s", problem)
                GLib.idle_add(lambda failure=problem: error(failure) and False)
            finally:
                os.close(descriptor)

        threading.Thread(target=work, daemon=True).start()

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='a(sh)',
                         sender_keyword="sender", connection_keyword="conn")
    def TakeFiles(self, sender, conn):
        # Files the helper app received for Linux, checked and deleted, as open files.
        self._authorize(sender, conn)
        import multipak_files
        result = []
        for name, descriptor in multipak_files.take():
            try:
                result.append(dbus.Struct((name, dbus.types.UnixFd(descriptor)), signature="sh"))
            finally:
                os.close(descriptor)  # UnixFd keeps its own copy.
        return dbus.Array(result, signature="(sh)")

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='as',
                         sender_keyword="sender", connection_keyword="conn")
    def ListPackages(self, sender, conn):
        # Read-only, fixed command scoped to this environment.
        self._authorize(sender, conn)
        output = self._cmd("package", "list", "packages", "--user", "0")
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
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def MediaVolumeMax(self, sender, conn):
        # Fixed commands: media starts at full volume, so the Linux mixer alone sets loudness.
        self._authorize(sender, conn)
        volume = ("media_session", "volume", "--stream", "3")
        # Android 13 names the range on --get; Android 11 prints nothing there,
        # but names it when it refuses an index that is too high (nothing changes).
        for probe in (("--get",), ("--set", "999")):
            highest = multipak_input.media_volume_max(self._cmd(*volume, *probe))
            if highest is not None:
                self._cmd(*volume, "--set", str(highest))
                return
        raise RuntimeError("Android did not report its media volume")

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def SetDefaultBrowser(self, sender, conn):
        # Makes the MultiPak helper the default Android browser. Fixed command, no input.
        self._authorize(sender, conn)
        import multipak_links
        self._cmd(*multipak_links.ROLE_COMMAND)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def AllowMediaListener(self, sender, conn):
        # Lets the MultiPak helper see the media sessions. Fixed command, no input.
        self._authorize(sender, conn)
        import multipak_media
        self._cmd(*multipak_media.LISTENER_COMMAND)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def RemoveOldHelper(self, sender, conn):
        # Removes MultiPak's Android app under its old ID after the move. Fixed command, no input.
        self._authorize(sender, conn)
        self._cmd("package", "uninstall", multipak_input.OLD_MAPPER)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='a{ss}',
                         sender_keyword="sender", connection_keyword="conn")
    def MediaState(self, sender, conn):
        # The newest media state found by TakeRequests, already checked by multipak_outbox.
        self._authorize(sender, conn)
        import multipak_outbox
        return multipak_outbox.media()

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='s', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def MediaCommand(self, key, sender, conn):
        # The caller names a key; only the fixed key event found for it reaches Android,
        # which hands it to the app that is playing, like a keyboard's media key.
        self._authorize(sender, conn)
        import multipak_media
        command = multipak_media.KEYS.get(str(key))
        if command is None:
            raise ValueError("Unknown media key")
        self._cmd(*command, program=multipak_media.INPUT)

    @dbus.service.signal("id.waydro.ContainerManager", signature="")
    def AppsPlaying(self):
        # Carries no data: the owner's session fetches the apps with PlayingApps.
        pass

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def WatchVolumes(self, sender, conn):
        # Starts the helper that sets each app's volume: a class of the MultiPak app, run as
        # its own Android process. No input: the only part that is not fixed is where Android
        # says the app is installed, and multipak_volume checks it.
        self._authorize(sender, conn)
        import multipak_volume
        path = multipak_volume.apk_path(self._cmd(*multipak_volume.PATH_COMMAND))
        command = ["/usr/bin/lxc-attach", "-P", tools.config.defaults["lxc"], "-n", instance().id,
                   "--clear-env", *helpers.lxc.android_env_attach_options(self.args),
                   "--set-var", "CLASSPATH=" + path, "--", *multipak_volume.COMMAND]
        if "volumes" in self.args:
            if self.args.volumes.runs(command):
                return
            self.args.volumes.stop()  # It ended, or the app was updated and has a new file.
        self.args.volumes = multipak_volume.Helper(command, self.AppsPlaying)

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='', out_signature='as',
                         sender_keyword="sender", connection_keyword="conn")
    def PlayingApps(self, sender, conn):
        # The apps the helper last named, already checked by multipak_volume.
        self._authorize(sender, conn)
        return self.args.volumes.apps if "volumes" in self.args else []

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='si', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def AppVolume(self, package, percent, sender, conn):
        # The caller names an app and a volume from 0 to 100; multipak_volume checks both
        # before the helper is given its one fixed line.
        self._authorize(sender, conn)
        if "volumes" in self.args:
            self.args.volumes.set(str(package), int(percent))

    @helpers.logging.log_exceptions
    @dbus.service.method("id.waydro.ContainerManager", in_signature='s', out_signature='',
                         sender_keyword="sender", connection_keyword="conn")
    def SetMicrophone(self, name, sender, conn):
        # The Linux microphone Android records from; "" is the Linux default. Only a checked
        # name reaches the file, which multipak_microphone writes itself.
        self._authorize(sender, conn)
        import multipak_microphone
        multipak_microphone.write(str(name))
        if "session" in self.args:
            self.args.session["microphone"] = str(name)

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

_sending = threading.Lock()

def send_file(args, descriptor, name, size):
    # One file at a time: a send that found Android frozen freezes it again afterwards.
    import multipak_files
    with _sending:
        state = helpers.lxc.status(args)
        if state not in ("RUNNING", "FROZEN"):
            raise RuntimeError("Android is not running")
        if state == "FROZEN":
            unfreeze(args)
        try:
            attach = ["/usr/bin/lxc-attach", "-P", tools.config.defaults["lxc"],
                      "-n", instance().id, "--clear-env"]
            chosen = multipak_files.send(descriptor, name, size, attach, subprocess.run)
            try:
                # The file is there; without this, apps and galleries would not list it.
                subprocess.run([*attach, *helpers.lxc.android_env_attach_options(args), "--",
                                *multipak_files.scan_command(chosen)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            except Exception as error:
                logging.warning("Android may not list %s yet: %s", chosen, error)
            return chosen
        finally:
            if state == "FROZEN":
                freeze(args)

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
        # MultiPak: only the chosen webcam is opened up, by multipak_camera.prepare.
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

    # Only the chosen webcam enters Android; config_nodes keeps none.
    import multipak_camera
    lxc = tools.config.defaults["lxc"] + "/" + instance().id
    try:
        camera = multipak_camera.prepare(session.get("camera", ""), lxc + "/config_nodes")
    except Exception as error:
        camera = None
        logging.warning("Android will start without a camera: %s", error)

    # Create session-specific LXC config file
    helpers.lxc.generate_session_lxc_config(args, session, camera)
    # Backwards compatibility
    with open(lxc + "/config") as f:
        if "config_session" not in f.read():
            helpers.mount.bind(args, session["waydroid_data"],
                               tools.config.defaults["data"])

    # The microphone file must be in the overlay layer before it is mounted.
    import multipak_microphone
    try:
        multipak_microphone.write(session.get("microphone", ""))
    except Exception as error:
        logging.warning("Android will record from the default microphone: %s", error)

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
        if "outbox" in args:
            args.outbox.cancel()
            del args.outbox
        if "sent" in args:
            args.sent.cancel()
            del args.sent
        if "volumes" in args:
            args.volumes.stop()
            del args.volumes
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

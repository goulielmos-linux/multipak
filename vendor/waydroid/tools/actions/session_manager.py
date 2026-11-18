# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import signal
import sys
import tools.config
import tools.helpers.ipc
from tools import services
import dbus
import dbus.service
import dbus.exceptions
from gi.repository import GLib
import copy

class DbusSessionManager(dbus.service.Object):
    def __init__(self, looper, bus, object_path, args):
        self.args = args
        self.looper = looper
        dbus.service.Object.__init__(self, bus, object_path)

    @dbus.service.method("id.waydro.SessionManager", in_signature='', out_signature='')
    def Stop(self):
        do_stop(self.args, self.looper)
        stop_container(quit_session=False)

def handle_disconnect(args, looper):
    do_stop(args, looper)
    stop_container(quit_session=False)

def service(args, looper):
    bus = dbus.SessionBus()
    bus.set_exit_on_disconnect(False)
    bus.add_signal_receiver(lambda: handle_disconnect(args, looper),
                            signal_name='Disconnected',
                            dbus_interface='org.freedesktop.DBus.Local')
    _session_manager = DbusSessionManager(looper, dbus.SessionBus(), '/SessionManager', args)
    looper.run()

def start(args, unlocked_cb=None, background=True):
    try:
        _name = dbus.service.BusName(instance().session_bus, dbus.SessionBus(), do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        logging.error("Session is already running")
        if unlocked_cb:
            unlocked_cb()
        return

    session = copy.copy(tools.config.session_defaults)

    # TODO: also support WAYLAND_SOCKET?
    wayland_display = session["wayland_display"]
    if wayland_display == "None" or not wayland_display:
        logging.warning('WAYLAND_DISPLAY is not set, defaulting to "wayland-0"')
        wayland_display = session["wayland_display"] = "wayland-0"

    if os.path.isabs(wayland_display):
        wayland_socket_path = wayland_display
    else:
        xdg_runtime_dir = session["xdg_runtime_dir"]
        if xdg_runtime_dir == "None" or not xdg_runtime_dir:
            logging.error("XDG_RUNTIME_DIR is not set; please don't start a Waydroid session with 'sudo'!")
            sys.exit(1)
        wayland_socket_path = os.path.join(xdg_runtime_dir, wayland_display)
    if not os.path.exists(wayland_socket_path):
        logging.error(f"Wayland socket '{wayland_socket_path}' doesn't exist; are you running a Wayland compositor?")
        sys.exit(1)

    if instance().data_layout != 2 or not (instance().exchange / "apks").is_dir():
        raise RuntimeError("The Android environment needs migration or preparation")

    dpi = tools.helpers.props.host_get(args, "ro.sf.lcd_density")
    if dpi == "":
        dpi = os.getenv("GRID_UNIT_PX")
        if dpi is not None:
            dpi = str(int(dpi) * 20)
        else:
            dpi = "0"
    session["lcd_density"] = dpi

    session["background_start"] = "true" if background else "false"

    import multipak_microphone
    try:
        session["microphone"] = multipak_microphone.chosen()
    except Exception as error:
        logging.warning("Android will record from the default microphone: %s", error)
    import multipak_camera
    try:
        session["camera"] = multipak_camera.chosen()
    except Exception as error:
        logging.warning("Android will use the first webcam it can: %s", error)

    mainloop = GLib.MainLoop()

    def sigint_handler(data):
        do_stop(args, mainloop)
        stop_container(quit_session=False)

    def sigusr_handler(data):
        do_stop(args, mainloop)

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGHUP, sigint_handler, None)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, sigint_handler, None)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, sigint_handler, None)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, sigusr_handler, None)
    try:
        tools.helpers.ipc.DBusContainerService().Start(session)
    except dbus.DBusException as e:
        logging.debug(e)
        if e.get_dbus_name().startswith("org.freedesktop.DBus.Python"):
            logging.error(e.get_dbus_message().splitlines()[-1])
        else:
            logging.error("WayDroid container is not listening")
        sys.exit(0)

    services.user_manager.start(args, session, unlocked_cb)
    services.clipboard_manager.start(args)
    services.notification_manager.start(args, session)
    import multipak_links
    import multipak_media
    try:
        media = multipak_media.listen(session)
    except Exception as error:
        media = None
        logging.warning("Android media will stay out of the desktop controls: %s", error)
    try:
        multipak_links.listen(media)
    except Exception as error:
        logging.warning("Android links will stay unopened: %s", error)
    import multipak_volume
    try:
        args.app_volumes = multipak_volume.listen(session)
    except Exception as error:
        logging.warning("Android apps will share one volume in the desktop's mixer: %s", error)
    service(args, mainloop)

def do_stop(args, looper):
    if "app_volumes" in args:
        args.app_volumes()  # Removes the apps from the desktop's mixer.
        del args.app_volumes
    services.user_manager.stop(args)
    services.clipboard_manager.stop(args)
    services.notification_manager.stop(args)
    looper.quit()

def stop(args):
    try:
        tools.helpers.ipc.DBusSessionService().Stop()
    except dbus.DBusException:
        stop_container(quit_session=True)

def stop_container(quit_session):
    try:
        tools.helpers.ipc.DBusContainerService().Stop(quit_session)
    except dbus.DBusException:
        pass

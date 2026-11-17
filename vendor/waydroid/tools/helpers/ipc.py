# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
# Copyright 2022 Alessandro Astone
# SPDX-License-Identifier: GPL-3.0-or-later

# Currently implemented as FIFO
import os
import dbus

def DBusContainerService(object_path="/ContainerManager", intf="id.waydro.ContainerManager"):
    return dbus.Interface(dbus.SystemBus().get_object(instance().container_bus, object_path), intf)

def DBusSessionService(object_path="/SessionManager", intf="id.waydro.SessionManager"):
    return dbus.Interface(dbus.SessionBus().get_object(instance().session_bus, object_path), intf)

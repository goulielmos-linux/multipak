# Modified by MultiPak on 2026-11-10 for named Android environments.
from multipak_instance import current as instance
# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import glob
import fcntl
import struct
import tools.config
import tools.helpers.run
from contextlib import suppress


BINDER_DRIVERS = [
    "anbox-binder",
    "puddlejumper",
    "bonder",
    "binder"
]
VNDBINDER_DRIVERS = [
    "anbox-vndbinder",
    "vndpuddlejumper",
    "vndbonder",
    "vndbinder"
]
HWBINDER_DRIVERS = [
    "anbox-hwbinder",
    "hwpuddlejumper",
    "hwbonder",
    "hwbinder"
]


def isBinderfsLoaded(args):
    with open("/proc/filesystems", "r") as handle:
        for line in handle:
            words = line.split()
            if len(words) >= 2 and words[1] == "binder":
                return True

    return False

def allocBinderNodes(args, binder_dev_nodes):
    NRBITS = 8
    TYPEBITS = 8
    SIZEBITS = 14
    NRSHIFT = 0
    TYPESHIFT = NRSHIFT + NRBITS
    SIZESHIFT = TYPESHIFT + TYPEBITS
    DIRSHIFT = SIZESHIFT + SIZEBITS
    WRITE = 0x1
    READ = 0x2

    def IOC(direction, _type, nr, size):
        return (direction << DIRSHIFT) | (_type << TYPESHIFT) | (nr << NRSHIFT) | (size << SIZESHIFT)

    def IOWR(_type, nr, size):
        return IOC(READ|WRITE, _type, nr, size)

    BINDER_CTL_ADD = IOWR(98, 1, 264)
    with open(instance().binderfs + '/binder-control', 'rb') as f:
        for node in binder_dev_nodes:
            node_struct = struct.pack('256sII', bytes(node, 'utf-8'), 0, 0)
            with suppress(FileExistsError):
                fcntl.ioctl(f.fileno(), BINDER_CTL_ADD, node_struct)

def probeBinderDriver(args):
    directory = instance().binderfs
    os.makedirs(directory, mode=0o755, exist_ok=True)
    if not os.path.ismount(directory):
        tools.helpers.run.user(args, ["mount", "-t", "binder", "binder", directory])
    allocBinderNodes(args, ["binder", "vndbinder", "hwbinder"])
    args.BINDER_DRIVER = directory.removeprefix("/dev/") + "/binder"
    args.VNDBINDER_DRIVER = directory.removeprefix("/dev/") + "/vndbinder"
    args.HWBINDER_DRIVER = directory.removeprefix("/dev/") + "/hwbinder"
    return 0

def probeAshmemDriver(args):
    if not os.path.exists("/dev/ashmem"):
        command = ["modprobe", "-q", "ashmem_linux"]
        tools.helpers.run.user(args, command, check=False)

    if not os.path.exists("/dev/ashmem"):
        return -1

    return 0

def setupBinderNodes(args):
    if args.vendor_type != "MAINLINE":
        raise RuntimeError("MultiPak Android instances require a MAINLINE vendor image")
    probeBinderDriver(args)


def loadBinderNodes(args):
    cfg = tools.config.load(args)
    args.BINDER_DRIVER = cfg["waydroid"]["binder"]
    args.VNDBINDER_DRIVER = cfg["waydroid"]["vndbinder"]
    args.HWBINDER_DRIVER = cfg["waydroid"]["hwbinder"]
    # These might not be in cfg on package upgrade
    args.BINDER_PROTOCOL = cfg["waydroid"].get("binder_protocol")
    args.SERVICE_MANAGER_PROTOCOL = cfg["waydroid"].get("service_manager_protocol")

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Linux webcam an Android environment sees.

Only the chosen webcam enters the container, always as /dev/video0. The choice is "auto" (the
first usable one), "none" or a webcam's stable name; root finds the node itself and only compares
the name. A missing webcam is no error. Android 13 reads MJPEG, Android 11 YUYV."""

import fcntl
import glob
import json
import logging
import os
import re
import stat
import struct

from multipak_instance import current as instance

PREFERENCE = "camera"
AUTO, NONE = "auto", "none"
# Any printable text: the name is compared with the webcams found, never used as a path.
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,200}")
FORMAT = {"20.0": "MJPG", "18.1": "YUYV"}
NODE = re.compile(r"/dev/video\d+")
MAJOR = 81  # Video4Linux
OPEN = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
QUERYCAP = 0x80685600  # VIDIOC_QUERYCAP, struct v4l2_capability (104 bytes)
ENUM_FMT = 0xC0405602  # VIDIOC_ENUM_FMT, struct v4l2_fmtdesc (64 bytes)
CAPTURE = 0x1  # V4L2_CAP_VIDEO_CAPTURE
DEVICE_CAPS = 0x80000000  # V4L2_CAP_DEVICE_CAPS: device_caps describes this node
# An entry of config_nodes from earlier test builds, which bound every /dev/videoN.
OLD_ENTRY = re.compile(r"lxc\.mount\.entry = /dev/video\d+ dev/video\d+ .*")


def check(choice):
    choice = str(choice) or AUTO
    if not NAME.fullmatch(choice):
        raise ValueError("Invalid camera name")
    return choice


def _video(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) != MAJOR:
        raise PermissionError("Not a video device")


def probe(node):
    """(name, formats) of a webcam node, or None when it does not capture video, such as
    the metadata node of a USB webcam or a virtual camera with nothing to show."""
    try:
        descriptor = os.open(node, OPEN)
    except OSError:
        return None
    try:
        _video(descriptor)
        buffer = bytearray(104)
        fcntl.ioctl(descriptor, QUERYCAP, buffer)
        _driver, card, _bus, _version, caps, device_caps = struct.unpack_from("16s32s32sIII", buffer)
        if caps & DEVICE_CAPS:
            caps = device_caps
        if not caps & CAPTURE:
            return None
        formats = []
        for index in range(64):
            description = bytearray(struct.pack("II", index, 1).ljust(64, b"\0"))
            try:
                fcntl.ioctl(descriptor, ENUM_FMT, description)
            except OSError:
                break
            # The highest bit only marks a big-endian variant of the format.
            fourcc = struct.unpack_from("I", description, 44)[0] & 0x7FFFFFFF
            formats.append(struct.pack("I", fourcc).decode("ascii", "replace").strip("\0 "))
        return card.split(b"\0")[0].decode("utf-8", "replace") or node, formats
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _links():
    """{"/dev/videoN": its udev link}; a by-id link wins over a by-path one."""
    found = {}
    for kind in ("by-id", "by-path"):
        for link in sorted(glob.glob(f"/dev/v4l/{kind}/*")):
            found.setdefault(os.path.realpath(link), link)
    return found


def cameras(release=None):
    """The webcams, in node order: id, name, formats, compatible with this Android, node."""
    release = release or instance().release
    links = _links()
    found = []
    nodes = [node for node in glob.glob("/dev/video*") if NODE.fullmatch(node)]
    for node in sorted(nodes, key=lambda node: int(node[10:])):
        probed = probe(node)
        if probed is None:
            continue
        name, formats = probed
        identifier = links.get(node, "name:" + name)
        if NAME.fullmatch(identifier):
            found.append({"id": identifier, "name": name, "formats": formats,
                          "compatible": FORMAT[release] in formats, "node": node})
    return found


def resolve(choice, found):
    """The node of the chosen webcam among those found, or None."""
    choice = check(choice)
    for camera in found:
        if (camera["compatible"] if choice == AUTO else camera["id"] == choice):
            return camera["node"]
    return None


# In the root service.


def prepare(choice, config_nodes):
    """The node that becomes /dev/video0 in Android, or None. Android's camera service
    runs as its own user, so that node, and only that one, is opened to every user."""
    _drop_old_entries(config_nodes)
    choice = check(choice)
    node = None if choice == NONE else resolve(choice, cameras())
    if node is None:
        return None
    descriptor = os.open(node, OPEN)
    try:
        _video(descriptor)
        os.fchmod(descriptor, 0o666)
    finally:
        os.close(descriptor)
    return node


def _drop_old_entries(path):
    """Environments made by earlier test builds bind every /dev/videoN; those lines would show
    Android more webcams and fight over /dev/video0."""
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return
    kept = [line for line in lines if not OLD_ENTRY.fullmatch(line)]
    if kept != lines:
        temporary = f"{path}.new"
        with open(temporary, "w") as output:
            output.write("".join(line + "\n" for line in kept))
        os.replace(temporary, path)


# In the user's session and command line.


def chosen():
    """The owner's saved choice; "auto" when there is none."""
    import multipak_input

    with multipak_input.preferences() as path:
        choice = multipak_input.load_preferences(path).get(PREFERENCE, AUTO)
    return choice if isinstance(choice, str) and NAME.fullmatch(choice) else AUTO


def run(args):
    """camera list | set [ID]

    ID is "auto", "none" or a webcam's id from the list; without it, "set" goes back to
    "auto". The choice applies when Android starts. Prints it, the webcams and Android's
    release as JSON for the Manager."""
    import multipak_input

    if args.mode == "set":
        choice = check(args.ID or AUTO)
        with multipak_input.preferences() as path:
            values = multipak_input.load_preferences(path)
            values[PREFERENCE] = choice
            multipak_input.save_preferences(path, values)
    try:
        found = cameras()
    except OSError as error:
        logging.warning("Could not list the webcams: %s", error)
        found = []
    print(json.dumps({"camera": chosen(), "release": instance().release,
                      "cameras": [{key: camera[key] for key in ("id", "name", "formats", "compatible")}
                                  for camera in found]},
                     ensure_ascii=False))

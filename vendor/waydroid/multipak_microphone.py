# SPDX-License-Identifier: GPL-3.0-or-later
"""The Linux microphone an Android environment records from.

Android reads "default-source" from /vendor/etc/pulse/client.conf at each recording. Root keeps
that file in the environment's overlay and rewrites it in place, so a change needs no restart.
It is always built here from a checked name, never taken from the owner: it could start programs
in a privileged container."""

import json
import logging
import os
import re
import stat
import subprocess

from multipak_instance import current as instance

# A PulseAudio or PipeWire source name, such as alsa_input.usb-046d_C920-02.analog-stereo.
NAME = re.compile(r"[A-Za-z0-9_.:@+-]{1,200}")
PREFERENCE = "microphone"
CONF = ("overlay", "vendor", "etc", "pulse", "client.conf")
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
PACTL = ("pactl", "list", "sources")
# English field names, and the descriptions as the sound server wrote them.
PACTL_ENV = {"LC_ALL": "C.UTF-8"}


def check(name):
    """The name as given, or "" for the Linux default."""
    name = str(name)
    if name and not NAME.fullmatch(name):
        raise ValueError("Invalid microphone name")
    return name


def content(name):
    text = "# Written by MultiPak: the microphone chosen for this Android environment.\n"
    return (text + (f"default-source = {name}\n" if name else "")).encode()


# In the root service.


def write(name):
    """Point this environment at a microphone. The file is changed in place: Android,
    once started, only sees the file it was started with."""
    data = content(check(name))
    directory = os.open(instance().work, DIRECTORY)
    try:
        for part in CONF[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=directory)
                made = True
            except FileExistsError:
                made = False
            following = os.open(part, DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = following
            if made:
                os.fchmod(directory, 0o755)
        descriptor = os.open(CONF[-1], FILE, 0o644, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("Unexpected Android sound configuration file")
            os.fchmod(descriptor, 0o644)
            os.pwrite(descriptor, data, 0)
            os.ftruncate(descriptor, len(data))
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


# In the user's session and command line.


def chosen():
    """The owner's saved microphone; "" is the Linux default."""
    import multipak_input

    with multipak_input.preferences() as path:
        name = multipak_input.load_preferences(path).get(PREFERENCE, "")
    return name if isinstance(name, str) and NAME.fullmatch(name) else ""


def parse_sources(output):
    """The microphones in the output of "pactl list sources". A monitor records what a
    sound output plays, so it is left out; so is a name the root service would refuse."""
    found = []
    for block in re.split(r"^Source #\d+$", output, flags=re.M)[1:]:
        fields = dict(re.findall(r"^\t(Name|Description|Monitor of Sink): (.*)$", block, re.M))
        name = fields.get("Name", "")
        if NAME.fullmatch(name) and fields.get("Monitor of Sink") == "n/a":
            found.append({"name": name, "description": fields.get("Description") or name})
    return found


def sources():
    """The microphones of the Linux sound server, or None when they cannot be listed."""
    try:
        result = subprocess.run(PACTL, capture_output=True, timeout=10, check=True,
                                env=os.environ | PACTL_ENV, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        logging.warning("Could not list the microphones: %s", error)
        return None
    return parse_sources(result.stdout.decode("utf-8", "replace"))


def run(args):
    """microphone list | set [NAME]

    Without NAME, "set" goes back to the Linux default. Prints the choice and the
    microphones as JSON for the Manager."""
    import multipak_input
    import tools.helpers.ipc

    if args.mode == "set":
        name = check(args.NAME or "")
        with multipak_input.preferences() as path:
            values = multipak_input.load_preferences(path)
            values[PREFERENCE] = name
            multipak_input.save_preferences(path, values)
        try:
            tools.helpers.ipc.DBusContainerService().SetMicrophone(name)
        except Exception as error:
            # Saved all the same: the session gives it to the service when Android starts.
            logging.warning("The microphone will change when Android starts: %s", error)
    listed = sources()
    print(json.dumps({"microphone": chosen(), "sources": listed or [], "listed": listed is not None},
                     ensure_ascii=False))

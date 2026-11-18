# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep Android in step with the desktop and report which integrations are active."""

import importlib.metadata
import importlib.util
import json
import logging
import os
import re
from pathlib import Path

from multipak_instance import current as instance

LOCALE = "persist.sys.locale"
TIMEZONE = "persist.sys.timezone"
# Only LineageOS 20 images forward notifications to the desktop.
NOTIFYING_RELEASES = ("20.0",)


def desktop_locale(environ=os.environ):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(name, "")
        if value:
            break
    match = re.fullmatch(r"([a-z]{2,3})(?:_([A-Z]{2}))?(?:\.[^@]*)?(?:@.*)?", value)
    if not match:
        return None
    return "-".join(part for part in match.groups() if part)


def desktop_timezone(localtime="/etc/localtime", environ=os.environ):
    value = environ.get("TZ", "").removeprefix(":")
    if not value:
        target = os.path.realpath(localtime)
        value = target.partition("/zoneinfo/")[2] if "/zoneinfo/" in target else ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}", value):
        return value
    return None


def sync(platform):
    """Copy the desktop language and time zone to Android. They apply on the next start."""
    changed = []
    for prop, value in ((LOCALE, desktop_locale()), (TIMEZONE, desktop_timezone())):
        if value and platform.getprop(prop, "") != value:
            platform.setprop(prop, value)
            changed.append(prop)
    if changed:
        logging.info("Android will follow the desktop %s from its next start", ", ".join(changed))
    return changed


def _gbinder_version():
    try:
        return importlib.metadata.version("gbinder-python")
    except importlib.metadata.PackageNotFoundError:
        return None


def _version_at_least(value, minimum):
    try:
        return tuple(int(p) for p in value.split(".")[:2]) >= minimum
    except (AttributeError, ValueError):
        return False


def diagnose(session_bus=None, platform=None):
    gbinder = _gbinder_version()
    result = {
        "instance": instance().id,
        "release": instance().release,
        "notifications_image": instance().release in NOTIFYING_RELEASES,
        "notifications_server": bool(session_bus and session_bus.name_has_owner("org.freedesktop.Notifications")),
        "gbinder": gbinder,
        "gbinder_images_safe": _version_at_least(gbinder, (1, 3)),
        "clipboard_text": True,
        "clipboard_pyclip": importlib.util.find_spec("pyclip") is not None,
        "running": platform is not None,
        "desktop_locale": desktop_locale(),
        "desktop_timezone": desktop_timezone(),
    }
    if platform is not None:
        result["android_locale"] = platform.getprop(LOCALE, "")
        result["android_timezone"] = platform.getprop(TIMEZONE, "")
    return result


def run(args):
    import dbus
    import tools.helpers.ipc
    from tools.interfaces import IPlatform

    try:
        bus = dbus.SessionBus()
    except dbus.DBusException:
        bus = None
    platform = None
    try:
        tools.helpers.ipc.DBusSessionService()
        if tools.helpers.ipc.DBusContainerService().GetSession().get("state") == "RUNNING":
            platform = IPlatform.get_service(args)
    except dbus.DBusException:
        pass
    print(json.dumps(diagnose(bus, platform)))

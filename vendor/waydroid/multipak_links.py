# SPDX-License-Identifier: GPL-3.0-or-later
"""Links between Android and the Linux desktop for one named MultiPak instance."""

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import multipak_files
import multipak_input
import multipak_outbox
from multipak_instance import current as instance

HELPER = multipak_input.MAPPER
BROWSER = "android.app.role.BROWSER"
# Read by the helper app: "1" sends links to Linux; anything else keeps them in Android.
SETTING = "multipak_links"
VIEW = "android.intent.action.VIEW"
MAX_URI = 4096
# Addresses Linux must never pass to Android as they are. intent:// and market://
# are read by route() and rebuilt from the few parts it keeps.
REFUSED_SCHEMES = frozenset(
    "about blob content data file http https intent javascript mailto market".split()
)
SCHEME = r"[A-Za-z][A-Za-z0-9+.-]{0,31}"
# Android stores that open market://details; without one, the web page opens on Linux.
STORES = ("com.android.vending", "org.fdroid.fdroid", "org.fdroid.basic", "com.aurora.store")
STORE_PAGE = "https://play.google.com/store/apps/details?id="


# Fixed command run by the root service; it takes nothing from the caller. Turning
# links off keeps the role: the helper then hands every link to the Android browser.
ROLE_COMMAND = ("role", "add-role-holder", "--user", "0", BROWSER, HELPER)


DEFAULT = True


def wanted():
    with multipak_input.preferences() as path:
        return multipak_input.load_preferences(path).get("links", DEFAULT) is True


def apply(platform, manager, enabled, force=False):
    """Set the Android default browser once per change; later choices made in Android stay."""
    with multipak_input.preferences() as path:
        values = multipak_input.load_preferences(path)
        if not force and values.get("links_applied") == enabled:
            return False
        value = "1" if enabled else "0"
        platform.settingsPutString(multipak_input.SECURE, SETTING, value)
        if platform.settingsGetString(multipak_input.SECURE, SETTING) != value:
            raise RuntimeError("Android did not confirm the links preference.")
        if enabled:
            manager.SetDefaultBrowser()
        values["links"] = enabled
        values["links_applied"] = enabled
        multipak_input.save_preferences(path, values)
    return True


def ensure(platform, manager):
    """Install or update the helper when Android is ready, then apply the saved choice."""
    installed = multipak_input.ensure_mapper(platform, manager)
    return apply(platform, manager, wanted(), force=installed)


def run(args):
    import tools.helpers.ipc
    from tools.interfaces import IPlatform

    tools.helpers.ipc.DBusSessionService()
    manager = tools.helpers.ipc.DBusContainerService()
    frozen = manager.GetSession().get("state") == "FROZEN"
    manager.Unfreeze()
    try:
        platform = IPlatform.get_service(args)
        if not platform:
            raise RuntimeError("Android is not ready. Open an application and try again.")
        enabled = args.mode == "on"
        if enabled:
            multipak_input.ensure_mapper(platform, manager)
        apply(platform, manager, enabled, force=True)
    finally:
        if frozen:
            manager.Freeze()
    logging.info("Android links open on %s", "Linux" if enabled else "Android")
    print(json.dumps({"links": enabled}))


def _plain(uri):
    if not isinstance(uri, str) or not 0 < len(uri) <= MAX_URI:
        raise ValueError("Invalid address length")
    if not uri.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in uri):
        raise ValueError("Addresses must not contain spaces or control characters")
    return uri


def check_uri(uri):
    """Return the address if Linux may hand it to Android, or raise ValueError."""
    match = re.match(SCHEME + ":", _plain(uri))
    if not match or match.group(0)[:-1].lower() in REFUSED_SCHEMES:
        raise ValueError("This address cannot be opened in Android")
    return uri


def _market(uri, installed):
    """market://details?id=APP: the Android store if there is one, else its web page."""
    parts = urlsplit(_plain(uri))
    ids = parse_qs(parts.query).get("id", [])
    if parts.scheme.lower() != "market" or parts.netloc != "details" or len(ids) != 1:
        raise ValueError("Only market://details?id= addresses are supported")
    multipak_input.validate_package(ids[0])
    if any(installed(store) for store in STORES):
        return "android", "market://details?id=" + ids[0]
    return "linux", STORE_PAGE + ids[0]


def _intent(uri, installed):
    """intent://…#Intent;…;end, read like Chrome but keeping only the app, the scheme
    and the web fallback; actions, components and extras never reach Android."""
    body, _, tail = _plain(uri).rpartition("#")
    items = tail.split(";")
    if not body.lower().startswith("intent:") or items[0] != "Intent" or items[-1:] != ["end"]:
        raise ValueError("Invalid intent address")
    fields = {}
    for item in items[1:-1]:
        key, _, value = item.partition("=")
        if key in fields:
            raise ValueError("Repeated intent field")
        fields[key] = unquote(value)
    package = fields.get("package")
    scheme = fields.get("scheme")
    fallback = fields.get("S.browser_fallback_url")
    if package:
        multipak_input.validate_package(package)
    if scheme is not None and not re.fullmatch(SCHEME, scheme):
        raise ValueError("Invalid intent scheme")
    target = scheme + body[len("intent"):] if scheme else None
    if package and target and installed(package):
        try:
            return "android", check_uri(target)
        except ValueError:
            pass  # A web address: Android would hand it back to the Linux browser anyway.
    if fallback:
        return "linux", multipak_outbox.check_link(fallback, multipak_outbox.WEB_SCHEMES)
    if target and scheme.lower() in multipak_outbox.WEB_SCHEMES:
        return "linux", multipak_outbox.check_link(target, multipak_outbox.WEB_SCHEMES)
    if package:
        return _market("market://details?id=" + package, installed)
    raise ValueError("This intent address names no app and no web address")


def route(uri, installed, app=True):
    """Where an address from Linux opens: ("android", address) or ("linux", address).

    Without an app (the environment's shortcut), only intent:// and market:// pass."""
    scheme = _plain(uri).partition(":")[0].lower()
    if scheme == "intent":
        return _intent(uri, installed)
    if scheme == "market":
        return _market(uri, installed)
    if not app:
        raise ValueError("Only intent:// and market:// addresses open without an app")
    return "android", check_uri(uri)


def installed(package):
    """Whether this environment has the app, from its shortcuts; Android may be stopped."""
    import tools.config

    folder = Path(tools.config.session_defaults["xdg_data_home"]) / "applications"
    return (folder / instance().desktop_name(package)).is_file()


def open_on_linux(uri):
    from gi.repository import Gio

    Gio.AppInfo.launch_default_for_uri(uri, None)


def shortcut_name():
    # "links" is not a package name, so it never clashes with an app's shortcut, and the
    # environment's removal already deletes multipak-waydroid.<id>.*.desktop.
    return f"multipak-waydroid.{instance().id}.links.desktop"


def register(applications):
    """One hidden shortcut per environment for intent:// and market://; the app they
    name comes from the address itself. Rewritten and re-indexed only when it changes."""
    import shutil
    import subprocess

    path = Path(applications) / shortcut_name()
    label = instance().label.replace("\\", "\\\\")
    text = (
        "[Desktop Entry]\nType=Application\n"
        f"Name=Android links — {label}\n"
        f"Exec={instance().command('open-uri')} %u\n"
        "Icon=waydroid\nTerminal=false\nNoDisplay=true\n"
        "MimeType=x-scheme-handler/intent;x-scheme-handler/market;\n"
    )
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)
    # GLib apps read mimeinfo.cache and KDE its own cache; both are best effort.
    for argv in (["update-desktop-database", str(applications)], ["kbuildsycoca6"]):
        if tool := shutil.which(argv[0]):
            try:
                subprocess.run([tool, *argv[1:]], capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass
    return True


def open_uri(args):
    """Desktop entry for Android addresses: an app's own (with no address, just open the
    app), or intent:// and market:// from the environment's shortcut, without an app."""
    import tools.actions.app_manager

    if args.URI is None and ":" in args.PACKAGE:
        args.PACKAGE, args.URI = None, args.PACKAGE
        where, uri = route(args.URI, installed, app=False)
    else:
        multipak_input.validate_package(args.PACKAGE)
        if not args.URI:
            tools.actions.app_manager.launch(args)
            return
        where, uri = route(args.URI, installed)
    if where == "linux":
        open_on_linux(uri)
        return
    args.ACTION, args.URI = VIEW, uri
    tools.actions.app_manager.intent(args)


def listen(taken=None):
    """In the user's session: open on Linux the links the helper leaves in its outbox, and
    save the files shared with Linux.

    taken() runs after each reading, so the media state it fetches is the newest."""
    import dbus
    import tools.helpers.ipc

    def opened(uris):
        for uri in uris:
            try:
                open_on_linux(multipak_outbox.check_link(str(uri)))
            except Exception as error:
                logging.warning("Could not open an Android link on Linux: %s", error)
        if taken:
            taken()

    def failed(error):
        logging.warning("Could not read Android links: %s", error)
        if taken:
            taken()

    def received(files):
        multipak_files.receive(files)

    def not_received(error):
        logging.warning("Could not read the files Android sent: %s", error)

    def waiting():
        manager = tools.helpers.ipc.DBusContainerService()
        manager.TakeRequests(reply_handler=opened, error_handler=failed)
        # The same signal wakes the session for files shared with Linux.
        manager.TakeFiles(reply_handler=received, error_handler=not_received)

    dbus.SystemBus().add_signal_receiver(
        waiting,
        signal_name="LinksWaiting",
        dbus_interface="id.waydro.ContainerManager",
        bus_name=instance().container_bus,
        path="/ContainerManager",
    )

"""Launch an AppImage integrated by MultiPak, using the system Python."""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multipak import host  # noqa: E402
from multipak.appimage import RepairNeeded, launch  # noqa: E402
from multipak.localization import tr  # noqa: E402


def _text(value):
    """A GVariant string literal."""
    value = " ".join(str(value).split())
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _portal_notify(summary, body):
    """Inside the Flatpak, through the notification portal, which needs no permission."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtDBus import QDBusConnection, QDBusMessage

    application = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
    message = QDBusMessage.createMethodCall(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Notification",
        "AddNotification",
    )
    message.setArguments(["multipak-appimage", {"title": summary, "body": body}])
    reply = QDBusConnection.sessionBus().call(message)
    return reply.type() == QDBusMessage.MessageType.ReplyMessage


def notify(summary, body):
    """Tell the user in a desktop notification: a shortcut has no window to show errors.
    Failing to notify is never an error."""
    if host.is_flatpak():
        try:
            if _portal_notify(summary, body):
                return
        except Exception:  # noqa: BLE001 - fall back to gdbus below
            pass
    gdbus = shutil.which("gdbus")
    if host.is_flatpak() and gdbus:
        command = [
            gdbus,
            "call",
            "--session",
            "--dest",
            "org.freedesktop.portal.Desktop",
            "--object-path",
            "/org/freedesktop/portal/desktop",
            "--method",
            "org.freedesktop.portal.Notification.AddNotification",
            "multipak-appimage",
            f"{{'title': <{_text(summary)}>, 'body': <{_text(body)}>}}",
        ]
    elif gdbus:
        command = [
            gdbus,
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.Notify",
            "'MultiPak'",
            "uint32 0",
            "'dialog-warning'",
            _text(summary),
            _text(body),
            "@as []",
            "@a{sv} {}",
            "int32 -1",
        ]
    elif shutil.which("notify-send"):
        command = ["notify-send", "--app-name=MultiPak", "--icon=dialog-warning", summary, body]
    else:
        return
    try:
        subprocess.run(command, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setenv", action="append", default=[])
    parser.add_argument("transaction")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    environment = {}
    for value in args.setenv:
        name, separator, content = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            parser.error("Invalid environment variable.")
        environment[name] = content
    try:
        launch(args.transaction, args.arguments, environment)
    except RepairNeeded as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        notify(
            tr("Could not open {0}.").format(error.name),
            tr("Open the MultiPak Application Manager to repair it."),
        )
        return 1
    except Exception as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        notify(tr("Could not open the application."), str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

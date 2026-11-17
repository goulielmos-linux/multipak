"""Open an app of a snap installed by MultiPak."""

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multipak.appimage_launcher import notify  # noqa: E402
from multipak.localization import tr  # noqa: E402
from multipak.snap import launch  # noqa: E402


def _services(arguments):
    """--service TX NAME runs one background service (systemd calls it);
    --services ACTION TX [NAMES] starts, stops, restarts or lists them;
    --set TX KEY=VALUE… changes the snap's configuration, as snap set."""
    from multipak.snap import configure, control_services, run_service

    try:
        if arguments[0] == "--set" and len(arguments) >= 3:
            configure(arguments[1], arguments[2:])
            return 0
        if arguments[0] == "--service" and len(arguments) == 3:
            return run_service(arguments[1], arguments[2])
        if arguments[0] == "--services" and len(arguments) >= 3:
            print(control_services(arguments[2], arguments[1], arguments[3:]), end="")
            return 0
        print(
            "usage: multipak-snap --services start|stop|restart|list|login TX [SERVICE…]\n"
            "       multipak-snap --set TX KEY=VALUE…\n"
            "       multipak-snap --admin TX APP [ARGUMENTS…]"
        )
        return 2
    except Exception as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        return 1


def _admin(arguments):
    """--admin TX APP [ARGUMENTS…]: the app as root in its sandbox, as sudo would run
    it with snapd. The option itself is the user's explicit consent."""
    if len(arguments) < 3 or arguments[1].startswith("-"):
        print("usage: multipak-snap --admin TX APP [ARGUMENTS…]", file=sys.stderr)
        return 2
    warning = tr(
        "Running {0} as the snap's administrator. It is root inside the snap's sandbox, "
        "never on your computer, and can change all of the snap's data."
    ).format(arguments[2])
    print(f"MultiPak: {warning}", file=sys.stderr, flush=True)
    try:
        return launch(arguments[1], arguments[2], arguments[3:], admin=True)
    except Exception as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        return 1


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]  # flatpak run may pass on its separator.
    if arguments[:1] == ["--admin"]:
        return _admin(arguments)
    if arguments[:1] in (["--service"], ["--services"], ["--set"]):
        return _services(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transaction")
    parser.add_argument("app")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        return launch(args.transaction, args.app, args.arguments)
    except Exception as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        notify(tr("Could not open the application."), str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

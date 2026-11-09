"""Launch an AppImage integrated by MultiPak, using the system Python."""

import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multipak.appimage import launch  # noqa: E402


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
    except Exception as error:
        print(f"MultiPak: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

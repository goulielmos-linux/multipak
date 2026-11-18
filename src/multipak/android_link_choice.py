"""Ask which program opens an address that an Android app and another program both declare.

On by default: MultiPak becomes the default only for the addresses with more than one
program, shows the choice, and opens the chosen program's shortcut directly, never through
xdg-open. Off, it only registers the Android apps' own addresses (myapp://)."""

import configparser
import io
import json
import re
import subprocess
from pathlib import Path

from . import host
from .file_associations import (
    SECTIONS,
    FileAssociations,
    _atomic_write,
    _restore,
    _without_multipak,
)
from .integration import DESKTOP_ID
from .localization import tr

STATE = "multipak/android-links.json"
SCHEME = re.compile(r"[a-z][a-z0-9+.-]{0,31}")
MAX_URI = 4096
# gio launch rewrites addresses it does not know (myapp://x?a=1 becomes myapp://x/?a=1),
# which breaks logins that compare them; launch_uris passes the address as it is.
LAUNCH = (
    "import sys; from gi.repository import Gio; "
    "Gio.DesktopAppInfo.new_from_filename(sys.argv[1]).launch_uris([sys.argv[2]], None)"
)
# Lists the programs GLib knows for one address type, each with its shortcut, name and icon.
HANDLERS = r"""
LC_ALL=C gio mime "x-scheme-handler/$1" 2>/dev/null |
sed -n '/^Registered applications:/,/^[^\t]/s/^\t//p' |
while IFS= read -r id; do
    for d in "${XDG_DATA_HOME:-$HOME/.local/share}" \
        $(printf %s "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" | tr : " "); do
        f="$d/applications/$id"
        if [ -f "$f" ]; then
            printf '%s\t%s\t%s\t%s\n' "$id" "$f" \
                "$(sed -n 's/^Name=//p' "$f" | head -n 1)" "$(sed -n 's/^Icon=//p' "$f" | head -n 1)"
            break
        fi
    done
done
"""


def _paths():
    data, config = host.directories()
    return Path(data) / "applications", Path(config) / STATE


def _read_state(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    before = value.get("before")
    owned = value.get("owned")
    return {
        "ask": value.get("ask") is not False,  # On until the user turns it off.
        "before": {
            k: v for k, v in (before if isinstance(before, dict) else {}).items()
            if isinstance(k, str) and isinstance(v, str)
        },
        "owned": sorted(
            s for s in (owned if isinstance(owned, list) else []) if isinstance(s, str)
            and SCHEME.fullmatch(s)
        ),
    }


def asking():
    return _read_state(_paths()[1])["ask"]


def declared_schemes(applications):
    """Addresses declared by the hidden link shortcuts of installed Android apps."""
    schemes = set()
    for path in sorted(Path(applications).glob("multipak-*-links.desktop")):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
        except (OSError, configparser.Error, UnicodeDecodeError):
            continue
        for item in parser.get("Desktop Entry", "MimeType", fallback="").split(";"):
            scheme = item.removeprefix("x-scheme-handler/")
            if item.startswith("x-scheme-handler/") and SCHEME.fullmatch(scheme):
                schemes.add(scheme)
    return schemes


def handlers(scheme):
    """[(desktop id, shortcut, name, icon)] of the programs for scheme, without MultiPak."""
    if not SCHEME.fullmatch(scheme):
        return []
    try:
        result = host.run(
            ["sh", "-c", HANDLERS, "multipak-handlers", scheme],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 4 or fields[0] == DESKTOP_ID or not fields[0].endswith(".desktop"):
            continue
        if not fields[1].startswith("/") or fields[0] in (f[0] for f in found):
            continue
        found.append((fields[0], fields[1], fields[2] or fields[0].removesuffix(".desktop"), fields[3]))
    return found


def _set_default(config, mime, before):
    defaults = config["Default Applications"]
    if defaults.get(mime, "").split(";")[0] != DESKTOP_ID:
        before[mime] = _without_multipak(defaults.get(mime))
    defaults[mime] = DESKTOP_ID + ";"
    others = _without_multipak(config["Added Associations"].get(mime))
    config["Added Associations"][mime] = DESKTOP_ID + ";" + (others + ";" if others else "")
    removed = _without_multipak(config["Removed Associations"].get(mime))
    if removed:
        config["Removed Associations"][mime] = removed + ";"
    else:
        config["Removed Associations"].pop(mime, None)


def _give_back(config, mime, before):
    defaults = config["Default Applications"]
    if DESKTOP_ID in defaults.get(mime, "").split(";"):
        restored = _without_multipak(before.get(mime, defaults.get(mime)))
        if restored:
            defaults[mime] = restored + ";"
        else:
            defaults.pop(mime, None)
    added = _without_multipak(config["Added Associations"].get(mime))
    if added:
        config["Added Associations"][mime] = added + ";"
    else:
        config["Added Associations"].pop(mime, None)
    before.pop(mime, None)


def refresh(ask=None, *, find=handlers):
    """Become the default of the addresses in conflict while asking; give back the rest.

    ask: True or False saves the choice; None keeps it (after installing or removing
    an Android app)."""
    applications, state_path = _paths()
    associations = FileAssociations()
    with associations._locked():
        state = _read_state(state_path)
        if ask is not None:
            state["ask"] = bool(ask)
        wanted = set()
        if state["ask"]:
            wanted = {s for s in declared_schemes(applications) if len(find(s)) > 1}
        owned = set(state["owned"])
        if not state["ask"] and not owned and ask is None:
            return []
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.optionxform = str
        if associations.path.exists():
            config.read_string(associations.path.read_text(encoding="utf-8"))
        for section in SECTIONS:
            if not config.has_section(section):
                config.add_section(section)
        before = state["before"]
        for scheme in sorted(wanted):
            _set_default(config, "x-scheme-handler/" + scheme, before)
        for scheme in sorted(owned - wanted):
            _give_back(config, "x-scheme-handler/" + scheme, before)
        state["owned"] = sorted(wanted)
        output = io.StringIO()
        config.write(output, space_around_delimiters=False)
        snapshots = {
            path: path.read_bytes() if path.exists() else None
            for path in (associations.path, state_path)
        }
        try:
            if wanted or owned:
                _atomic_write(associations.path, output.getvalue())
            _atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
        except Exception:
            _restore(snapshots)
            raise
    if wanted != owned and (cache := host.which("kbuildsycoca6")):
        try:
            host.run([cache], capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(wanted)


def scheme_of(uri):
    """The scheme of an address MultiPak was asked to choose for, or None."""
    if not isinstance(uri, str) or not 0 < len(uri) <= MAX_URI:
        return None
    scheme, colon, _ = uri.partition(":")
    scheme = scheme.lower()
    if not colon or not SCHEME.fullmatch(scheme):
        return None
    return scheme if scheme in _read_state(_paths()[1])["owned"] else None


def launch(shortcut, uri):
    """Open uri with one program's shortcut, never through xdg-open (that would come back)."""
    if any(ord(c) < 32 or ord(c) == 127 for c in uri) or len(uri) > MAX_URI:
        raise ValueError(tr("This address cannot be opened."))
    host.run(
        ["python3", "-I", "-c", LAUNCH, str(shortcut), uri],
        check=True,
        timeout=30,
        capture_output=True,
    )

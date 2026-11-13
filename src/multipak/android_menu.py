"""Group managed Android shortcuts using the freedesktop menu merge mechanism."""

import configparser
import json
import os
import re
import shlex
from pathlib import Path
from xml.sax.saxutils import escape

from . import host


def matches_shortcut(path, expected):
    """Confirm package and instance before migrating a generated launcher."""
    if path.is_symlink():
        return False
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
        args = shlex.split(entry.get("Exec", ""))
        return (
            entry.get("Type") == "Application"
            and bool(args)
            and Path(args[0]).name == Path(expected[0]).name
            and args[1:] == expected[1:]
        )
    except (OSError, UnicodeError, ValueError, KeyError, configparser.Error):
        return False


def register_shortcut(applications, filename=None):
    if filename is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+\.desktop", filename):
        raise ValueError("Invalid Android desktop filename")
    data, config = host.directories()
    index = data / "multipak/android-shortcuts.json"
    try:
        names = json.loads(index.read_text())
    except (OSError, ValueError):
        names = []
    names = names if isinstance(names, list) else []
    names = sorted(
        {
            *((filename,) if filename else ()),
            *(
                n
                for n in names
                if isinstance(n, str)
                and re.fullmatch(r"[A-Za-z0-9_.-]+\.desktop", n)
                and (applications / n).is_file()
            ),
        }
    )
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps(names))
    directory = data / "desktop-directories/multipak-android.directory"
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("[Desktop Entry]\nType=Directory\nName=Android apps\nIcon=phone\n")
    merged = config / "menus/applications-merged/multipak-android.menu"
    merged.parent.mkdir(parents=True, exist_ok=True)
    include = "".join("<Filename>" + escape(n) + "</Filename>" for n in names)
    owned = "".join(
        "<Filename>" + escape(n) + "</Filename>"
        for n in names
        if re.fullmatch(r"multipak-waydroid\.[a-z][a-z0-9]{0,10}\..+\.desktop", n)
    )
    text = (
        "<!DOCTYPE Menu PUBLIC '-//freedesktop//DTD Menu 1.0//EN' "
        "'http://www.freedesktop.org/standards/menu-spec/1.0/menu.dtd'>\n"
        "<Menu><Name>Applications</Name><Menu><Name>MultiPakAndroid</Name>"
        "<Directory>multipak-android.directory</Directory><Include>"
        + include
        + "</Include></Menu><Menu><Name>Waydroid</Name><Exclude>"
        + owned
        + "</Exclude></Menu></Menu>\n"
    )
    merged.write_text(text)
    # Plasma's menu prefix is plasma-; the unprefixed merge is used by other desktops.
    plasma = config / "menus/plasma-applications-merged/multipak-android.menu"
    plasma.parent.mkdir(parents=True, exist_ok=True)
    plasma.write_text(text)


def migrate_installed(store=None):
    """Register already installed Android apps without contacting Android."""
    from .store import Store

    store = store or Store()
    with store.lock():
        _migrate_owned_shortcuts(store)
    records = [
        tx for tx in store.transactions() if tx.method == "waydroid" and tx.status == "installed"
    ]
    register_shortcut(store.applications)
    for path in store.applications.glob("*.desktop"):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
            entry = parser["Desktop Entry"]
            if entry.get("Hidden", "false").lower() == "true":
                continue
            args = shlex.split(entry.get("Exec", ""))
            for tx in records:
                expected = (
                    ["--instance", tx.android_instance_id, "app", "launch", tx.android_package_id]
                    if tx.android_instance_id
                    else ["app", "launch", tx.android_package_id]
                )
                executable = "multipak-waydroid" if tx.android_instance_id else "waydroid"
                if args and args[1:] == expected and Path(args[0]).name == executable:
                    register_shortcut(store.applications, path.name)
                    break
        except (OSError, UnicodeError, ValueError, KeyError, configparser.Error):
            continue


def _migrate_owned_shortcuts(store):
    for tx in store.transactions():
        if tx.method != "waydroid" or tx.status != "installed" or not tx.android_instance_id:
            continue
        name = f"multipak-waydroid.{tx.android_instance_id}.{tx.android_package_id}.desktop"
        try:
            canonical = store.launcher(tx, name)
        except ValueError:
            continue
        expected = [
            "multipak-waydroid",
            "--instance",
            tx.android_instance_id,
            "app",
            "launch",
            tx.android_package_id,
        ]
        legacy_name = f"multipak-{tx.id}-android.desktop"
        legacy = store.launcher(tx, legacy_name)
        if matches_shortcut(legacy, expected):
            if not canonical.exists():
                try:
                    os.link(legacy, canonical)
                except FileExistsError:
                    pass
            if matches_shortcut(canonical, expected):
                backup = store.directory(tx.id) / "legacy-android-launcher.desktop.bak"
                if not backup.exists():
                    backup.write_bytes(legacy.read_bytes())
                legacy.unlink()
                tx.launchers = [n for n in tx.launchers if n != legacy_name]
        if matches_shortcut(canonical, expected):
            tx.launchers = sorted(set([*tx.launchers, name]))
            store.save(tx)


if __name__ == "__main__":
    migrate_installed()

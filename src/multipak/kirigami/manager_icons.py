"""Resolve existing application icons without launching or querying containers."""

import configparser
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QIcon

GENERIC_ICON = QUrl.fromLocalFile(
    str(Path(__file__).with_name("assets") / "application.svg")
).toString()
_GENERIC_NAMES = {"application-x-executable", "application-x-generic"}


def _source(value):
    if not value or value in _GENERIC_NAMES:
        return ""
    path = Path(value)
    if value.startswith("file:"):
        url = QUrl(value)
        if not url.isLocalFile():
            return ""
        path = Path(url.toLocalFile())
    if path.is_absolute():
        if path.is_file() and not QIcon(str(path)).pixmap(32, 32).isNull():
            return QUrl.fromLocalFile(str(path)).toString()
        return ""
    if "/" not in value and not QIcon.fromTheme(value).pixmap(32, 32).isNull():
        return value
    return ""


def application_icon(store, transaction):
    launchers = []
    for name in transaction.launchers:
        try:
            launchers.append(store.launcher(transaction, name))
        except ValueError:
            continue
    # Direct system installations may use a system launcher rather than an export.
    package = transaction.package_name
    if transaction.method == "system" and Path(package).name == package:
        launchers.extend(
            Path(directory) / (package + ".desktop")
            for directory in ("/usr/local/share/applications", "/usr/share/applications")
        )
    for path in launchers:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
            icon = _source(parser.get("Desktop Entry", "Icon", fallback="").replace("\\\\", "\\"))
            if icon:
                return icon
        except (OSError, UnicodeError, ValueError, configparser.Error):
            continue
    # AppImage icons are stored with the managed copy, not with the installation record.
    directories = [store.directory(transaction.id)]
    if transaction.method == "appimage":
        try:
            directories.insert(0, store.appimage_directory(transaction.id))
        except (OSError, ValueError):
            pass
    for directory in directories:
        for extension in (".png", ".svg"):
            icon = _source(str(directory / ("icon" + extension)))
            if icon:
                return icon
    return _source(package) or GENERIC_ICON

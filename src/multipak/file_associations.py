"""Per-user file-opening preferences; unrelated associations are preserved."""

import configparser
import fcntl
import io
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from multipak.localization import mark, tr

from . import host
from .integration import DESKTOP_ID

LEGACY_INSTALLER = host.LEGACY_APP_ID + ".installer.desktop"
APPIMAGE_MIME = "application/x-multipak-appimage"
FORMATS = {
    "rpm": ("RPM (.rpm)", ("application/x-rpm",)),
    "deb": ("DEB (.deb)", ("application/vnd.debian.binary-package", "application/x-deb")),
    "arch": (
        mark(
            "Arch Linux packages (.pkg.tar, .pkg.tar.zst, .pkg.tar.xz, .pkg.tar.gz "
            "and .pkg.tar.bz2)"
        ),
        ("application/x-archlinux-package",),
    ),
    "appimage": (
        "AppImage (.AppImage)",
        (
            "application/vnd.appimage",
            "application/x-iso9660-appimage",
            "application/x-appimage",
            APPIMAGE_MIME,
        ),
    ),
}
SECTIONS = ("Default Applications", "Added Associations", "Removed Associations")


def _without_multipak(value):
    return ";".join(item for item in (value or "").split(";") if item and item != DESKTOP_ID)


def _replace_legacy_installer(value):
    items = value.split(";")
    if LEGACY_INSTALLER not in items:
        return value
    result = []
    for item in items:
        if item in (LEGACY_INSTALLER, DESKTOP_ID):
            if DESKTOP_ID not in result:
                result.append(DESKTOP_ID)
        else:
            result.append(item)
    return ";".join(result)


def _migrate_mimeapps(text, mimes):
    section = ""
    lines = []
    for line in text.splitlines(keepends=True):
        content = line.strip()
        if content.startswith("[") and content.endswith("]"):
            section = content[1:-1]
        if section in SECTIONS[:2]:
            key, delimiter, value = line.partition("=")
            if delimiter and key.strip() in mimes:
                updated = _replace_legacy_installer(value.strip())
                if updated != value.strip():
                    newline = line[len(line.rstrip("\r\n")) :]
                    line = f"{key}={updated}{newline}"
        lines.append(line)
    return "".join(lines)


def _restore(snapshots):
    for path, content in snapshots.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(path, content)


def _atomic_write(path, content):
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _preferred_application(mime):
    if host.is_flatpak():
        return host.run(
            ["xdg-mime", "query", "default", mime],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    executable = shutil.which("xdg-mime")
    if not executable:
        raise RuntimeError(tr("Could not query file associations: xdg-utils is missing."))
    # Older xdg-utils use the Qt 5 executable name even on Plasma 6.
    with TemporaryDirectory(prefix="multipak-qtpaths-") as directory:
        environment = dict(os.environ)
        if not shutil.which("qtpaths"):
            helper = shutil.which("qtpaths6") or shutil.which("qtpaths-qt6")
            if helper:
                (Path(directory) / "qtpaths").symlink_to(Path(helper).resolve())
                environment["PATH"] = directory + os.pathsep + environment.get("PATH", os.defpath)
        result = subprocess.run(
            [executable, "query", "default", mime],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return result.stdout.strip()


class FileAssociations:
    def __init__(self, *, config_home=None, data_home=None, reader=None, refresher=None):
        data, config = host.directories()
        self.config = Path(config_home or config)
        self.data = Path(data_home or data)
        desktop_config = self.config / "kde-mimeapps.list"
        self.path = desktop_config if desktop_config.is_file() else self.config / "mimeapps.list"
        self.state_path = self.config / "multipak/file-associations.json"
        self.xml_path = self.data / "mime/packages/io.github.multipak.appimage.xml"
        self.arch_xml_path = self.data / "mime/packages/io.github.multipak.packages.xml"
        self.reader = reader or _preferred_application
        self.refresher = refresher or self._refresh

    @contextmanager
    def _locked(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def initialize_defaults(self):
        if not host.is_flatpak():
            return False
        with self._locked():
            if self._read_state().get("desktop_id") == DESKTOP_ID:
                return False
            self._apply({key: True for key in FORMATS})
        return True

    def migrate_legacy(self, *, removed=False):
        if not host.is_flatpak():
            return
        with self._locked():
            mimes = {mime for _, aliases in FORMATS.values() for mime in aliases}
            writes = {}
            snapshots = {}
            for path in dict.fromkeys((self.config / "mimeapps.list", self.path)):
                if not path.exists():
                    continue
                original = path.read_bytes()
                text = original.decode("utf-8")
                migrated_text = _migrate_mimeapps(text, mimes)
                if migrated_text != text:
                    writes[path] = migrated_text
                    snapshots[path] = original
            state = self._read_state()
            before = state.get("before")
            if isinstance(before, dict):
                migrated = {
                    mime: _replace_legacy_installer(value)
                    if mime in mimes and isinstance(value, str)
                    else value
                    for mime, value in before.items()
                }
                if migrated != before:
                    state["before"] = migrated
            if (
                removed
                or writes
                or state.get("before") != before
                or state.get("desktop_id") == LEGACY_INSTALLER
            ):
                state["desktop_id"] = DESKTOP_ID
                state_path = self.state_path
                writes[state_path] = json.dumps(state, ensure_ascii=False, indent=2)
                snapshots[state_path] = state_path.read_bytes() if state_path.exists() else None
            if not writes:
                return
            try:
                for path, content in writes.items():
                    _atomic_write(path, content)
                self.refresher()
            except Exception:
                _restore(snapshots)
                raise

    def _read_state(self):
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def load(self):
        selected = {}
        saved = self._read_state().get("selected", {})
        if not isinstance(saved, dict):
            saved = {}
        config = configparser.ConfigParser(interpolation=None, strict=False)
        if self.path.exists():
            config.read(self.path, encoding="utf-8")
        for key, (_, mimes) in FORMATS.items():
            mime = APPIMAGE_MIME if key == "appimage" and self.xml_path.exists() else mimes[0]
            # Read explicit user choices before a desktop helper's cached result.
            # Prefer the canonical type when an alternate MIME alias has a different default.
            defaults = (
                config.get("Default Applications", candidate, fallback="").split(";")[0]
                for candidate in dict.fromkeys((mime, *mimes))
            )
            if explicit := next((value for value in defaults if value), ""):
                selected[key] = explicit == DESKTOP_ID
                continue
            if saved.get(key) is False:
                selected[key] = False
                continue
            selected[key] = self.reader(mime) == DESKTOP_ID
        return selected

    def _refresh(self):
        (self.data / "mime").mkdir(parents=True, exist_ok=True)
        updater = host.which("update-mime-database")
        if not updater:
            raise RuntimeError(tr("Could not update file types: shared-mime-info is missing."))
        host.run(
            [updater, str(self.data / "mime")],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if cache := host.which("kbuildsycoca6"):
            host.run(
                [cache, "--noincremental"], capture_output=True, text=True, check=True, timeout=60
            )

    def apply(self, selected):
        with self._locked():
            return self._apply(selected)

    def _apply(self, selected):
        if set(selected) != set(FORMATS) or any(
            type(value) is not bool for value in selected.values()
        ):
            raise ValueError(tr("Invalid file type selection."))
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.optionxform = str
        if self.path.exists():
            config.read_string(self.path.read_text(encoding="utf-8"))
        before = self._read_state().get("before", {})
        before = (
            {mime: value for mime, value in before.items() if isinstance(value, str)}
            if isinstance(before, dict)
            else {}
        )
        for section in SECTIONS:
            if not config.has_section(section):
                config.add_section(section)
        for key, (_, mimes) in FORMATS.items():
            for mime in mimes:
                current = config["Default Applications"].get(mime, "")
                if selected[key]:
                    if mime not in before or current.split(";")[0] != DESKTOP_ID:
                        before[mime] = _without_multipak(current)
                    config["Default Applications"][mime] = DESKTOP_ID + ";"
                    others = _without_multipak(config["Added Associations"].get(mime))
                    config["Added Associations"][mime] = (
                        DESKTOP_ID + ";" + (others + ";" if others else "")
                    )
                    removed = _without_multipak(config["Removed Associations"].get(mime))
                    if removed:
                        config["Removed Associations"][mime] = removed + ";"
                    else:
                        config["Removed Associations"].pop(mime, None)
                else:
                    if DESKTOP_ID in current.split(";"):
                        restored = _without_multipak(before.get(mime, current))
                        if restored:
                            config["Default Applications"][mime] = restored + ";"
                        else:
                            config["Default Applications"].pop(mime, None)
                    added = _without_multipak(config["Added Associations"].get(mime))
                    if added:
                        config["Added Associations"][mime] = added + ";"
                    else:
                        config["Added Associations"].pop(mime, None)
                    removed = _without_multipak(config["Removed Associations"].get(mime))
                    config["Removed Associations"][mime] = (
                        (removed + ";" if removed else "") + DESKTOP_ID + ";"
                    )
        output = io.StringIO()
        config.write(output, space_around_delimiters=False)
        snapshots = {
            path: path.read_bytes() if path.exists() else None
            for path in (self.path, self.state_path, self.xml_path, self.arch_xml_path)
        }
        try:
            _atomic_write(
                self.state_path,
                json.dumps(
                    {"desktop_id": DESKTOP_ID, "before": before, "selected": dict(selected)},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            if selected["arch"]:
                _atomic_write(
                    self.arch_xml_path,
                    (Path(__file__).with_name("assets") / "multipak-packages.xml").read_text(),
                )
            else:
                self.arch_xml_path.unlink(missing_ok=True)
            if selected["appimage"]:
                _atomic_write(
                    self.xml_path,
                    (Path(__file__).with_name("assets") / "multipak-appimage.xml").read_text(),
                )
            else:
                self.xml_path.unlink(missing_ok=True)
                # A system image may provide the package type globally. Restore
                # ordinary executable handling for this user when they opt out.
                directories = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
                if any(
                    (Path(item) / "mime/application/x-multipak-appimage.xml").is_file()
                    for item in directories.split(os.pathsep)
                    if item
                ):
                    _atomic_write(
                        self.xml_path,
                        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">'
                        f'<mime-type type="{APPIMAGE_MIME}">'
                        '<sub-class-of type="application/x-executable"/>'
                        "</mime-type></mime-info>",
                    )
            _atomic_write(self.path, output.getvalue())
            self.refresher()
        except Exception:
            _restore(snapshots)
            try:
                self.refresher()
            except Exception:
                pass
            raise
        return dict(selected)

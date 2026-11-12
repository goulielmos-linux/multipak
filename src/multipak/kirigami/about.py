"""About-window data and asynchronous, read-only backend version queries."""

import configparser
import platform
import re
from pathlib import Path

import PySide6
from PySide6.QtCore import (
    Property,
    QFile,
    QIODevice,
    QLibraryInfo,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
    Slot,
    qVersion,
)
from PySide6.QtGui import QGuiApplication

from multipak import DISPLAY_VERSION, host
from multipak.localization import tr

AUTHORS = (("Goulielmos", "https://github.com/goulielmos-linux"),)

BACKEND_PROBES = {
    "Distrobox": ("distrobox", "--version"),
    "Podman": ("podman", "--version"),
}


def license_text():
    return (Path(__file__).parents[1] / "assets/LICENSE.txt").read_text(encoding="utf-8")


def kirigami_version(import_paths):
    for directory in import_paths:
        path = directory.rstrip("/") + "/org/kde/kirigami/kde-qmlmodule.version"
        resource = QFile(path.replace("qrc:/", ":/", 1))
        if resource.open(QIODevice.ReadOnly):
            value = bytes(resource.read(64)).decode("ascii", errors="replace").strip()
            resource.close()
            if re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
                return value

    # The KDE Flatpak runtime ships a versioned library even without the QML version file.
    libraries = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
    try:
        library = (libraries / "libKirigami.so.6").resolve(strict=True)
    except (OSError, RuntimeError):
        return ""
    version = re.fullmatch(r"libKirigami\.so\.(\d+(?:\.\d+){2,3})", library.name)
    return version[1] if version and library.is_file() else ""


class AboutInfo(QObject):
    changed = Signal()

    def __init__(self, import_paths, parent=None):
        super().__init__(parent)
        self._kirigami = kirigami_version(import_paths)
        self._backends = {
            name: {"path": "", "version": "", "state": "idle"} for name in BACKEND_PROBES
        }
        self._probes = {}
        self._requested = False

    @Property(str, constant=True)
    def version(self):
        return DISPLAY_VERSION

    @Property(str, constant=True)
    def licenseText(self):
        return license_text()

    @Property("QVariantList", notify=changed)
    def authors(self):
        return [{"name": name, "url": url} for name, url in AUTHORS]

    @Property("QVariantList", notify=changed)
    def libraries(self):
        return [
            {
                "name": name,
                "version": version or tr("Unavailable"),
                "license": license_label,
                "licenseUrl": license_url,
                "website": website,
                "description": description,
            }
            for name, version, license_label, license_url, website, description in (
                (
                    "Python",
                    platform.python_version(),
                    "PSF-2.0",
                    "https://docs.python.org/3/license.html",
                    "https://www.python.org/",
                    tr("Language used for MultiPak’s application logic."),
                ),
                (
                    "Qt",
                    qVersion(),
                    tr("LGPLv3 / GPLv3 / Commercial"),
                    "https://doc.qt.io/qt-6/licensing.html",
                    "https://www.qt.io/",
                    tr("Foundation of the graphical interface."),
                ),
                (
                    "PySide6",
                    PySide6.__version__,
                    tr("LGPLv3 / GPLv3 / Commercial"),
                    "https://doc.qt.io/qtforpython-6/commercial/index.html",
                    "https://doc.qt.io/qtforpython-6/",
                    tr("Integration between Python and Qt."),
                ),
                (
                    "Kirigami",
                    self._kirigami,
                    "LGPL-2.0-or-later",
                    "https://github.com/KDE/kirigami/tree/master/LICENSES",
                    "https://develop.kde.org/docs/getting-started/kirigami/",
                    tr("KDE interface components."),
                ),
            )
        ]

    @Property("QVariantList", notify=changed)
    def environment(self):
        platform_name = QGuiApplication.platformName()
        graphics = {"xcb": "X11"}.get(platform_name, platform_name)
        if platform_name.startswith("wayland"):
            graphics = "Wayland"
        flatpak = host.is_flatpak()
        name = tr("Native installation")
        if flatpak:
            name = tr("Flatpak runtime")
            info = configparser.ConfigParser(interpolation=None)
            try:
                with Path("/.flatpak-info").open(encoding="utf-8") as stream:
                    info.read_string(stream.read(65536))
                runtime = info.get("Application", "runtime", fallback="").removeprefix("runtime/")
                if runtime.startswith("org.kde.Platform/"):
                    name = "KDE Flatpak runtime"
            except (OSError, configparser.Error):
                pass
        return [
            {
                "name": f"{name} ({graphics})" if graphics else name,
                "description": tr("Execution environment and graphics system."),
            }
        ]

    @Property(bool, notify=changed)
    def flatpak(self):
        return host.is_flatpak()

    @Slot(str)
    def copyInformation(self, title):
        lines = [f"{title} — {self.version}", ""]
        lines.extend(row["name"] for row in self.environment)
        lines.extend(f"{row['name']} {row['version']}" for row in self.libraries)
        if self.flatpak:
            lines.append("Flatpak")
        lines.extend(["", tr("Backend")])
        lines.extend(f"{row['name']} {row['version']}\n{row['path']}" for row in self.backends)
        QGuiApplication.clipboard().setText("\n".join(lines))

    @Property("QVariantList", notify=changed)
    def backends(self):
        states = {
            "idle": tr("Not checked"),
            "loading": tr("Checking…"),
            "missing": tr("Not installed"),
            "failed": tr("Unavailable"),
        }
        return [
            {
                "id": name,
                "name": name,
                "version": data["version"] or states.get(data["state"], ""),
                "path": data["path"] or tr("Not found"),
            }
            for name, data in self._backends.items()
        ]

    @Property(bool, notify=changed)
    def loading(self):
        return bool(self._probes)

    @Slot()
    def refresh(self):
        if self._requested:
            return
        self._requested = True
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        for name, data in self._backends.items():
            command, flag = BACKEND_PROBES[name]
            executable = host.which(command)
            if not executable:
                data["state"] = "missing"
                continue
            data.update(path=executable, state="loading")
            process = QProcess(self)
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.MergedChannels)
            timer = QTimer(process)
            timer.setSingleShot(True)
            self._probes[name] = {"process": process, "timer": timer, "output": bytearray()}
            process.readyReadStandardOutput.connect(lambda name=name: self._read(name))
            process.finished.connect(
                lambda code, status, name=name: self._finish(
                    name, code == 0 and status == QProcess.NormalExit
                )
            )
            process.errorOccurred.connect(lambda _error, name=name: self._finish(name, False))
            timer.timeout.connect(lambda name=name: self._finish(name, False))
            timer.start(2000)
            invocation = host.command([executable, flag], environment={"LC_ALL": "C"})
            process.start(invocation[0], invocation[1:])
        self.changed.emit()

    def _read(self, name):
        probe = self._probes.get(name)
        if probe is None:
            return
        probe["output"].extend(bytes(probe["process"].readAllStandardOutput()))
        if len(probe["output"]) > 4096:
            self._finish(name, False)

    def _finish(self, name, success):
        probe = self._probes.pop(name, None)
        if probe is None:
            return
        probe["timer"].stop()
        process = probe["process"]
        probe["output"].extend(bytes(process.readAllStandardOutput()))
        if process.state() != QProcess.NotRunning:
            process.kill()
        output = bytes(probe["output"][:4096]).decode("utf-8", errors="replace")
        version_pattern = r"(\d+(?:\.\d+){1,3}(?:[-+~][\w.-]+)?)"
        pattern = r"(?im)^\s*" + name + r"(?:\s+version)?\s*:?\s*" + version_pattern
        match = re.search(pattern, output)
        data = self._backends[name]
        data["version"] = match[1] if success and match else ""
        data["state"] = "ready" if data["version"] else "failed"
        process.deleteLater()
        self.changed.emit()


def bind_about(engine, languages):
    info = AboutInfo(engine.importPathList(), engine)
    engine._about_info = info
    engine.rootContext().setContextProperty("aboutInfo", info)
    languages.languageChanged.connect(info.changed)
    return info

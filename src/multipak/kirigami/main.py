#!/usr/bin/env python3
"""MultiPak native installer; without a package, show visual examples only."""

import argparse
import base64
import os
import re
import sys
from html import escape
from pathlib import Path

from PySide6.QtCore import Property, QMetaObject, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QStyleFactory

from multipak import DISPLAY_VERSION
from multipak.catalog import package_environment  # noqa: E402
from multipak.localization import tr
from multipak.models import Environment, Package  # noqa: E402
from multipak.suggestions import suggestion_details  # noqa: E402

from .about import bind_about
from .configuration import Configuration
from .identity import configure_application
from .languages import bind_languages
from .legacy_migration import bind_legacy_migration
from .update_notice import bind_update_notice

# Preview fixtures only; the live installer obtains these versions from its catalog.
PREVIEW_ENVIRONMENTS = (
    Environment("fedora-45", "Fedora 45", "rpm", "quay.io/fedora/fedora-toolbox:45"),
    Environment("fedora-44", "Fedora 44", "rpm", "quay.io/fedora/fedora-toolbox:44"),
    Environment("fedora-43", "Fedora 43", "rpm", "quay.io/fedora/fedora-toolbox:43"),
    Environment("ubuntu-26.04", "Ubuntu 26.04", "deb", "docker.io/library/ubuntu:26.04"),
    Environment("ubuntu-24.04", "Ubuntu 24.04", "deb", "docker.io/library/ubuntu:24.04"),
    Environment("debian-13", "Debian 13", "deb", "docker.io/library/debian:13"),
    Environment("debian-12", "Debian 12", "deb", "docker.io/library/debian:12"),
    Environment(
        "arch-rolling", "Arch Linux (rolling release)", "arch", "docker.io/library/archlinux:latest"
    ),
)


class Welcome(QObject):
    dataChanged = Signal()
    stateChanged = Signal()
    closeRequested = Signal()
    notice = Signal(str)
    choiceMade = Signal()
    methodChosen = Signal()
    modeChosen = Signal()
    environmentChosen = Signal()
    installationRequested = Signal()
    distroboxRequired = Signal()

    def __init__(
        self,
        name="Strawberry",
        version="1.2.31-1.fc45",
        packaging="RPM",
        *,
        package=None,
        environment=None,
    ):
        super().__init__()
        if package is None:
            hint = re.search(r"\.fc(\d+)(?:\.|$)", version) if packaging == "RPM" else None
            package = Package(
                Path(f"{name}.{packaging.lower()}"),
                packaging.lower(),
                name,
                version,
                "x86_64",
                distro_hint=f"fedora-{hint[1]}" if hint else "",
                distro_hint_source="metadata" if hint else "",
            )
        self.package = package
        self.name = package.name
        self.version = package.version
        self.kind = package.kind.upper()
        self.environment = (
            environment
            or package_environment(package.distro_hint, list(PREVIEW_ENVIRONMENTS))
            or next((item for item in PREVIEW_ENVIRONMENTS if item.kind == package.kind), None)
        )
        self.strength, explanation = (
            ("", "")
            if self.kind == "APPIMAGE"
            else suggestion_details(package, self.environment, self.environment.title)
        )
        self.explanation = explanation
        self.is_live = False
        self.worker = None
        self.transaction = None
        self.choice = ""
        self.method = "appimage" if self.kind == "APPIMAGE" else "container"
        self.mode = "standard"
        self.existing = ()
        self.container_names = ()
        self._dependency_report = None
        self._appimage_container = False
        self._appimage_verify = True
        self._appimage_environment = ""
        self._appimage_container_exists = False
        self._configuration = Configuration(self)
        self._configuration.changed.connect(self._configuration_changed)
        self._selection = None
        if self.environment is not None:
            self.configure(PREVIEW_ENVIRONMENTS, self.existing, self.environment)

    def _configuration_changed(self):
        self._advanced_confirmed = False

    def configure(self, profiles, existing, suggested, container_names=()):
        self.profiles, self.existing = tuple(profiles), tuple(existing)
        self.container_names = tuple(container_names)
        self._configuration.reset(self.profiles, self.existing, suggested, self.container_names)
        self._selection = self._configuration.standard_selection()
        self.environment = suggested

    @Property(QObject, constant=True)
    def configuration(self):
        return self._configuration

    @Property(bool, notify=dataChanged)
    def environmentManuallySelected(self):
        return bool(self._selection and self._selection.manual)

    def installation_allowed(self):
        if self.isAppImage:
            return self.method == "appimage" and self.package is not None
        return (
            self.method == "container"
            and self.package is not None
            and (self.mode == "standard" or (self.mode == "advanced" and self._advanced_confirmed))
        )

    @Property(str, constant=True)
    def appVersion(self):
        return DISPLAY_VERSION

    @Property(bool, notify=dataChanged)
    def distroboxCanInstall(self):
        return getattr(self, "_distrobox_can_install", False)

    @Property(bool, notify=dataChanged)
    def distroboxRestartRequired(self):
        return getattr(self, "_distrobox_restart_required", False)

    @Property(str, notify=dataChanged)
    def distroboxMessage(self):
        return getattr(self, "_distrobox_message", "")

    @Property(bool, notify=dataChanged)
    def distroboxSetupActive(self):
        return getattr(self, "_mode", "") in ("distrobox", "reboot")

    @Property(str, notify=dataChanged)
    def applicationNameHtml(self):
        return escape(self.name)

    @Property(str, notify=dataChanged)
    def versionHtml(self):
        if self.isAppImage and self.version == "Not provided":
            return tr("Version not provided")
        version = re.search(r"\d+(?:\.\d+)*", self.version.rsplit(":", 1)[-1])
        return escape(version[0] if version else self.version)

    @Property(str, notify=dataChanged)
    def packaging(self):
        return {"ARCH": "Arch Linux", "APPIMAGE": "AppImage"}.get(self.kind, self.kind)

    @Property(bool, notify=dataChanged)
    def isAppImage(self):
        return self.kind == "APPIMAGE"

    def set_dependency_report(self, report):
        """Missing required components select the container option by default."""
        self._dependency_report = report
        self._appimage_container = bool(report is not None and report.required)
        self._appimage_verify = True
        self.dataChanged.emit()

    def appimage_options(self):
        report = self._dependency_report
        if not (self.isAppImage and self._appimage_container and report and report.required):
            return {}
        return {
            "container": True,
            # The container is a different system: request everything the AppImage uses;
            # DNF skips what is already installed there.
            "packages": report.container_packages,
            "verify": self._appimage_verify,
        }

    def set_appimage_environment(self, title, exists, name=""):
        self._appimage_environment = title
        self._appimage_container_exists = bool(exists)
        self._appimage_container_name = name
        self.dataChanged.emit()

    @Property(str, notify=dataChanged)
    def appImageEnvironmentTitle(self):
        return self._appimage_environment or "Fedora"

    @Property(str, notify=dataChanged)
    def appImageContainerName(self):
        from multipak.appimage_container import CONTAINER_NAME

        return getattr(self, "_appimage_container_name", "") or CONTAINER_NAME

    @Property(bool, notify=dataChanged)
    def appImageContainerExists(self):
        return self._appimage_container_exists

    @Property(str, notify=dataChanged)
    def appImageRequirementsText(self):
        report = self._dependency_report
        return report.requirements_text() if report is not None else ""

    @Property(str, notify=dataChanged)
    def appImageEnvironmentExplanation(self):
        return tr(
            "Fedora can locate the correct package from the name of each missing library, "
            "which makes it possible to install the dependencies automatically. All AppImages "
            "installed in a container will share this same environment."
        )

    @Property(bool, notify=dataChanged)
    def appImageNeedsDependencies(self):
        report = self._dependency_report
        return bool(self.isAppImage and report is not None and report.required)

    @Property(str, notify=dataChanged)
    def appImageDependencyText(self):
        report = self._dependency_report
        return report.text() if report is not None else ""

    @Property(bool, notify=dataChanged)
    def appImageContainerSelected(self):
        return self.appImageNeedsDependencies and self._appimage_container

    @Property(bool, notify=dataChanged)
    def appImageVerify(self):
        return self._appimage_verify

    @Slot(bool)
    def chooseAppImageContainer(self, selected):
        # The verification choice is kept when the user switches back and forth.
        if self.appImageNeedsDependencies and selected != self._appimage_container:
            self._appimage_container = bool(selected)
            self.dataChanged.emit()

    @Slot(bool)
    def setAppImageVerify(self, value):
        if value != self._appimage_verify:
            self._appimage_verify = bool(value)
            self.dataChanged.emit()

    @Property(QUrl, notify=dataChanged)
    def applicationIcon(self):
        if self.package is not None and self.package.icon_data:
            media = "image/svg+xml" if self.package.icon_extension == ".svg" else "image/png"
            encoded = base64.b64encode(self.package.icon_data).decode("ascii")
            return QUrl(f"data:{media};base64,{encoded}")
        return QUrl.fromLocalFile(str(Path(__file__).with_name("assets") / "application.svg"))

    @Property(str, notify=dataChanged)
    def packageManager(self):
        return {"RPM": "DNF", "DEB": "APT", "ARCH": "Pacman"}.get(self.kind, "")

    @Property(bool, constant=True)
    def live(self):
        return self.is_live

    @Property(str, notify=stateChanged)
    def state(self):
        return getattr(self, "_state", "preview")

    @Property(bool, notify=stateChanged)
    def canClose(self):
        return not self.is_live or (self.worker is None and self.transaction is None)

    @Property(str, notify=dataChanged)
    def resultTitle(self):
        return getattr(self, "_result_title", tr("Installation completed"))

    @Property(str, notify=dataChanged)
    def resultMessage(self):
        return getattr(
            self,
            "_result_message",
            tr("The AppImage was installed and added to the application launcher.")
            if self.isAppImage
            else tr("The application was installed in the environment ")
            + self.environment.title
            + ".",
        )

    @Property(str, notify=dataChanged)
    def resultNote(self):
        return getattr(self, "_result_note", "")

    @Property(bool, notify=dataChanged)
    def systemInstallAvailable(self):
        return self.kind == "RPM"

    @Slot()
    def requestClose(self):
        self.closeRequested.emit()

    @Property(str, notify=choiceMade)
    def lastChoice(self):
        return self.choice

    @Slot(str)
    def choose(self, choice):
        if choice in ("search", "continue"):
            self.choice = choice
            self.choiceMade.emit()

    @Property(str, notify=methodChosen)
    def selectedMethod(self):
        return self.method

    @Slot(str)
    def chooseMethod(self, method):
        if self.isAppImage:
            return
        if method not in ("container", "system"):
            return
        if method == "system" and (self.kind != "RPM" or self.mode != "advanced"):
            return
        self.method = method
        self.methodChosen.emit()

    @Property(str, notify=modeChosen)
    def selectedMode(self):
        return self.mode

    @Slot(str)
    def chooseMode(self, mode):
        if self.method != "container" or mode not in ("standard", "advanced"):
            return
        self.chooseConfiguration(mode)

    @Slot(str)
    def chooseConfiguration(self, mode):
        if self.isAppImage:
            return
        if mode not in ("standard", "advanced"):
            return
        self.method, self.mode = "container", mode
        self._advanced_confirmed = False
        if mode == "standard":
            self._selection = self._configuration.standard_selection()
            self.environment = self._selection.environment
        self.dataChanged.emit()
        self.methodChosen.emit()
        self.modeChosen.emit()

    @Slot()
    def confirmConfiguration(self):
        if self.method != "container" or self.mode != "advanced" or not self._configuration.valid:
            return
        self._selection = self._configuration.selection()
        self.environment = self._selection.environment
        self._advanced_confirmed = True
        self.dataChanged.emit()
        self.environmentChosen.emit()

    @Property(str, notify=dataChanged)
    def environmentTitle(self):
        if self.environment is None:
            return ""
        if self._selection.existing:
            existing = self._selection.existing
            return f"{existing.family.title()} {existing.version}"
        return tr(self.environment.title)

    @Property(str, notify=dataChanged)
    def containerName(self):
        return (
            self._selection.container_name(self.existing, self.container_names)
            if self._selection
            else ""
        )

    @Property(bool, notify=dataChanged)
    def containerExists(self):
        return bool(self._selection and self._selection.existing is not None)

    @Property(str, notify=dataChanged)
    def confidence(self):
        return self.strength

    @Property(str, notify=dataChanged)
    def confidenceExplanation(self):
        return self.explanation

    @Property(str, notify=dataChanged)
    def dependencies(self):
        return self.package.dependencies if self.package is not None else ""

    @Property(bool, notify=dataChanged)
    def hasPackage(self):
        return self.package is not None

    @Slot()
    def confirmEnvironment(self):
        if self.method == "container" and self.mode == "standard":
            self.environmentChosen.emit()

    @Slot()
    def requestInstallation(self):
        if self.installation_allowed():
            self.installationRequested.emit()

    @Slot(bool)
    def requestSystemInstallation(self, accepted):
        if (
            accepted
            and self.method == "system"
            and self.mode == "advanced"
            and self.systemInstallAvailable
        ):
            self.installationRequested.emit()


def create_preview(
    app,
    name="Strawberry",
    version="1.2.31-1.fc45",
    packaging="RPM",
    *,
    package=None,
    environment=None,
):
    bridge = Welcome(name, version, packaging, package=package, environment=environment)
    return create_window(app, bridge)


def create_window(app, bridge):
    configure_application(app)
    engine = QQmlApplicationEngine(app)
    languages = bind_languages(app, engine, bridge)
    bind_about(engine, languages)
    migration = bind_legacy_migration(engine, bridge, preview=not bridge.live)
    bind_update_notice(engine, bridge, preview=not bridge.live, migration=migration)
    engine.warnings.connect(
        lambda warnings: print(
            "\n".join(warning.toString() for warning in warnings), file=sys.stderr
        )
    )
    bridge.setParent(engine)
    engine.rootContext().setContextProperty("welcome", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Welcome.qml"))))
    if not engine.rootObjects():
        raise RuntimeError(
            tr(
                "Could not load Kirigami. Use the compatible PySide6 and Kirigami "
                "supplied by the system."
            )
        )
    window = engine.rootObjects()[0]
    if not isinstance(window, QQuickWindow):
        raise RuntimeError(tr("The welcome component did not create a Qt Quick window."))
    window.setIcon(app.windowIcon())
    return engine, bridge, window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument("--name", default="Strawberry")
    parser.add_argument("--version", default=None)
    parser.add_argument("--packaging", choices=("RPM", "DEB", "ARCH", "APPIMAGE"), default="RPM")
    parser.add_argument(
        "--missing-dependencies",
        action="store_true",
        help="AppImage preview: show the screen for missing dependencies",
    )
    parser.add_argument(
        "--page",
        choices=(
            "welcome",
            "method",
            "mode",
            "advanced",
            "environment",
            "review",
            "container",
            "completion",
        ),
        default="welcome",
    )
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = QApplication(["io.github.multipak.installer"])
    if "breeze" in (key.lower() for key in QStyleFactory.keys()):
        app.setStyle("Breeze")
    version = args.version or ("1.2.31-1.fc45" if args.packaging == "RPM" else "1.2.31")
    if args.package:
        if args.page != "welcome" or args.screenshot:
            parser.error(
                tr(
                    "Open the package without --page or --screenshot; these options are for "
                    "previews only."
                )
            )
        from .live import LiveInstaller

        bridge = LiveInstaller(args.package)
        engine, bridge, window = create_window(app, bridge)
        bridge._legacy_migration.when_ready(bridge.inspect)
    else:
        engine, bridge, window = create_preview(app, args.name, version, args.packaging)
        if args.missing_dependencies and args.packaging == "APPIMAGE":
            from multipak.appimage_dependencies import Dependency, DependencyReport

            bridge.set_dependency_report(
                DependencyReport(
                    (
                        Dependency("python", "_tkinter", "python3-tkinter"),
                        Dependency("python", "yaml", "python3-pyyaml", optional=True),
                    ),
                    incomplete=True,
                    requirements=(
                        Dependency("interpreter", "python3", "python3"),
                        Dependency("library", "libX11.so.6", "libX11.so.6()(64bit)"),
                        Dependency("python", "_tkinter", "python3-tkinter"),
                        Dependency("python", "yaml", "python3-pyyaml", optional=True),
                    ),
                )
            )
            bridge.set_appimage_environment("Fedora 45", False)
    window.requestActivate()

    if args.page == "method":
        QTimer.singleShot(0, lambda: QMetaObject.invokeMethod(window, "openMethod"))
    elif args.page == "mode":
        QTimer.singleShot(0, lambda: QMetaObject.invokeMethod(window, "openModes"))
    elif args.page == "advanced":
        QTimer.singleShot(0, lambda: bridge.chooseConfiguration("advanced"))
    elif args.page == "environment":
        QTimer.singleShot(0, lambda: QMetaObject.invokeMethod(window, "openEnvironment"))
    elif args.page == "review":
        QTimer.singleShot(0, lambda: QMetaObject.invokeMethod(window, "openReview"))
    elif args.page == "container":
        # AppImage preview with --missing-dependencies: the container review page.

        def open_container():
            QMetaObject.invokeMethod(window, "openMethod")
            QMetaObject.invokeMethod(window, "openAppImageContainer")

        QTimer.singleShot(0, open_container)
    elif args.page == "completion":
        print(tr("Visual completion preview; no application was installed."))
        QTimer.singleShot(0, lambda: QMetaObject.invokeMethod(window, "previewCompletion"))

    if args.screenshot:

        def capture():
            if window.grabWindow().save(str(args.screenshot)):
                print(tr("Preview: {0} × {1} px").format(window.width(), window.height()))
                app.exit(0)
            else:
                print(tr("Could not save the image."), file=sys.stderr)
                app.exit(1)

        QTimer.singleShot(1000, capture)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

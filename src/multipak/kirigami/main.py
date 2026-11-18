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

from multipak import DISPLAY_VERSION, alpine, gentoo
from multipak.android_instances import valid_label
from multipak.android_translation import preview_plan
from multipak.catalog import package_environment  # noqa: E402
from multipak.localization import tr
from multipak.metadata import rpm_release_hints  # noqa: E402
from multipak.models import (  # noqa: E402
    ALPINE_ENVIRONMENTS,
    GENTOO_ENVIRONMENT,
    PISI_ENVIRONMENT,
    RPM_FAMILIES,
    RPM_FAMILY_ENVIRONMENTS,
    SLACKWARE_ENVIRONMENTS,
    SLITAZ_ENVIRONMENT,
    SLITAZ_ENVIRONMENTS,
    SOLUS_ENVIRONMENT,
    VOID_ENVIRONMENT,
    Environment,
    Package,
    package_profiles,
    rpm_environment,
)
from multipak.suggestions import android_suggestion_details, suggestion_details  # noqa: E402

from .about import bind_about
from .configuration import Configuration
from .identity import configure_application
from .languages import bind_languages
from .legacy_migration import bind_legacy_migration
from .shutdown import release_engine
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
    *RPM_FAMILY_ENVIRONMENTS,
    *SLACKWARE_ENVIRONMENTS,
    PISI_ENVIRONMENT,
    *SLITAZ_ENVIRONMENTS,
    GENTOO_ENVIRONMENT,
    *ALPINE_ENVIRONMENTS,
    VOID_ENVIRONMENT,
    SOLUS_ENVIRONMENT,
)


def _message_html(text):
    """Text for Kirigami's InlineMessage, which reads markup on its own: escaped, so
    that names and paths taken from a package are shown exactly as they are."""
    if not text:
        return ""
    return "<p>" + escape(text).replace("\n", "<br>") + "</p>"


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
            # Fedora 1.fc45, Mageia 1.mga10, openSUSE Leap bp161, ALT alt1.
            hints = rpm_release_hints(version.partition("-")[2]) if packaging == "RPM" else []
            hint = hints[0] if hints else ""
            package = Package(
                Path(f"{name}.{packaging.lower()}"),
                packaging.lower(),
                name,
                version,
                "x86_64",
                distro_hint=hint
                if hint
                else {
                    "SLACKWARE": "slackware-15.0",
                    "PISI": PISI_ENVIRONMENT.id,
                    "SLITAZ": SLITAZ_ENVIRONMENT.id,
                    "GENTOO": GENTOO_ENVIRONMENT.id,
                    "PUPPY": "debian-12",
                    # As estimated from abuild 3.13: a release past its support.
                    "APK": "alpine-3.20",
                    "XBPS": VOID_ENVIRONMENT.id,
                    "EOPKG": SOLUS_ENVIRONMENT.id,
                }.get(packaging, ""),
                distro_hint_source=(
                    "metadata" if hint or packaging in ("PUPPY", "APK", "XBPS", "EOPKG") else ""
                ),
            )
        self.package = package
        self.name = package.name
        self.version = package.version
        self.kind = package.kind.upper()
        self.environment = (
            environment
            or package_environment(package.distro_hint, list(PREVIEW_ENVIRONMENTS))
            or next(iter(package_profiles(package, PREVIEW_ENVIRONMENTS)), None)
        )
        self.strength, explanation = (
            ("", "")
            if self.kind in ("APPIMAGE", "SNAP", "ANDROID")
            else suggestion_details(package, self.environment, self.environment.title)
        )
        self.explanation = explanation
        self.is_live = False
        self.worker = None
        self.transaction = None
        self.android_plan = preview_plan(package) if self.kind == "ANDROID" else None
        self._android_environments = ()
        self._android_profile = "android-13"
        self._android_environment = ""
        self._android_label = "MultiPak-Android-13"
        self._android_use_suggested = True
        self._android_container_mode = "standard"
        self.choice = ""
        self.method = {"APPIMAGE": "appimage", "SNAP": "snap", "ANDROID": "waydroid"}.get(
            self.kind, "container"
        )
        self.mode = "standard"
        self.existing = ()
        self.container_names = ()
        self._dependency_report = None
        self._appimage_container = False
        self._appimage_verify = True
        self._appimage_environment = ""
        self._appimage_container_exists = False
        self._snap_refusal = ""
        self._snap_missing = ()
        # Optional components chosen on the review page.
        self._snap_components = set()
        self._snap_services = ()
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
        if self.isAndroid:
            return (
                self.method == "waydroid"
                and self.package is not None
                and self.androidSelectionValid
                and self.androidTranslationState != "unsupported"
            )
        if self.isSnap:
            return self.method == "snap" and self.package is not None and not self._snap_refusal
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

    @Property(bool, notify=dataChanged)
    def distroboxUpdateRequired(self):
        return getattr(self, "_distrobox_update_required", False)

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
        if self.isAndroidBundle:
            extension = self.package.path.suffix.removeprefix(".").upper()
            return "Android " + (
                extension
                if extension in ("APKM", "APKS", "XAPK")
                else self.package.android_bundle_format or "APKM"
            )
        if self.kind == "SNAP" and self.snapClassic:
            return tr("Snap (classic)")
        return {
            "ARCH": "Arch Linux",
            "APPIMAGE": "AppImage",
            "SNAP": "Snap",
            "SLACKWARE": "Slackware",
            "PISI": "Pisi Linux",
            "SLITAZ": "SliTaz",
            "GENTOO": "Gentoo",
            "PUPPY": "Puppy Linux",
            "APK": "Alpine Linux",
            "XBPS": "Void Linux",
            "EOPKG": "Solus",
            "ANDROID": "Android APK",
        }.get(self.kind, self.kind)

    @Property(bool, notify=dataChanged)
    def isAppImage(self):
        return self.kind == "APPIMAGE"

    @Property(bool, notify=dataChanged)
    def isAndroid(self):
        return self.kind == "ANDROID"

    @Property(int, notify=dataChanged)
    def packagePartCount(self):
        return len(self.package.android_parts) if self.package else 0

    @Property(bool, notify=dataChanged)
    def isAndroidBundle(self):
        return self.isAndroid and self.package is not None and bool(self.package.android_parts)

    @Property("QVariantList", constant=True)
    def androidProfiles(self):
        from multipak.android_instances import PROFILES

        return [{"id": p[0], "label": p[1], "sdk": p[3]} for p in PROFILES]

    @Property("QVariantList", notify=dataChanged)
    def androidEnvironments(self):
        return [{"id": "", "label": tr("Create a new Android environment")}] + [
            {"id": e["id"], "label": e["label"]}
            for e in self._android_environments
            if (e.get("personal") and self.mode == "advanced")
            or (not e.get("personal") and e["profile"] == self._android_profile)
        ]

    @Property(str, notify=dataChanged)
    def androidSuggestedVersion(self):
        return max(self.androidProfiles, key=lambda p: p["sdk"])["label"]

    @Property(str, notify=dataChanged)
    def androidSelectedVersion(self):
        if self.androidUsesPersonalEnvironment:
            return self.androidExistingVersion
        return next(
            (p["label"] for p in self.androidProfiles if p["id"] == self._android_profile), ""
        )

    @Property(bool, notify=dataChanged)
    def androidUseSuggested(self):
        return self._android_use_suggested

    @Property(str, notify=dataChanged)
    def androidContainerMode(self):
        return self._android_container_mode

    @Property("QVariantList", notify=dataChanged)
    def androidExistingEnvironments(self):
        return self.androidEnvironments[1:]

    @Property(str, notify=dataChanged)
    def androidContainerDescription(self):
        if not self.androidEnvironmentName.strip():
            return ""
        if self.androidEnvironmentExists:
            return tr("Container {0} will be reused for this installation.").format(
                self.androidEnvironmentName
            )
        return tr("Container {0} will be created for this installation.").format(
            self.androidEnvironmentName
        )

    @Property(str, notify=dataChanged)
    def androidConfidence(self):
        return self._android_suggestion()[0]

    def _android_suggestion(self):
        profile = max(self.androidProfiles, key=lambda p: p["sdk"])
        return android_suggestion_details(self.package, profile["label"], profile["sdk"])

    @Property(str, notify=dataChanged)
    def androidConfidenceExplanation(self):
        return self._android_suggestion()[1]

    @Slot(bool)
    def chooseAndroidSuggested(self, suggested):
        if self.mode != "advanced":
            return
        self._android_use_suggested = suggested
        if suggested:
            self.chooseAndroidProfile(max(self.androidProfiles, key=lambda p: p["sdk"])["id"])
        self.dataChanged.emit()

    @Slot(str)
    def chooseAndroidContainerMode(self, mode):
        if self.mode != "advanced" or mode not in ("standard", "custom", "existing"):
            return
        custom_label = self._android_label if self._android_container_mode == "custom" else ""
        self._android_container_mode = mode
        self._android_environment = ""
        version = next(p["label"] for p in self.androidProfiles if p["id"] == self._android_profile)
        self._android_label = "MultiPak-" + version.replace(" ", "-")
        if mode == "standard":
            self._android_environment = next(
                (
                    e["id"]
                    for e in self._android_environments
                    if not e.get("personal")
                    and e["profile"] == self._android_profile
                    and e["label"] == self._android_label
                ),
                "",
            )
        elif mode == "custom":
            self._android_label = custom_label
        elif self.androidExistingEnvironments:
            self._android_environment = self.androidExistingEnvironments[0]["id"]
        self.dataChanged.emit()

    @Property(str, notify=dataChanged)
    def androidProfile(self):
        return self._android_profile

    @Property(str, notify=dataChanged)
    def androidEnvironment(self):
        return self._android_environment

    @Property(str, notify=dataChanged)
    def androidEnvironmentName(self):
        return next(
            (
                e["label"]
                for e in self._android_environments
                if e["id"] == self._android_environment
            ),
            self._android_label,
        )

    @Property(str, notify=dataChanged)
    def androidNewEnvironmentName(self):
        return self._android_label

    @Property(bool, notify=dataChanged)
    def androidUsesPersonalEnvironment(self):
        return self._android_environment == "system-waydroid"

    @Property(str, notify=dataChanged)
    def androidExistingVersion(self):
        return next(
            (
                e.get("version", "")
                for e in self._android_environments
                if e["id"] == self._android_environment
            ),
            "",
        )

    def android_target_sdk(self):
        if self.androidUsesPersonalEnvironment:
            return next(
                (
                    e.get("sdk", 0)
                    for e in self._android_environments
                    if e["id"] == self._android_environment
                ),
                0,
            )
        return next((p["sdk"] for p in self.androidProfiles if p["id"] == self._android_profile), 0)

    @Property(bool, notify=dataChanged)
    def androidSelectionValid(self):
        sdk = self.android_target_sdk()
        return bool(
            self.package
            and (not sdk or self.package.android_min_sdk <= sdk)
            and valid_label(self.androidEnvironmentName)
            and (not self.androidUsesPersonalEnvironment or self.mode == "advanced")
            and (self._android_container_mode != "existing" or bool(self._android_environment))
        )

    def reset_android_default(self):
        self._android_use_suggested = True
        self._android_container_mode = "standard"
        profile = max(self.androidProfiles, key=lambda p: p["sdk"])
        self._android_profile = profile["id"]
        self._android_label = "MultiPak-" + profile["label"].replace(" ", "-")
        self._android_environment = next(
            (
                e["id"]
                for e in self._android_environments
                if not e.get("personal")
                and e["profile"] == profile["id"]
                and e["label"] == self._android_label
            ),
            "",
        )

    @Slot()
    def confirmAndroidConfiguration(self):
        if self.isAndroid and self.mode == "advanced" and self.androidSelectionValid:
            self.environmentChosen.emit()

    @Slot(str)
    def chooseAndroidProfile(self, identifier):
        if self.mode != "advanced":
            return
        profile = next((p for p in self.androidProfiles if p["id"] == identifier), None)
        if not profile:
            return
        self._android_profile = identifier
        self.chooseAndroidContainerMode(self._android_container_mode)
        self.android_plan = preview_plan(self.package)
        self.dataChanged.emit()

    @Slot(str)
    def chooseAndroidEnvironment(self, identifier):
        if identifier not in {e["id"] for e in self.androidEnvironments}:
            return
        if identifier == "system-waydroid" and self.mode != "advanced":
            return
        self._android_environment = identifier
        self.android_plan = preview_plan(self.package)
        self.dataChanged.emit()

    @Slot(str)
    def setAndroidEnvironmentName(self, name):
        if self.mode != "advanced":
            return
        self._android_label = name
        self.dataChanged.emit()

    @Property(bool, notify=dataChanged)
    def androidEnvironmentExists(self):
        return bool(self._android_environment)

    @Property(str, notify=dataChanged)
    def androidTranslationState(self):
        state = self.android_plan.translation if self.android_plan else "native"
        if self.android_target_sdk() not in (0, 33) and state in ("required", "check"):
            return "unsupported"
        return state

    @Property(str, notify=dataChanged)
    def androidTranslationExplanation(self):
        if (
            self.android_target_sdk() not in (0, 33)
            and self.androidTranslationState == "unsupported"
        ):
            return tr("Automatic ARM translation setup currently supports Android 13 on x86_64.")
        return self.android_plan.explanation if self.android_plan else ""

    @Property(str, notify=dataChanged)
    def androidTranslationDetails(self):
        if self.androidTranslationState == "unsupported":
            return self.androidTranslationExplanation
        if self.androidTranslationState == "available":
            return tr(
                "The ARM translation layer required by this application is already available "
                "in the Android container."
            )
        return tr(
            "The ARM translation layer lets this application run on this computer. "
            "MultiPak prepares this dependency automatically as part of the installation. "
            "An existing compatible layer is reused. Installing the layer restarts "
            "the selected Android environment."
        )

    @Property(bool, notify=dataChanged)
    def isSnap(self):
        return self.kind == "SNAP"

    @Property("QVariantList", notify=dataChanged)
    def snapChannels(self):
        """The Snap Store channels to choose from, as a store's app page offers them."""
        return [
            {"name": name, "label": f"{name} · {version}" if version else name}
            for name, version in getattr(self.package, "snap_channels", ())
        ]

    @Property(int, notify=dataChanged)
    def snapChannelIndex(self):
        current = getattr(self.package, "snap_channel", "")
        names = [item["name"] for item in self.snapChannels]
        return names.index(current) if current in names else 0

    @Slot(str)
    def chooseSnapChannel(self, channel):
        if any(item["name"] == channel for item in self.snapChannels):
            self._choose_channel(channel)

    def _choose_channel(self, channel):
        """Only the live installer can download again."""

    def set_snap_details(self, refusal, missing, services=()):
        self._snap_refusal, self._snap_missing = refusal, tuple(missing)
        self._snap_services = tuple(services)
        self._snap_components = set()
        self.dataChanged.emit()

    def _installable_components(self):
        from multipak.snap_components import installable

        info = getattr(self.package, "snap", None)
        if info is None:
            return []
        return installable(info, getattr(self.package, "snap_resources", ()))

    @Property("QVariantList", notify=dataChanged)
    def snapComponents(self):
        """Each optional component: its name, summary, and whether it can be chosen."""
        info = getattr(self.package, "snap", None)
        if info is None:
            return []
        available = self._installable_components()
        return [
            {
                "name": item.name,
                "summary": item.summary,
                "available": item.name in available,
                "selected": item.name in self._snap_components,
            }
            for item in info.component_details
        ]

    @Slot(str, bool)
    def setSnapComponent(self, name, selected):
        if name in self._installable_components():
            (self._snap_components.add if selected else self._snap_components.discard)(name)
            self.dataChanged.emit()

    def snap_component_choice(self):
        return sorted(self._snap_components & set(self._installable_components()))

    @Property(str, notify=dataChanged)
    def snapRefusal(self):
        return _message_html(self._snap_refusal)

    @Property(str, notify=dataChanged)
    def snapNetworkWarning(self):
        """One sentence for each port other computers on the network can connect to."""
        from multipak.snap_meta import network_ports

        info = getattr(self.package, "snap", None)
        return _message_html("\n".join(network_ports(info))) if info else ""

    @Property(bool, notify=dataChanged)
    def snapClassic(self):
        info = getattr(self.package, "snap", None)
        return bool(info and info.classic)

    @Property(str, notify=dataChanged)
    def snapBaseTitle(self):
        from multipak.snap_meta import base_title

        info = getattr(self.package, "snap", None)
        return base_title(info.base) if info is not None else ""

    @Property(str, notify=dataChanged)
    def snapEnvironmentExplanation(self):
        info = getattr(self.package, "snap", None)
        if info is None:
            return ""
        from multipak.snap_meta import base_title

        return tr("The package's metadata indicates that it was built for {0} ({1}).").format(
            base_title(info.base), info.base
        )

    @Property(str, notify=dataChanged)
    def snapRequirementsText(self):
        """Plain text: the content snaps, and which are still to download.

        The base is left out: it is the environment itself, named in the title.
        """
        info = getattr(self.package, "snap", None)
        if info is None:
            return ""
        from multipak.snap import runtime_names

        lines = []
        for name in runtime_names(info):
            if name == info.base:
                continue
            label = name
            if name not in self._snap_missing:
                label += " " + tr("(already available)")
            lines.append("• " + label)
        for content in info.unprovided:
            lines.append("• " + content + " " + tr("(could not be found automatically)"))
        return "\n".join(lines)

    @Property("QVariantList", notify=dataChanged)
    def snapAlerts(self):
        """What MultiPak cannot provide for this snap, grouped: title, text, items, note."""
        info = getattr(self.package, "snap", None)
        if info is None:
            return []
        from multipak.snap_meta import limitation_sections

        alerts = limitation_sections(info, self.snap_component_choice(), self._snap_services)
        channel = getattr(self.package, "snap_channel", "")
        if channel and channel != "latest/stable":
            # Asked for by the link from the Snap Store website.
            stable = channel.endswith("/stable")
            alerts.insert(
                0,
                {
                    "title": tr("Channel") if stable else tr("Unstable channel"),
                    "text": tr("From the {0} channel.").format(channel)
                    if stable
                    else tr("{0} can have problems and change often.").format(channel),
                    "items": [],
                    "note": "",
                },
            )
        return alerts

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

    @Property(int, constant=True)
    def appImageCheckSeconds(self):
        """How long the isolated check runs the AppImage."""
        from multipak.appimage_container import CHECK_SECONDS

        return CHECK_SECONDS

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
        if self.kind == "PUPPY":
            # The PET is converted to a package of the chosen environment's distribution.
            kind = self.environment.kind if self.environment is not None else "deb"
            return "slackpkg" if kind == "slackware" else "APT"
        if self.kind == "RPM" and self.environment is not None:
            # Mageia uses DNF like Fedora; openSUSE and ALT Linux have their own.
            family = self.environment.id.partition("-")[0]
            manager = {"opensuse": "zypper", "alt": "APT-RPM"}.get(family)
            if manager:
                return manager
        return {
            "RPM": "DNF",
            "DEB": "APT",
            "ARCH": "Pacman",
            "SLACKWARE": "slackpkg",
            "PISI": "pisi",
            "SLITAZ": "tazpkg",
            "GENTOO": "Portage",
            "APK": "apk",
            "XBPS": "XBPS",
            "EOPKG": "eopkg",
        }.get(self.kind, "")

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
            tr("The Android application was installed and added to the application launcher.")
            if self.isAndroid
            else tr("The snap was installed and added to the application launcher.")
            if self.isSnap
            else tr("The AppImage was installed and added to the application launcher.")
            if self.isAppImage
            else tr("The application was installed in the environment ")
            + self.environment.title
            + ".",
        )

    @Property(str, notify=dataChanged)
    def resultNote(self):
        return getattr(self, "_result_note", "")

    @Property(bool, notify=dataChanged)
    def resultWarning(self):
        return getattr(self, "_result_warning", False)

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
        if self.isAppImage or self.isSnap or self.isAndroid:
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
        if self.isAppImage or self.isSnap:
            return
        if mode not in ("standard", "advanced"):
            return
        if self.isAndroid:
            self.method, self.mode = "waydroid", mode
            if mode == "standard":
                self.reset_android_default()
            self.android_plan = preview_plan(self.package)
            self.dataChanged.emit()
            self.modeChosen.emit()
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
            if existing.family in ("slackware", "slitaz"):
                # Slackware: light and full share a release; the matching title tells
                # them apart. SliTaz: the title names the architecture.
                return tr(self.environment.title)
            if existing.family == "pisilinux":
                return "Pisi Linux"
            if existing.family == "gentoo":
                return "Gentoo Linux"
            if existing.family == "void":
                # The musl and 32-bit environments are named by their titles.
                if existing.version in ("", "rolling"):
                    return "Void Linux"
                return tr(self.environment.title)
            if existing.family == "solus":
                return "Solus"
            if existing.family == "alpine":
                return f"Alpine Linux {existing.version}"
            if existing.family in RPM_FAMILIES:
                try:
                    return tr(rpm_environment(existing.family, existing.version).title)
                except ValueError:
                    pass
            return f"{existing.family.title()} {existing.version}"
        if self.environment.id == "arch-rolling":
            return "Arch Linux"
        if self.environment.id == PISI_ENVIRONMENT.id:
            return "Pisi Linux"
        if self.environment.id == GENTOO_ENVIRONMENT.id:
            return "Gentoo Linux"
        if self.environment.id == VOID_ENVIRONMENT.id:
            return "Void Linux"
        if self.environment.id == SOLUS_ENVIRONMENT.id:
            return "Solus"
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
    def containerDescription(self):
        name = self.containerName
        if not name:
            return ""
        if name == tr("The name will be assigned during setup"):
            return tr("A new container will be created and named during setup.")
        if self.containerExists:
            return tr("Container {0} will be reused for this installation.").format(name)
        return tr("Container {0} will be created for this installation.").format(name)

    @Property(str, notify=dataChanged)
    def confidence(self):
        return self.strength

    @Property(str, notify=dataChanged)
    def confidenceExplanation(self):
        return self.explanation

    @Property(str, notify=dataChanged)
    def dependencies(self):
        if self.package is None:
            return ""
        dependencies = self.package.dependencies
        if self.package.kind == "deb":
            separator = f" {tr('or')} "
            requirements = (
                separator.join(alternative.strip() for alternative in requirement.split("|"))
                for requirement in dependencies.split(",")
            )
        elif self.package.kind == "slackware":
            requirements = (item.strip() for item in dependencies.split(","))
        elif self.package.kind in ("pisi", "eopkg"):
            separator = f" {tr('or')} "
            requirements = (separator.join(line.split(" | ")) for line in dependencies.splitlines())
        elif self.package.kind == "gentoo":
            requirements = gentoo.dependency_lines(dependencies, f" {tr('or')} ")
        elif self.package.kind == "puppy":
            requirements = (line.strip().lstrip("+") for line in dependencies.splitlines())
        elif self.package.kind == "apk":
            requirements = alpine.dependency_lines(dependencies)
        elif self.package.kind in ("rpm", "arch", "slitaz", "xbps"):
            requirements = (line.strip() for line in dependencies.splitlines())
        else:
            return dependencies
        return "\n".join(f"• {requirement}" for requirement in requirements if requirement)

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
    # A file, or a Snap Store name or link (snap:NAME, https://snapcraft.io/NAME).
    parser.add_argument("package", nargs="?")
    parser.add_argument("--name", default="Strawberry")
    parser.add_argument("--version", default=None)
    parser.add_argument(
        "--packaging",
        choices=(
            "RPM",
            "DEB",
            "ARCH",
            "APPIMAGE",
            "SLACKWARE",
            "PISI",
            "SLITAZ",
            "GENTOO",
            "APK",
            "XBPS",
            "EOPKG",
        ),
        default="RPM",
    )
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

    try:
        return app.exec()
    finally:
        release_engine(app, engine, window)


if __name__ == "__main__":
    raise SystemExit(main())

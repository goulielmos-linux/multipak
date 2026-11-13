"""Serializable installation and environment models."""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from multipak.localization import mark

PackageKind = Literal["rpm", "deb", "arch", "appimage"]


def valid_environment_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", name))


def reserved_environment_name(name: str) -> bool:
    return bool(re.match(r"^(?:C[0-9]+-)?MultiPak", name, re.I))


@dataclass(frozen=True)
class Package:
    path: Path
    kind: PackageKind
    name: str
    version: str
    architecture: str
    dependencies: str = ""
    distro_hint: str = ""
    distro_hint_source: str = ""
    filename_distro_hint: str = ""
    desktop_entry: str = ""
    icon_data: bytes = b""
    icon_extension: str = ""
    appimage_type: int = 0


@dataclass(frozen=True)
class Environment:
    id: str
    title: str
    kind: PackageKind
    image: str
    resolved_version: str = ""


@dataclass(frozen=True)
class ExistingEnvironment:
    name: str
    container_id: str
    family: str
    version: str
    image_id: str = ""
    image_names: tuple[str, ...] = ()
    managed_home: bool = False
    compatible_profiles: tuple[str, ...] = ()
    custom_named: bool = False
    # Carries MultiPak's owner label: MultiPak created it. Only these are MultiPak's.
    owned: bool = True

    @property
    def default_names(self) -> tuple[str, ...]:
        """The default names MultiPak gives a container with these contents."""
        if self.family == "arch":
            # 1.0.0 Alpha 1 used MultiPak-Arch-rolling.
            return ("MultiPak-Arch-Linux", "MultiPak-Arch-rolling")
        return (f"MultiPak-{self.family.title()}-{self.version}",)

    @property
    def multipak_number(self) -> int | None:
        """1 for the default name, 2+ for C2-, C3-...; None if not MultiPak's container.

        It must carry MultiPak's owner label and still have the name MultiPak gave it:
        a renamed container, or one named for other contents, is no longer MultiPak's.
        """
        if self.custom_named or not self.owned:
            return None
        match = re.fullmatch(r"(?:C([0-9]+)-)?(.+)", self.name)
        if match is None or match[2] not in self.default_names:
            return None
        if match[1] is None:
            return 1
        return int(match[1]) if int(match[1]) >= 2 else None

    @property
    def multipak_managed(self) -> bool:
        return self.multipak_number is not None

    @property
    def display_name(self) -> str:
        if self.name.startswith("MultiPak-"):
            return "MultiPak - " + self.name.removeprefix("MultiPak-").replace("-", " ")
        match = re.fullmatch(r"(C[0-9]+-)?MultiPak-(.+)", self.name)
        if match:
            prefix = (match[1] or "").replace("-", " - ")
            return prefix + "MultiPak - " + match[2].replace("-", " ")
        return self.name

    def matches(self, environment: Environment) -> bool:
        family, _, version = environment.id.partition("-")
        if family != self.family:
            return False
        if family == "arch" and version == "rolling":
            return True  # Arch has no VERSION_ID; pacman upgrades the rolling environment.
        if environment.resolved_version:
            return self.version == environment.resolved_version
        if version in ("latest", "stable"):
            # A floating tag is reusable only if the same cached image backs it.
            return (
                environment.image in self.image_names or environment.id in self.compatible_profiles
            )
        return version == self.version


@dataclass(frozen=True)
class PackageInspection:
    package: Package
    existing: tuple[ExistingEnvironment, ...] = ()
    environments: tuple[Environment, ...] = ()
    # appimage_dependencies.DependencyReport for AppImages; None when not checked.
    dependencies: object = None
    # Every container name in use, Distrobox or not, so new names never collide.
    container_names: tuple[str, ...] = ()


# Offline fallback only. The installed Distrobox catalog and registry metadata supply live versions.
ENVIRONMENTS = (
    Environment(
        "fedora-latest",
        mark("Fedora — latest stable"),
        "rpm",
        "quay.io/fedora/fedora-toolbox:latest",
    ),
    Environment("fedora-44", "Fedora 44", "rpm", "quay.io/fedora/fedora-toolbox:44"),
    Environment("fedora-43", "Fedora 43", "rpm", "quay.io/fedora/fedora-toolbox:43"),
    Environment(
        "ubuntu-latest",
        mark("Ubuntu — default catalog version"),
        "deb",
        "docker.io/library/ubuntu:latest",
    ),
    Environment(
        "debian-stable",
        "Debian 13",
        "deb",
        "docker.io/library/debian:stable",
        "13",
    ),
    Environment("ubuntu-24.04", "Ubuntu 24.04 LTS", "deb", "docker.io/library/ubuntu:24.04"),
    Environment("ubuntu-22.04", "Ubuntu 22.04 LTS", "deb", "docker.io/library/ubuntu:22.04"),
    Environment("debian-13", "Debian 13", "deb", "docker.io/library/debian:13"),
    Environment("debian-12", "Debian 12", "deb", "docker.io/library/debian:12"),
    Environment(
        "arch-rolling", "Arch Linux (rolling release)", "arch", "docker.io/library/archlinux:latest"
    ),
)


@dataclass
class Transaction:
    id: str
    container: str
    image: str
    package_name: str
    package_version: str
    package_kind: PackageKind
    sha256: str
    method: str = "distrobox"
    status: str = "prepared"
    stage: str = ""
    image_id: str = ""
    images_scanned: bool = False
    images_before: list[str] = field(default_factory=list)
    container_id: str = ""
    # A namespaced launcher is journaled BEFORE it is written.
    launchers: list[str] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    container_created: bool = True
    managed_home: bool = False
    home_created: bool = False
    package_install_started: bool = False
    environment_id: str = ""
    custom_container_name: bool = False
    force_new_environment: bool = False
    preserve_environment_on_cleanup: bool = False
    appimage_mode: str = "direct"
    # AppImages that need extra system components run in MultiPak's AppImage container.
    appimage_container: bool = False
    appimage_packages: list[str] = field(default_factory=list)
    appimage_verify: bool = False
    # Dependencies that could not be installed, from the last installation or update.
    appimage_missing: list[str] = field(default_factory=list)
    # Where to look for new versions when the AppImage does not say it itself: a link
    # the user entered, or the project page MultiPak found and confirmed.
    appimage_update_source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

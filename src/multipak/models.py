"""Serializable installation and environment models."""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from multipak.localization import mark

PackageKind = Literal[
    "rpm",
    "deb",
    "arch",
    "appimage",
    "snap",
    "slackware",
    "pisi",
    "slitaz",
    "gentoo",
    "puppy",
    "apk",
    "xbps",
    "eopkg",
    "android",
]


# The full Slackware environment is a variant of the same release and image,
# "slackware-15.0-full": it adds Slackware's application package sets.
FULL_VARIANT = "-full"
# Marks a container created as the full variant.
FULL_LABEL = "io.github.multipak.full-environment"


# Distributions with one release line, updated continuously: os-release ID and
# MultiPak's container name. Their environments are "<family>-rolling".
ROLLING_FAMILIES = {
    "arch": "MultiPak-Arch-Linux",
    "pisilinux": "MultiPak-Pisi-Linux",
    "gentoo": "MultiPak-Gentoo-Linux",
    "void": "MultiPak-Void-Linux",
    "solus": "MultiPak-Solus-Linux",
}
# Void Linux's musl and 32-bit environments: "void-musl" and "void-i686".
VOID_VARIANT_NAMES = {"musl": "MultiPak-Void-Linux-musl", "i686": "MultiPak-Void-Linux-32-bits"}


# Continuously updated release lines of distributions that also have fixed releases.
# The environment id and the container name carry them: opensuse-tumbleweed.
ROLLING_RELEASES = {"opensuse": "tumbleweed", "alt": "sisyphus"}
# Spelling in container names and titles where str.title() would be wrong.
FAMILY_TITLES = {"slitaz": "SliTaz", "opensuse": "openSUSE", "alt": "ALT"}


def container_title(family: str, version: str, *, full: bool = False) -> str:
    """The container name MultiPak gives a release: MultiPak-openSUSE-Tumbleweed."""
    if family == "void" and version in VOID_VARIANT_NAMES:
        return VOID_VARIANT_NAMES[version]
    if family in ROLLING_FAMILIES:
        return ROLLING_FAMILIES[family]
    label = version.title() if ROLLING_RELEASES.get(family) == version else version
    suffix = FULL_VARIANT if full else ""
    return f"MultiPak-{FAMILY_TITLES.get(family, family.title())}-{label}{suffix}"


def environment_release(environment_id: str) -> tuple[str, str]:
    """Distribution and release of an environment id, without the image variant."""
    family, _, version = environment_id.partition("-")
    return family, version.removesuffix(FULL_VARIANT)


def valid_environment_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", name))


def reserved_environment_name(name: str) -> bool:
    return bool(re.match(r"^(?:C[0-9]+-)?MultiPak", name, re.I))


@dataclass(frozen=True)
class AndroidPart:
    member: str
    split_id: str
    size: int
    sha256: str
    kind: str = "feature"
    target: str = ""
    qualifier: str = ""
    abis: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    required_types: tuple[str, ...] = ()
    split_types: tuple[str, ...] = ()
    min_sdk: int = 1


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
    android_version_code: int = 0
    android_target_sdk: int = 0
    android_bundle_format: str = ""
    android_expansions: tuple[AndroidPart, ...] = ()
    android_variants: tuple[dict, ...] = ()
    android_min_sdk: int = 1
    # URI schemes the app opens from links, such as "myapp" (VIEW + BROWSABLE).
    android_schemes: tuple[str, ...] = ()
    android_parts: tuple[AndroidPart, ...] = ()
    # snap_meta.SnapInfo for snaps; None otherwise.
    snap: object = None
    # Set when the snap was downloaded from the Snap Store.
    snap_revision: str = ""
    # The Snap Store channel it came from, such as latest/stable or latest/edge.
    snap_channel: str = ""
    # (channel, version) of every channel the Snap Store publishes it in.
    snap_channels: tuple = ()
    # snap_store.Resource for each component the store publishes with that revision.
    snap_resources: tuple = ()


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
    # Created as a full variant, such as Slackware 15.0 full.
    full_variant: bool = False

    @property
    def default_names(self) -> tuple[str, ...]:
        """The default names MultiPak gives a container with these contents."""
        if self.family == "arch":
            # 1.0.0 Alpha 1 used MultiPak-Arch-rolling.
            return ("MultiPak-Arch-Linux", "MultiPak-Arch-rolling")
        return (container_title(self.family, self.version, full=self.full_variant),)

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
        if family == "void":
            # One rolling release in three variants; discovery reads the variant.
            return (self.version or "rolling") == version
        if family in ROLLING_FAMILIES and version == "rolling":
            # Arch has no VERSION_ID; Pisi Linux's and Gentoo's change with updates.
            return True
        if family == "slackware":
            # Light and full images report the same release; only the image tells them apart.
            return (
                version.removesuffix(FULL_VARIANT) == self.version
                and version.endswith(FULL_VARIANT) == self.full_variant
            )
        if environment.resolved_version:
            return self.version == environment.resolved_version
        if version in ("latest", "stable"):
            # A floating tag is reusable only if the same cached image backs it.
            return (
                environment.image in self.image_names or environment.id in self.compatible_profiles
            )
        return version == self.version


@dataclass(frozen=True)
class AndroidPlan:
    initialized: bool = False
    translation: str = "native"
    bridge: str = ""
    explanation: str = ""


@dataclass(frozen=True)
class PackageInspection:
    package: Package
    existing: tuple[ExistingEnvironment, ...] = ()
    environments: tuple[Environment, ...] = ()
    android: AndroidPlan | None = None
    android_environments: tuple[dict, ...] = ()
    # appimage_dependencies.DependencyReport for AppImages; None when not checked.
    dependencies: object = None
    # Every container name in use, Distrobox or not, so new names never collide.
    container_names: tuple[str, ...] = ()
    # Snaps: why the snap cannot be installed, and shared snaps still to download.
    snap_refusal: str = ""
    snap_missing: tuple[str, ...] = ()
    # Features of the snap whose service this computer lacks.
    snap_services: tuple[str, ...] = ()


def package_environment_kinds(package: Package) -> tuple[str, ...]:
    """Environment kinds that can install the package.

    A Puppy Linux PET is converted to the format of the distribution it was built from.
    """
    return ("slackware", "deb") if package.kind == "puppy" else (package.kind,)


def package_profiles(package: Package, profiles) -> tuple[Environment, ...]:
    """The environments, of those offered, that can install the package."""
    result = tuple(item for item in profiles if item.kind in package_environment_kinds(package))
    if package.kind == "puppy" and package.distro_hint_source == "metadata":
        # pet.specs names the distribution the PET was built from.
        family = package.distro_hint.partition("-")[0] + "-"
        result = tuple(item for item in result if item.id.startswith(family)) or result
    return result


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
    Environment(
        "slackware-15.0",
        mark("Slackware 15.0 — light"),
        "slackware",
        "docker.io/aclemons/slackware:15.0",
    ),
    Environment(
        "slackware-15.0-full",
        mark("Slackware 15.0 — full (about 1.2 GB more to download)"),
        "slackware",
        "docker.io/aclemons/slackware:15.0",
    ),
)
SLACKWARE_ENVIRONMENTS = tuple(item for item in ENVIRONMENTS if item.kind == "slackware")
# Official image. The base tag is the minimal system; MultiPak's pre-init hook adds the
# package repository and what Distrobox needs (assets/pisi-setup.sh).
PISI_ENVIRONMENT = Environment(
    "pisilinux-rolling",
    "Pisi Linux (rolling release)",
    "pisi",
    "docker.io/pisilinux/chroot:base",
)
# SliTaz publishes no current container image. MultiPak builds these local images from
# the official Base ISO (slitaz_image.py); the two architectures have separate
# repositories. The release is cooking, SliTaz's continuously updated development line.
SLITAZ_ENVIRONMENT = Environment(
    "slitaz-cooking",
    mark("SliTaz cooking — 64-bit"),
    "slitaz",
    "localhost/multipak-slitaz:cooking",
)
SLITAZ_I486_ENVIRONMENT = Environment(
    "slitaz-cooking-i486",
    mark("SliTaz cooking — 32-bit (i486)"),
    "slitaz",
    "localhost/multipak-slitaz:cooking-i486",
)
SLITAZ_ENVIRONMENTS = (SLITAZ_ENVIRONMENT, SLITAZ_I486_ENVIRONMENT)


def slitaz_environment(architecture: str) -> Environment:
    """The SliTaz environment whose repository has a package of this architecture."""
    return SLITAZ_I486_ENVIRONMENT if architecture == "i486" else SLITAZ_ENVIRONMENT


# Gentoo's official base system (stage3). MultiPak switches it to the desktop profile and
# uses Gentoo's official binary packages, so dependencies are rarely compiled
# (assets/gentoo-setup.sh).
GENTOO_ENVIRONMENT = Environment(
    "gentoo-rolling",
    "Gentoo Linux (rolling release)",
    "gentoo",
    "docker.io/gentoo/stage3:amd64-openrc",
)


# Alpine's official image. Its packages name exact library versions, so they install in
# the release they were built for (estimated from abuild, alpine.estimated_release) or a
# close one. Alpine uses musl, not glibc. Distrobox knows apk and adds what it needs.
ALPINE_REPOSITORY = "docker.io/library/alpine"


def alpine_environment(release: str) -> Environment:
    """The environment of an Alpine release such as 3.24, or of edge."""
    title = (
        mark("Alpine Linux edge (development branch)")
        if release == "edge"
        else f"Alpine Linux {release}"
    )
    return Environment(f"alpine-{release}", title, "apk", f"{ALPINE_REPOSITORY}:{release}")


# Offline fallback: the supported releases in November 2026, newest first, then edge.
# The catalog lists the releases Alpine supports on the day.
ALPINE_ENVIRONMENTS = tuple(alpine_environment(item) for item in ("3.24", "3.23", "3.22", "edge"))
# The newest stable release of the fallback.
ALPINE_ENVIRONMENT = ALPINE_ENVIRONMENTS[0]


# Void's official full images: glibc, the one tested by Distrobox, and musl. The 32-bit
# glibc image shares the 64-bit one's tag, so MultiPak gives it a local name
# (void_image.py).
VOID_REPOSITORY = "ghcr.io/void-linux/void-glibc-full"
VOID_MUSL_REPOSITORY = "ghcr.io/void-linux/void-musl-full"
VOID_ENVIRONMENT = Environment(
    "void-rolling",
    mark("Void Linux (rolling release)"),
    "xbps",
    f"{VOID_REPOSITORY}:latest",
)
VOID_MUSL_ENVIRONMENT = Environment(
    "void-musl",
    mark("Void Linux musl (rolling release)"),
    "xbps",
    f"{VOID_MUSL_REPOSITORY}:latest",
)
VOID_I686_ENVIRONMENT = Environment(
    "void-i686",
    mark("Void Linux 32-bit (rolling release)"),
    "xbps",
    "localhost/multipak-void:i686",
)
VOID_ENVIRONMENTS = (VOID_ENVIRONMENT, VOID_MUSL_ENVIRONMENT, VOID_I686_ENVIRONMENT)


def void_environment(architecture: str) -> Environment:
    """The Void environment for a package's XBPS architecture; noarch suits any."""
    return {"x86_64-musl": VOID_MUSL_ENVIRONMENT, "i686": VOID_I686_ENVIRONMENT}.get(
        architecture, VOID_ENVIRONMENT
    )


def void_variant(images) -> str:
    """rolling, musl or i686: the variant of a Void container, from its image names."""
    for image in images:
        if image.startswith((VOID_MUSL_REPOSITORY + ":", VOID_MUSL_REPOSITORY + "@")):
            return "musl"
        if image == VOID_I686_ENVIRONMENT.image:
            return "i686"
    return "rolling"


# Solus publishes no container image of its own. This unofficial one is built in public
# GitLab CI (gitlab.com/silkeh/docker-solus) from the official Polaris repository with only
# the system.base component: about 800 MB on disk, against 3.3 GB for the official solbuild
# build image. It skips the packages' configuration scripts, so MultiPak completes it in a
# local image (solus_image.py). Distrobox does not know eopkg; MultiPak's pre-init hook
# adds what it needs (assets/solus-setup.sh).
SOLUS_BASE_IMAGE = "docker.io/silkeh/solus:base"
SOLUS_REPOSITORY = "localhost/multipak-solus"
SOLUS_ENVIRONMENT = Environment(
    "solus-rolling",
    mark("Solus (rolling release)"),
    "eopkg",
    f"{SOLUS_REPOSITORY}:base",
)


# RPM distributions other than Fedora, from their official images. Each installs RPM
# packages with its own package manager: dnf on Mageia, zypper on openSUSE and APT-RPM
# (apt-get) on ALT Linux. Distrobox knows all three and adds what it needs.
MAGEIA_REPOSITORY = "docker.io/library/mageia"
OPENSUSE_LEAP_REPOSITORY = "docker.io/opensuse/leap"
OPENSUSE_TUMBLEWEED_REPOSITORY = "docker.io/opensuse/tumbleweed"
ALT_REPOSITORY = "docker.io/library/alt"
RPM_FAMILIES = ("mageia", "opensuse", "alt")
_RPM_RELEASES = {
    "mageia": r"[0-9]+",
    "opensuse": r"[0-9]+\.[0-9]+",
    "alt": r"p[0-9]+",
}


def rpm_fixed_release(family: str, release: str) -> bool:
    """Whether the text names a fixed release of Mageia (10), openSUSE Leap (16.1) or ALT (p11)."""
    pattern = _RPM_RELEASES.get(family)
    return bool(pattern and re.fullmatch(pattern, release))


def rpm_release_key(release: str) -> tuple[int, ...]:
    return tuple(int(part) for part in release.removeprefix("p").split("."))


def rpm_environment(family: str, release: str) -> Environment:
    """The environment of a Mageia, openSUSE or ALT Linux release, or of its rolling line."""
    rolling = ROLLING_RELEASES.get(family) == release
    if not rolling and not rpm_fixed_release(family, release):
        raise ValueError(release)
    if family == "mageia":
        return Environment(
            f"mageia-{release}", f"Mageia {release}", "rpm", f"{MAGEIA_REPOSITORY}:{release}"
        )
    if family == "opensuse":
        if rolling:
            return Environment(
                "opensuse-tumbleweed",
                "openSUSE Tumbleweed",
                "rpm",
                f"{OPENSUSE_TUMBLEWEED_REPOSITORY}:latest",
            )
        return Environment(
            f"opensuse-{release}",
            f"openSUSE Leap {release}",
            "rpm",
            f"{OPENSUSE_LEAP_REPOSITORY}:{release}",
        )
    title = "ALT Linux Sisyphus" if rolling else f"ALT Linux {release}"
    return Environment(f"alt-{release}", title, "rpm", f"{ALT_REPOSITORY}:{release}")


# Offline fallback: the releases supported in November 2026, newest first, then the
# rolling line. Mageia's development branch (Cauldron) reports the next release number
# in os-release, so it could not be told apart from a stable release and is not offered.
RPM_FAMILY_ENVIRONMENTS = tuple(
    rpm_environment(family, release)
    for family, releases in (
        ("mageia", ("10", "9")),
        ("opensuse", ("16.1", "16.0", "tumbleweed")),
        ("alt", ("p11", "p10", "sisyphus")),
    )
    for release in releases
)


ENVIRONMENTS = (
    *ENVIRONMENTS,
    *RPM_FAMILY_ENVIRONMENTS,
    PISI_ENVIRONMENT,
    *SLITAZ_ENVIRONMENTS,
    GENTOO_ENVIRONMENT,
    *ALPINE_ENVIRONMENTS,
    *VOID_ENVIRONMENTS,
    SOLUS_ENVIRONMENT,
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
    # The staged file name when the format needs one, such as Slackware's
    # name-version-arch-build.txz. Otherwise the copy is package.<kind>.
    package_file: str = ""
    # Packages a Slackware package lists in install/slack-required.
    slackware_required: list[str] = field(default_factory=list)
    # "puppy" when MultiPak converted a Puppy Linux PET into this package format.
    converted_from: str = ""
    # Package names from pet.specs: installed when the distribution has them.
    puppy_dependencies: list[str] = field(default_factory=list)
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
    android_package_id: str = ""
    android_package_owned: bool = False
    android_translation_allowed: bool = False
    android_instance_id: str = ""
    android_profile: str = ""
    android_environment_label: str = ""
    # AppImages that need extra system components run in MultiPak's AppImage container.
    appimage_container: bool = False
    appimage_packages: list[str] = field(default_factory=list)
    appimage_verify: bool = False
    # Dependencies that could not be installed, from the last installation or update.
    appimage_missing: list[str] = field(default_factory=list)
    # Where to look for new versions when the AppImage does not say it itself: a link
    # the user entered, or the project page MultiPak found and confirmed.
    appimage_update_source: str = ""
    # Snaps run with bubblewrap from their own files and shared bases and content snaps.
    snap_name: str = ""
    snap_revision: str = ""
    snap_confinement: str = ""
    # The Snap Store channel the snap follows; empty for a snap installed from a file.
    snap_channel: str = ""
    # "name=revision" for the base and each content snap this snap uses.
    snap_runtime: list[str] = field(default_factory=list)
    snap_previous_revision: str = ""
    snap_previous_runtime: list[str] = field(default_factory=list)
    # "name=revision" for each optional component installed with the snap.
    snap_components: list[str] = field(default_factory=list)
    # What MultiPak could not provide for this installation, shown as a warning.
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

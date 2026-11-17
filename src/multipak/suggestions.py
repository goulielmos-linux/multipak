"""Explain the evidence used to suggest an environment, without claiming compatibility."""

import datetime

from multipak.localization import tr

from . import alpine
from .models import FAMILY_TITLES, RPM_FAMILIES, Environment, Package, rpm_environment

# The package manager MultiPak uses in each RPM distribution other than Fedora.
RPM_MANAGERS = {"mageia": "dnf", "opensuse": "zypper", "alt": "APT-RPM (apt-get)"}


def rpm_hint_label(family: str, release: str) -> str:
    """How people read an RPM distribution hint: "openSUSE Leap 16.1", "ALT Linux"."""
    if not release:
        return {"alt": "ALT Linux"}.get(family, FAMILY_TITLES.get(family, family.title()))
    return tr(rpm_environment(family, release).title)


def rpm_family_details(package: Package, environment: Environment, title: str) -> tuple[str, str]:
    """Evidence for a Mageia, openSUSE or ALT Linux package, from its RPM header."""
    family = environment.id.partition("-")[0]
    hint_family, _, hint_version = package.distro_hint.partition("-")
    title = tr(title)
    label = rpm_hint_label(hint_family, hint_version)
    if family != hint_family:
        return tr("Low"), tr(
            "The package's metadata indicates that it was built for {0}, but MultiPak could "
            "not offer an environment of that distribution. It suggests {1}; the application "
            "may not work there."
        ).format(label, title)
    if not hint_version:
        return tr("Medium"), tr(
            "The package's metadata identifies {0} but not the release it was built for. "
            "MultiPak uses {1}, the newest stable release. If a library is missing, try "
            "another release of {0}."
        ).format(label, title)
    if environment.id != package.distro_hint:
        return tr("Medium"), tr(
            "The package's metadata indicates that it was built for {0}, newer than the "
            "releases currently available. In these cases, MultiPak suggests the newest "
            "available release ({1}); if a library is missing, look for a package built for it."
        ).format(label, title)
    return tr("High"), tr(
        "The package's metadata indicates that it was built for {0}. MultiPak installs it "
        "in {0} with that distribution's package manager, {1}."
    ).format(label, RPM_MANAGERS[family])


_ANDROID_RELEASES = (
    "1.0",
    "1.1",
    "1.5",
    "1.6",
    "2.0",
    "2.0.1",
    "2.1",
    "2.2",
    "2.3",
    "2.3.3",
    "3.0",
    "3.1",
    "3.2",
    "4.0",
    "4.0.3",
    "4.1",
    "4.2",
    "4.3",
    "4.4",
    "4.4W",
    "5.0",
    "5.1",
    "6.0",
    "7.0",
    "7.1",
    "8.0",
    "8.1",
    "9",
    "10",
    "11",
    "12",
    "12L",
    "13",
    "14",
    "15",
    "16",
)


def _android_release(sdk: int) -> str:
    if 1 <= sdk <= len(_ANDROID_RELEASES):
        return "Android " + _ANDROID_RELEASES[sdk - 1]
    return tr("Android (version not recognized by MultiPak)")


def android_suggestion_details(package: Package, title: str, sdk: int) -> tuple[str, str]:
    minimum, target = package.android_min_sdk, package.android_target_sdk
    matching_target = target == sdk and 1 < minimum <= sdk
    strength = tr("High") if target == sdk else tr("Medium") if minimum > 1 else tr("Low")
    if matching_target:
        text = tr(
            "The application requires {0} or later and was developed targeting {1}. "
            "MultiPak can use this same version to prepare the environment."
        ).format(_android_release(minimum), title)
    elif minimum > sdk:
        strength = tr("Low")
        text = tr(
            "This application requires {0} or later, and MultiPak can only create up to {1}. "
            "Therefore, it cannot be installed."
        ).format(_android_release(minimum), title)
    elif minimum > 1:
        text = tr(
            "The application requires {0} or later. MultiPak recommends {1} "
            "because it is the newest version it can prepare."
        ).format(_android_release(minimum), title)
    else:
        text = tr(
            "The package does not specify a useful minimum Android version. MultiPak "
            "recommends {0}, the newest Android version it can create, but cannot confirm "
            "the application's requirements."
        ).format(title)
    if target > 0 and not matching_target:
        if target > sdk and minimum <= sdk:
            text += " " + tr("The application, however, was developed targeting {0}.").format(
                _android_release(target)
            )
            text += tr(
                " Since the suggested Android version is older, "
                "some features may behave differently."
            )
        else:
            text += " " + tr("The application was developed targeting {0}.").format(
                _android_release(target)
            )
    elif target <= 0:
        text += "\n\n" + tr(
            "The package does not say which Android version the developer configured it for."
        )
    return strength, tr("Identification confidence: {0}.\n\n{1}").format(strength, text)


def suggestion_details(package: Package, environment: Environment, title: str) -> tuple[str, str]:
    family, _, version = environment.id.partition("-")
    hint_family, _, hint_version = package.distro_hint.partition("-")
    matched = (
        bool(package.distro_hint)
        and family == hint_family
        and (version == hint_version or environment.resolved_version == hint_version)
    )
    if package.kind == "arch":
        strength = tr("Medium")
        text = tr(
            "The package's metadata identifies it as part of the Arch Linux family, but does "
            "not distinguish Arch Linux from its derivatives. In these cases, the suggested "
            "environment is Arch Linux."
        )
    elif package.kind == "slackware":
        strength = tr("Medium")
        text = tr(
            "Slackware packages do not say which release they were built for. MultiPak "
            "uses Slackware 15.0, the current stable release.\n\nThe light environment "
            "adds only what the application needs, but may miss something. The full "
            "environment downloads about 1.2 GB more and includes Slackware's common "
            "packages."
        )
    elif package.kind == "pisi":
        strength = tr("High")
        text = tr(
            "The package's metadata identifies Pisi Linux. MultiPak uses the official "
            "Pisi Linux image, which is updated continuously."
        )
    elif package.kind == "eopkg":
        strength = tr("High")
        text = tr(
            "The package's metadata identifies Solus, which publishes no official container image. "
            "MultiPak uses the community image silkeh/solus and installs dependencies from "
            "Solus's official repository."
        )
    elif package.kind == "slitaz":
        strength = tr("High")
        text = tr("The package's files identify {0}.").format(tr(title))
    elif package.kind == "gentoo":
        strength = tr("High")
        text = tr("The package's metadata identifies Gentoo (rolling release).")
    elif package.kind == "xbps":
        strength = tr("High")
        text = tr("The package's metadata identifies {0}.").format(tr(title))
    elif package.kind == "apk":
        estimate = (
            hint_version
            if hint_family == "alpine" and package.distro_hint_source == "metadata"
            else ""
        )
        if matched and estimate:
            strength = tr("High")
            text = tr(
                "The package's .PKGINFO names the version of abuild that built it, the one "
                "Alpine {0} uses. MultiPak uses {1}, from Alpine's official image."
            ).format(estimate, title)
            if not alpine.supported(estimate, datetime.date.today()):
                text += " " + tr(
                    "Alpine no longer updates this release. Prefer a newer package if one is "
                    "available."
                )
        elif estimate and (
            version == alpine.EDGE
            or (
                alpine.RELEASE.fullmatch(version)
                and alpine.release_key(estimate) > alpine.release_key(version)
            )
        ):
            strength = tr("Medium")
            text = tr(
                "The package was built with a newer version of abuild than Alpine's stable "
                "releases use, probably for Alpine's development branch. MultiPak uses {0}."
            ).format(title)
        else:
            strength = tr("Medium")
            text = tr(
                "The package's metadata does not show which Alpine release built it. MultiPak "
                "uses {0}, the newest stable release. If the package was built for an older "
                "release, a library may be missing."
            ).format(title)
        text += "\n\n" + tr(
            "Alpine uses the musl C library instead of glibc, so its packages work only in an "
            "Alpine environment."
        )
    elif package.kind == "puppy":
        if matched and package.distro_hint_source == "metadata":
            strength = tr("High")
            text = tr(
                "Puppy Linux is built from another distribution's packages, and pet.specs "
                "says this package was built for {0}. MultiPak converts it into a package "
                "of that distribution and adds the libraries its programs need."
            ).format(title)
        else:
            strength = tr("Low")
            text = tr(
                "Puppy Linux is built from another distribution's packages, but this package "
                "does not say which one, or names a release MultiPak does not offer. MultiPak "
                "suggests {0}. Compatibility is uncertain: if the application does not open, "
                "try another environment."
            ).format(title)
    elif (
        package.kind == "rpm"
        and hint_family in RPM_FAMILIES
        and package.distro_hint_source == "metadata"
    ):
        strength, text = rpm_family_details(package, environment, title)
    elif matched and package.distro_hint_source == "filename":
        strength = tr("Medium")
        text = tr(
            "The filename indicates that the package was built for {0}, but this could not be "
            "confirmed in the package's metadata."
        ).format(title)
    elif matched:
        strength = tr("High")
        built_for = (
            "Fedora " + hint_version if package.kind == "rpm" and hint_family == "fedora" else title
        )
        text = tr("The package's metadata indicates that it was built for {0}.").format(built_for)
        if package.filename_distro_hint and package.filename_distro_hint != package.distro_hint:
            strength = tr("Medium")
            other_family, _, other = package.filename_distro_hint.partition("-")
            other = (
                "Fedora " + other
                if other_family == "fedora"
                else rpm_hint_label(other_family, other)
            )
            text = tr(
                "The filename indicates that the package was built for {0}, unlike the "
                "package's metadata; the metadata took priority."
            ).format(other)
    else:
        strength = tr("Low")
        if package.kind == "rpm":
            number = environment.resolved_version or version
            text = tr(
                "Since MultiPak could not determine whether the package was built for Fedora "
                "or another RPM-based distribution, it will use the latest Fedora release "
                "({0}) as the default environment. If the application does not work, "
                "reinstall it from the advanced settings and choose another release or "
                "distribution."
            ).format(number if number.isdigit() else title)
        else:
            text = tr(
                "The target distribution could not be identified. Since the .deb "
                "format is used by both Ubuntu and Debian, the latest Ubuntu LTS "
                "version is used by default."
            )
        if version in ("latest", "stable") and not environment.resolved_version:
            text += tr(" The version number will be confirmed when the image is downloaded.")
    explanation = tr("Distribution detection confidence: {0}.\n\n{1}").format(strength, text)
    return strength, explanation

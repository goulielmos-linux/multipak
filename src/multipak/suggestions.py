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
            "releases MultiPak offers. MultiPak uses {1}; if a library is missing, look for "
            "a package built for it."
        ).format(label, title)
    return tr("High"), tr(
        "The package's metadata indicates that it was built for {0}. MultiPak installs it "
        "in {0} with that distribution's package manager, {1}."
    ).format(label, RPM_MANAGERS[family])


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
            "The .PKGINFO metadata identifies this as an Arch-based package, "
            "but does not distinguish Arch Linux from its derivatives. "
            "The recommended environment is Arch Linux (rolling release)."
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
            "The package's metadata identifies Solus. Solus publishes no container image: "
            "MultiPak uses an unofficial image built from Solus's official repository, "
            "about 300 MB to download the first time, and installs dependencies from it."
        )
    elif package.kind == "slitaz":
        strength = tr("High")
        text = tr(
            "The package's programs show whether it is for SliTaz's 64-bit or 32-bit (i486) "
            "repository. MultiPak builds its SliTaz environment from the official system "
            "image the first time, a download of about 30 MB."
        )
    elif package.kind == "gentoo":
        strength = tr("High")
        text = tr(
            "The package's metadata identifies Gentoo. MultiPak uses Gentoo's official base "
            "system, about 400 MB the first time, and its prebuilt packages for dependencies."
        )
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
                "The package's .PKGINFO does not show which Alpine release built it. MultiPak "
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
            "The filename suggests that this package was built for {0}, but its "
            "internal metadata does not confirm this. MultiPak used the filename to "
            "suggest this version."
        ).format(title)
    elif matched:
        strength = tr("High")
        text = tr(
            "The package's internal metadata indicates that it was built for "
            "{0}, so MultiPak recommends this version."
        ).format(title)
        if package.kind == "rpm" and hint_family == "fedora":
            text = tr(
                "The Release field in the package metadata indicates that it was built "
                "for Fedora {0}."
            ).format(hint_version)
        if package.filename_distro_hint and package.filename_distro_hint != package.distro_hint:
            other_family, _, other = package.filename_distro_hint.partition("-")
            if other_family == "fedora":
                text += tr(
                    " The filename indicates Fedora {0}, unlike the internal metadata; the "
                    "internal metadata took priority."
                ).format(other)
            else:
                text += tr(
                    " The filename indicates {0}, unlike the internal metadata; the "
                    "internal metadata took priority."
                ).format(rpm_hint_label(other_family, other))
    else:
        strength = tr("Low")
        text = tr("Could not identify the distribution and version this package was built for. ")
        if package.kind == "rpm":
            text = tr(
                "MultiPak could not determine which RPM-based distribution this "
                "package was built for, so it will use the latest Fedora version "
                "as the default environment: {0}.\n\nIf the application does not "
                "work after installation, it may have been built for a different "
                "Fedora version or another distribution, such as Nobara, Rocky "
                "Linux or AlmaLinux. Uninstall it through Application "
                "Manager — MultiPak and look for a package built for your chosen "
                "distribution."
            ).format(title)
        else:
            text = tr(
                "The target distribution could not be identified. Since the .deb "
                "format is used by both Ubuntu and Debian, the latest Ubuntu LTS "
                "version is used by default."
            )
        if version in ("latest", "stable") and not environment.resolved_version:
            text += tr(" The image version number has not been confirmed yet.")
    explanation = tr("Distribution detection confidence: {0}.\n\n{1}").format(strength, text)
    return strength, explanation

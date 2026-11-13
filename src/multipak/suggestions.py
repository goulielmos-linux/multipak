"""Explain the evidence used to suggest an environment, without claiming compatibility."""

from multipak.localization import tr

from .models import Environment, Package

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
            "This application requires {0} or later. The newest Android version MultiPak "
            "can create is {1}, which does not meet that requirement. "
            "Choose an existing container with a compatible Android version to continue."
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
            "The .PKGINFO metadata identifies this as an Arch-based package, "
            "but does not distinguish Arch Linux from its derivatives. "
            "The recommended environment is Arch Linux (rolling release)."
        )
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
            other = package.filename_distro_hint.partition("-")[2]
            text += tr(
                " The filename indicates Fedora {0}, unlike the internal metadata; the "
                "internal metadata took priority."
            ).format(other)
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
                "Linux, AlmaLinux or openSUSE. Uninstall it through Application "
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

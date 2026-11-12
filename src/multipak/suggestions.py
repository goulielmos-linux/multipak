"""Explain the evidence used to suggest an environment, without claiming compatibility."""

from multipak.localization import tr

from .models import Environment, Package


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

"""Container roles and deterministic names, independent from the user interface."""

import re

from .models import Environment, ExistingEnvironment


def container_base(environment: Environment, *, resolved_version: str | None = None) -> str:
    family, _, version = environment.id.partition("-")
    if family == "arch":
        return "MultiPak-Arch-Linux"
    target_version = resolved_version or environment.resolved_version or version
    return f"MultiPak-{family.title()}-{target_version}"


def next_container_name(base: str, names, *, new: bool = False) -> str:
    names = set(names)
    numbered = re.compile(r"C(?:[2-9]|[1-9][0-9]+)-" + re.escape(base) + r"$")
    # Keep the base name for the first container; subsequent creations fill C2, C3, ... gaps.
    has_managed = base in names or any(numbered.fullmatch(name) for name in names)
    if base not in names and (not new or not has_managed):
        return base
    number = 2
    while f"C{number}-{base}" in names:
        number += 1
    return f"C{number}-{base}"


# The shared AppImage container is never the default container for packages.
APPIMAGE_CONTAINER = "MultiPak-AppImages"


def default_environment(environments, profile: Environment) -> ExistingEnvironment | None:
    """MultiPak's own container for this distribution and version, if it has one.

    Its name is the default one, or C2-, C3-... when someone else had taken that name.
    """
    candidates = [
        item
        for item in environments
        if item.matches(profile) and item.multipak_managed and item.name != APPIMAGE_CONTAINER
    ]
    return next(iter(sorted(candidates, key=lambda item: (item.multipak_number, item.name))), None)


def free_container_name(base: str, names) -> str:
    """The default name, or C2-, C3-... when another container already uses it."""
    return next_container_name(base, names)


def display_container_name(name: str) -> str:
    if name.startswith("MultiPak-"):
        return "MultiPak - " + name.removeprefix("MultiPak-").replace("-", " ")
    match = re.fullmatch(r"(C[0-9]+-)?MultiPak-(.+)", name)
    if match:
        return (match[1] or "").replace("-", " - ") + "MultiPak - " + match[2].replace("-", " ")
    return name

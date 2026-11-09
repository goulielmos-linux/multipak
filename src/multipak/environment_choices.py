"""Container roles and deterministic names, independent from the user interface."""

import re

from .models import Environment, ExistingEnvironment


def container_base(environment: Environment) -> str:
    family, _, version = environment.id.partition("-")
    return f"MultiPak-{family.title()}-{environment.resolved_version or version}"


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


def default_environment(environments, profile: Environment) -> ExistingEnvironment | None:
    candidates = [
        item
        for item in environments
        if item.matches(profile) and item.multipak_managed and item.multipak_number == 1
    ]
    base = container_base(profile)
    return next(iter(sorted(candidates, key=lambda item: (item.name != base, item.name))), None)


def display_container_name(name: str) -> str:
    if name.startswith("MultiPak-"):
        return "MultiPak - " + name.removeprefix("MultiPak-").replace("-", " ")
    match = re.fullmatch(r"(C[0-9]+-)?MultiPak-(.+)", name)
    if match:
        return (match[1] or "").replace("-", " - ") + "MultiPak - " + match[2].replace("-", " ")
    return name

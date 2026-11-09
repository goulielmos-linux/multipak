"""Discover Bazaar's supported CLI before asking it to open a search window."""

import re
from dataclasses import dataclass

from multipak.localization import tr

from . import host
from .process import CommandError, Runner

BAZAAR_APP_ID = "io.github.kolunmi.Bazaar"


def application_search_term(package_name: str) -> str:
    """Drop packaging/channel suffixes without guessing an application ID."""
    value = re.sub(r"[-_.](stable|beta|nightly|unstable|amd64|x86_64)$", "", package_name)
    return re.sub(r"[-_]+", " ", value).strip()


@dataclass(frozen=True)
class BazaarCommand:
    argv: list[str]
    search_supported: bool
    term: str


def search_command(runner: Runner, term: str) -> BazaarCommand:
    term = application_search_term(term)
    candidates = []
    for binary in ("bazaar", "bazaar-daemon"):
        if executable := host.which(binary):
            candidates.append([executable])
    if executable := host.which("flatpak"):
        try:
            runner.run([executable, "info", BAZAAR_APP_ID], timeout=15)
        except CommandError:
            pass
        else:
            candidates.append([executable, "run", BAZAAR_APP_ID])
    for command in candidates:
        try:
            help_text = runner.run([*command, "--help"], timeout=15)
        except (CommandError, TimeoutError):
            continue
        if "--search-for" in help_text:
            # The equals form prevents even a term beginning with '-' becoming an option.
            return BazaarCommand([*command, f"--search-for={term}"], True, term)
    if candidates:
        return BazaarCommand(candidates[0], False, term)
    raise RuntimeError(tr("Bazaar is not installed or could not be found on this computer."))

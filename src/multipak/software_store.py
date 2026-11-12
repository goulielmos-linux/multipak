"""Find a supported host app store and use its available search interface."""

import re
from dataclasses import dataclass

from multipak.localization import tr

from . import host
from .process import CommandError, Runner


@dataclass(frozen=True)
class StoreCommand:
    argv: list[str]
    search_supported: bool
    term: str


@dataclass(frozen=True)
class _Store:
    app_id: str
    binaries: tuple[str, ...]
    search_option: str


STORES = (
    _Store("io.github.kolunmi.Bazaar", ("bazaar", "bazaar-daemon"), "--search-for"),
    _Store("org.gnome.Software", ("gnome-software",), "--search"),
    _Store("org.kde.discover", ("plasma-discover",), "--search"),
)
PROBE_ERRORS = (CommandError, OSError, TimeoutError)


def application_search_term(package_name: str) -> str:
    """Drop packaging/channel suffixes without guessing an application ID."""
    value = re.sub(r"[-_.](stable|beta|nightly|unstable|amd64|x86_64)$", "", package_name)
    return re.sub(r"[-_]+", " ", value).strip()


def _default_store(runner):
    if executable := host.which("xdg-mime"):
        try:
            return runner.run(
                [executable, "query", "default", "x-scheme-handler/appstream"],
                timeout=5,
                output_limit=4096,
            ).strip()
        except PROBE_ERRORS:
            pass
    return ""


def _flatpak_stores(runner):
    if not (executable := host.which("flatpak")):
        return []
    try:
        output = runner.run(
            [executable, "list", "--app", "--columns=application,branch,installation"],
            timeout=10,
            output_limit=256 * 1024,
        )
    except PROBE_ERRORS:
        return []
    installed = []
    for row in output.splitlines():
        fields = row.split("\t")
        if len(fields) != 3:
            continue
        app_id, branch, installation = fields
        if app_id not in {store.app_id for store in STORES} or not re.fullmatch(
            r"[A-Za-z0-9._-]+", branch
        ):
            continue
        if installation == "user":
            scope = "--user"
        elif installation in ("system", "default"):
            scope = "--system"
        elif re.fullmatch(r"[A-Za-z0-9_-]+", installation):
            scope = f"--installation={installation}"
        else:
            continue
        installed.append(
            (app_id, branch, scope, [executable, "run", scope, f"--branch={branch}", app_id])
        )
    installed.sort(key=lambda item: (item[1] != "stable", item[2] != "--user"))
    return installed


def search_command(runner: Runner, term: str) -> StoreCommand:
    term = application_search_term(term)
    preferred = _default_store(runner)
    flatpaks = _flatpak_stores(runner)
    fallback = None
    for store in sorted(STORES, key=lambda item: preferred != item.app_id + ".desktop"):
        candidates = [[path] for binary in store.binaries if (path := host.which(binary))]
        candidates.extend(
            argv for app_id, _branch, _scope, argv in flatpaks if app_id == store.app_id
        )
        for command in candidates:
            try:
                help_text = runner.run([*command, "--help"], timeout=5, output_limit=65536)
            except PROBE_ERRORS:
                continue
            option = re.escape(store.search_option)
            if re.search(rf"(?<![\w-]){option}(?=[=\s,]|$)", help_text):
                # Keep the search term in one argument, even if it starts with '-'.
                return StoreCommand([*command, f"{store.search_option}={term}"], True, term)
            if fallback is None:
                fallback = StoreCommand(command, False, term)
    if fallback is not None:
        return fallback
    raise RuntimeError(
        tr(
            "No supported app store was found. "
            "Install Bazaar, GNOME Software or KDE Discover to search for applications."
        )
    )

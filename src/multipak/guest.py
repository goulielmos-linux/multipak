"""Helper run inside the container, with the container's own Python.

Lists an installed package's files, finds the libraries it is missing and the packages
that provide them, and rebuilds Puppy packages, using the container's own tools."""

# Slackware 15.0 has Python 3.9: keep annotations from being evaluated.
from __future__ import annotations

import base64
import glob
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

MARKER = "MULTIPAK_RESULT="
SLACKWARE_DATABASE = Path("/var/log/packages")


def slackware_entry(name: str) -> Path | None:
    """The installed package's record, named name-version-arch-build."""
    if not SLACKWARE_DATABASE.is_dir():
        return None
    return next(
        (
            entry
            for entry in sorted(SLACKWARE_DATABASE.iterdir())
            if entry.name.rsplit("-", 3)[0] == name and len(entry.name.rsplit("-", 3)) == 4
        ),
        None,
    )


def slackware_files(name: str) -> list[str]:
    entry = slackware_entry(name)
    if entry is None:
        raise RuntimeError("The package is not installed.")
    lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()
    if "FILE LIST:" not in lines:
        raise RuntimeError("The package record has no file list.")
    files = lines[lines.index("FILE LIST:") + 1 :]
    # Paths are relative to /; install/ holds the package scripts, not installed files.
    return [
        "/" + value
        for value in files
        if value and value not in ("./", "install/") and not value.startswith("install/")
    ]


SLACKPKG = "/usr/sbin/slackpkg"
SLACKPKG_LISTS = Path("/var/lib/slackpkg")
# Official package sets, newest first. testing/ holds unreleased versions.
SLACKPKG_SERIES = ("patches", "slackware64", "extra", "pasture")
# What the full environment adds: the base system, libraries, network and X11
# applications. Kernels and boot loaders are useless in a container.
FULL_SERIES = ("a", "ap", "l", "n", "x", "xap")
NOT_IN_CONTAINER = re.compile(r"kernel-.*|lilo|elilo|grub|mkinitrd|os-prober|efibootmgr|syslinux")
# Data, not libraries, that graphical applications need and ldd cannot see. Without
# xkeyboard-config, for example, GTK crashes when it opens the display.
GRAPHICAL_DATA = (
    "xkeyboard-config",
    "dejavu-fonts-ttf",
    "hicolor-icon-theme",
    "adwaita-icon-theme",
    "gsettings-desktop-schemas",
    "shared-mime-info",
    "dconf",
)
MAX_ELF_CHECKS = 400
MAX_ROUNDS = 10
NOT_FOUND = re.compile(r"^\s*(\S+) => not found\s*$")


def _slackware_package_name(filename: str) -> str:
    return filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].rsplit("-", 3)[0]


def slackware_installed() -> set[str]:
    if not SLACKWARE_DATABASE.is_dir():
        return set()
    return {
        entry.name.rsplit("-", 3)[0]
        for entry in SLACKWARE_DATABASE.iterdir()
        if len(entry.name.rsplit("-", 3)) == 4
    }


def missing_libraries(name: str) -> set[str]:
    """Libraries the package's programs need that this system cannot find.

    ldd follows the whole chain, so libraries needed by other libraries appear too.
    The programs belong to the package being installed, so they run here anyway.
    """
    subprocess.run(["/sbin/ldconfig"], check=False)
    files = slackware_files(name)
    shipped = {value.rsplit("/", 1)[-1] for value in files}
    missing = set()
    checked = 0
    for value in files:
        path = Path(value)
        if checked >= MAX_ELF_CHECKS or path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        checked += 1
        result = subprocess.run(
            ["ldd", str(path)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
        for line in result.stdout.splitlines():
            match = NOT_FOUND.match(line)
            # A library the package ships itself is found by its own launcher.
            if match and match[1] not in shipped:
                missing.add(match[1])
    return missing


def slackware_providers(libraries: set[str]) -> dict[str, str]:
    """The official package that provides each library, from slackpkg's file lists.

    The lists hold real file names, such as libgtk-3.so.0.2404.27; installpkg creates
    the libgtk-3.so.0 link later. So a library also matches a longer versioned name.
    Some real files are numbered apart from the library name (libopenjp2.so.7 is
    libopenjp2.so.2.4.0); those match by the part before the version, as a last resort.
    """
    exact: dict[str, str] = {}
    stems = {library.split(".so", 1)[0] + ".so": library for library in libraries}
    by_stem: dict[str, str] = {}
    for series in SLACKPKG_SERIES:
        path = SLACKPKG_LISTS / f"{series}-filelist.gz"
        if not path.is_file() or len(exact) == len(libraries):
            continue
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                tokens = line.split()
                if len(tokens) < 2:
                    continue
                package = _slackware_package_name(tokens[0])
                for token in tokens[1:]:
                    base = token.rsplit("/", 1)[-1]
                    if ".so" not in base:
                        continue
                    parts = base.split(".")
                    for end in range(len(parts), 1, -1):
                        candidate = ".".join(parts[:end])
                        if candidate in libraries and candidate not in exact:
                            exact[candidate] = package
                        if parts[end - 1] == "so":
                            if candidate in stems and candidate not in by_stem:
                                by_stem[candidate] = package
                            break
    for stem, library in stems.items():
        if library not in exact and stem in by_stem:
            exact[library] = by_stem[stem]
    return exact


def slackware_available() -> set[str]:
    path = SLACKPKG_LISTS / "pkglist"
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[0] in SLACKPKG_SERIES:
            names.add(fields[1])
    return names


def slackware_full_set() -> list[str]:
    """Packages of the full environment's sets, from slackpkg's package list."""
    path = SLACKPKG_LISTS / "pkglist"
    names = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if (
            len(fields) > 6
            and fields[0] == "slackware64"
            and fields[6].rsplit("/", 1)[-1] in FULL_SERIES
            and not NOT_IN_CONTAINER.fullmatch(fields[1])
        ):
            names.append(fields[1])
    return names


def slackpkg_install(packages: list[str]) -> None:
    subprocess.run(
        [SLACKPKG, "-batch=on", "-default_answer=y", "install", *packages],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def slackware_dependencies(variant: str, name: str, required: list[str]) -> dict:
    """Install what the package needs from Slackware's official packages.

    Slackware records no dependencies, so the needs come from slack-required, when
    the package has one, and from the libraries its programs cannot find.
    """
    installed = slackware_installed()
    available = slackware_available()
    attempted: set[str] = set()
    added: list[str] = []
    wanted = [
        item for item in (*GRAPHICAL_DATA, *required) if item in available and item not in installed
    ]
    if variant == "full":
        wanted.extend(
            item for item in slackware_full_set() if item not in installed and item not in wanted
        )
    missing: set[str] = set()
    for _ in range(MAX_ROUNDS):
        missing = missing_libraries(name)
        providers = slackware_providers(missing) if missing else {}
        wanted.extend(
            package
            for package in sorted(set(providers.values()))
            if package not in installed and package not in wanted
        )
        wanted = [package for package in wanted if package not in attempted]
        if not wanted:
            break
        slackpkg_install(wanted)
        attempted.update(wanted)
        added.extend(wanted)
        installed = slackware_installed()
        wanted = []
    return {"added": added, "missing": sorted(missing)}


# Debian and Ubuntu: data, not libraries, that graphical applications need.
DEB_GRAPHICAL_DATA = (
    "xkb-data",
    "fonts-dejavu-core",
    "hicolor-icon-theme",
    "adwaita-icon-theme",
    "gsettings-desktop-schemas",
    "shared-mime-info",
)
GRAPHICAL_LIBRARY = re.compile(r"lib(?:gtk|gdk|X11|Qt\d*(?:Gui|Widgets)|SDL|fltk)", re.I)
# Converted PETs keep their own pinstall.sh and puninstall.sh here.
PUPPY_SCRIPTS = Path("/usr/share/multipak/puppy")
APT_ENV = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}


def _apt(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["apt-get", "-y", "-q", "-o", "Dpkg::Use-Pty=0", *arguments],
        check=check,
        stdin=subprocess.DEVNULL,
        env=APT_ENV,
    )


def _elf_libraries(root: Path) -> tuple[set[str], bool]:
    """Libraries the extracted package's programs cannot find, and whether it is graphical."""
    files = [path for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()]
    shipped = {path.name for path in files}
    library_paths = sorted({str(path.parent) for path in files if ".so" in path.name})
    environment = {**os.environ, "LC_ALL": "C"}
    if library_paths:
        environment["LD_LIBRARY_PATH"] = ":".join(library_paths)
    missing: set[str] = set()
    graphical = False
    checked = 0
    for path in files:
        if checked >= MAX_ELF_CHECKS:
            break
        try:
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        checked += 1
        result = subprocess.run(
            ["ldd", str(path)], capture_output=True, text=True, env=environment, check=False
        )
        for line in result.stdout.splitlines():
            if GRAPHICAL_LIBRARY.search(line.split("=>", 1)[0]):
                graphical = True
            match = NOT_FOUND.match(line)
            if match and match[1] not in shipped:
                missing.add(match[1])
    return missing, graphical


def deb_providers(libraries: set[str]) -> dict[str, str]:
    """The package that provides each library, from apt-file's index of the repository."""
    if not libraries:
        return {}
    if subprocess.run(["sh", "-c", "command -v apt-file"], capture_output=True).returncode:
        _apt("install", "--no-install-recommends", "apt-file")
    subprocess.run(["apt-file", "update"], check=False, stdin=subprocess.DEVNULL, env=APT_ENV)
    pattern = "/(" + "|".join(re.escape(item) for item in sorted(libraries)) + ")$"
    result = subprocess.run(
        ["apt-file", "search", "--regexp", pattern],
        capture_output=True,
        text=True,
        env=APT_ENV,
        check=False,
    )
    candidates: dict[str, list[tuple[int, str]]] = {}
    for line in result.stdout.splitlines():
        package, separator, path = line.partition(": ")
        if not separator or package.endswith(("-dbg", "-dbgsym")):
            continue
        library = path.rsplit("/", 1)[-1]
        if library not in libraries:
            continue
        # Prefer the system library folders over copies inside other applications.
        standard = re.match(r"/(?:usr/)?lib(?:32|64)?/(?:[^/]+-linux-gnu[^/]*/)?[^/]+$", path)
        candidates.setdefault(library, []).append((0 if standard else 1, package))
    return {
        library: sorted(options, key=lambda item: (item[0], len(item[1]), item[1]))[0][1]
        for library, options in candidates.items()
    }


def deb_available(names: list[str]) -> list[str]:
    available = []
    for name in names:
        result = subprocess.run(
            ["apt-cache", "show", "--no-all-versions", name],
            capture_output=True,
            text=True,
            env=APT_ENV,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            available.append(name)
    return available


def puppy_deb_dependencies(package: str, hints: list[str]) -> dict:
    """Add Depends to a converted PET from the libraries its programs need.

    pet.specs uses Puppy's own names; those that are also package names here are
    kept. The rest comes from the libraries ldd cannot find, looked up in the
    repository's file index.
    """
    import shutil
    import tempfile

    _apt("update", check=False)
    with tempfile.TemporaryDirectory(prefix="multipak-pet-") as directory:
        files = Path(directory, "files")
        subprocess.run(["dpkg-deb", "-x", package, str(files)], check=True)
        missing, graphical = _elf_libraries(files)
        providers = deb_providers(missing)
        wanted = deb_available([name.lower() for name in hints])
        if graphical:
            wanted.extend(deb_available(list(DEB_GRAPHICAL_DATA)))
        own = subprocess.check_output(
            ["dpkg-deb", "-f", package, "Package"], text=True, env=APT_ENV
        ).strip()
        for item in sorted(set(providers.values())):
            if item not in wanted:
                wanted.append(item)
        wanted = [item for item in dict.fromkeys(wanted) if item != own]
        tree = Path(directory, "tree")
        subprocess.run(["dpkg-deb", "-R", package, str(tree)], check=True)
        control = tree / "DEBIAN" / "control"
        lines = [
            line
            for line in control.read_text(encoding="utf-8").splitlines()
            if not line.startswith("Depends:")
        ]
        if wanted:
            lines.insert(1, "Depends: " + ", ".join(wanted))
        control.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rebuilt = Path(directory, "package.deb")
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "-b", str(tree), str(rebuilt)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        shutil.copyfile(rebuilt, package)
    return {"added": wanted, "missing": sorted(set(missing) - set(providers))}


def run_puppy_script(name: str, script: str) -> None:
    """Run a converted PET's script, as its package manager would; failures are ignored."""
    path = PUPPY_SCRIPTS / name / script
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name) or not path.is_file():
        return
    try:
        subprocess.run(
            ["sh", str(path)], cwd="/", stdin=subprocess.DEVNULL, timeout=300, check=False
        )
    except subprocess.TimeoutExpired:
        pass


PISI_DATABASE = Path("/var/lib/pisi/package")
# Solus's eopkg, a fork of PiSi, keeps the same records in its own folder.
EOPKG_DATABASE = Path("/var/lib/eopkg/package")
PISI_BUS = Path("/run/dbus")


def pisi_entry(name: str, database: Path | None = None) -> Path | None:
    """The installed package's record, a folder named name-version-release."""
    database = database or PISI_DATABASE
    if not database.is_dir():
        return None
    return next(
        (
            entry
            for entry in sorted(database.iterdir())
            if entry.name.rsplit("-", 2)[0] == name and len(entry.name.rsplit("-", 2)) == 3
        ),
        None,
    )


def pisi_files(name: str, database: Path | None = None) -> list[str]:
    entry = pisi_entry(name, database)
    if entry is None:
        raise RuntimeError("The package is not installed.")
    root = ElementTree.parse(entry / "files.xml").getroot()
    return ["/" + (item.text or "").strip("/") for item in root.iterfind("File/Path")]


def pisi_dependents(name: str, database: Path | None = None) -> list[str]:
    """Installed packages that list the package among their runtime dependencies."""
    database = database or PISI_DATABASE
    dependents = []
    for entry in sorted(database.iterdir()) if database.is_dir() else ():
        try:
            package = ElementTree.parse(entry / "metadata.xml").getroot().find("Package")
        except (OSError, ElementTree.ParseError):
            continue
        if package is None or package.findtext("Name") == name:
            continue
        needs = {
            (item.text or "").strip()
            for item in package.iterfind("RuntimeDependencies//Dependency")
        }
        if name in needs:
            dependents.append(package.findtext("Name") or entry.name)
    return dependents


def eopkg_files(name: str) -> list[str]:
    return pisi_files(name, EOPKG_DATABASE)


def pisi_bus() -> dict:
    """Start the container's own system D-Bus, which pisi needs for package scripts.

    Distrobox does not share the host's system bus. A socket left by an earlier start of
    the container refuses connections, so it is replaced when its daemon is gone.
    """
    socket, pid = PISI_BUS / "system_bus_socket", PISI_BUS / "pid"
    try:
        running = socket.is_socket() and Path(f"/proc/{int(pid.read_text())}").is_dir()
    except (OSError, ValueError):
        running = False
    if not running:
        for path in (socket, pid):
            if path.exists() or path.is_symlink():
                path.unlink()
        PISI_BUS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dbus-daemon", "--system", "--fork"], check=True)
    return {"running": True}


SLITAZ_DATABASE = Path("/var/lib/tazpkg/installed")
SLITAZ_INSTALLED = Path("/var/lib/tazpkg/installed.info")


def slitaz_entry(name: str) -> Path | None:
    """The installed package's record, a folder with its receipt and files.list."""
    if "/" in name or name in ("", ".", ".."):
        return None
    entry = SLITAZ_DATABASE / name
    return entry if (entry / "receipt").is_file() else None


def slitaz_files(name: str) -> list[str]:
    entry = slitaz_entry(name)
    if entry is None:
        raise RuntimeError("The package is not installed.")
    lines = (entry / "files.list").read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in lines if line.startswith("/")]


def slitaz_dependents(name: str) -> list[str]:
    """Installed packages that list the package among their dependencies.

    installed.info has one line per package; its eighth field holds the dependencies.
    """
    if not SLITAZ_INSTALLED.is_file():
        return []
    dependents = []
    for line in SLITAZ_INSTALLED.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split("\t")
        if len(fields) > 7 and fields[0] != name and name in fields[7].split():
            dependents.append(fields[0])
    return dependents


GENTOO_DATABASE = Path("/var/db/pkg")
GENTOO_VERSION = re.compile(
    r"-[0-9]+(?:\.[0-9]+)*[a-z]?(?:_(?:alpha|beta|pre|rc|p)[0-9]*)*(?:-r[0-9]+)?$"
)


def gentoo_entries(name: str) -> list[Path]:
    """Records of the installed package, folders named category/name-version.

    A package can be installed in more than one slot.
    """
    if not GENTOO_DATABASE.is_dir() or "/" in name or name in ("", ".", ".."):
        return []
    return [
        entry
        for entry in sorted(GENTOO_DATABASE.glob(f"*/{glob.escape(name)}-*"))
        if (match := GENTOO_VERSION.search(entry.name)) and entry.name[: match.start()] == name
    ]


def gentoo_files(name: str) -> list[str]:
    entries = gentoo_entries(name)
    if not entries:
        raise RuntimeError("The package is not installed.")
    files = []
    for entry in entries:
        lines = (entry / "CONTENTS").read_text(encoding="utf-8", errors="replace").splitlines()
        # "obj PATH MD5 MTIME"; the path may contain spaces.
        files.extend(line[4:].rsplit(" ", 2)[0] for line in lines if line.startswith("obj "))
    return files


def gentoo_dependents(name: str) -> list[str]:
    """Installed packages whose runtime dependencies name the package."""
    atoms = {f"{entry.parent.name}/{name}" for entry in gentoo_entries(name)}
    if not atoms:
        return []
    pattern = re.compile(
        r"(?<![\w/+.-])(?:"
        + "|".join(re.escape(atom) for atom in sorted(atoms))
        + r")(?=$|[\s:\[]|-[0-9])"
    )
    own = set(gentoo_entries(name))
    dependents = []
    for entry in sorted(GENTOO_DATABASE.glob("*/*")):
        if entry in own:
            continue
        needs = " ".join(
            (entry / key).read_text(encoding="utf-8", errors="replace")
            for key in ("RDEPEND", "PDEPEND")
            if (entry / key).is_file()
        )
        if pattern.search(needs):
            dependents.append(f"{entry.parent.name}/{entry.name}")
    return dependents


ALPINE_DATABASE = Path("/lib/apk/db/installed")
# A dependency or provided name, without its version constraint: "so:libX11.so.6>=1.8".
ALPINE_ATOM = re.compile(r"[^<>=~]+")


def alpine_records() -> list[dict[str, list[str]]]:
    """Installed packages from apk's database: blocks of "X:value" lines.

    P is the name, F a folder, R a file in the last folder, D the dependencies and p
    the names the package provides (libraries, commands...).
    """
    if not ALPINE_DATABASE.is_file():
        return []
    records: list[dict[str, list[str]]] = []
    record: dict[str, list[str]] = {}
    for line in ALPINE_DATABASE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            if record:
                records.append(record)
            record = {}
        elif len(line) > 1 and line[1] == ":":
            record.setdefault(line[0], []).append(line[2:])
    if record:
        records.append(record)
    return records


def alpine_record(name: str) -> dict[str, list[str]] | None:
    return next((item for item in alpine_records() if item.get("P") == [name]), None)


def alpine_files(name: str) -> list[str]:
    """Files of the installed package: each R line belongs to the F line before it."""
    if alpine_record(name) is None:
        raise RuntimeError("The package is not installed.")
    files: list[str] = []
    current, folder = "", ""
    for line in ALPINE_DATABASE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            current, folder = "", ""
        elif line.startswith("P:"):
            current = line[2:]
        elif line.startswith("F:"):
            folder = line[2:]
        elif line.startswith("R:") and current == name:
            files.append("/" + "/".join(filter(None, (folder, line[2:]))))
    return files


def alpine_dependents(name: str) -> list[str]:
    """Installed packages that need the package, by name or by a name it provides."""
    record = alpine_record(name)
    if record is None:
        return []
    names = {name}
    for line in record.get("p", ()):
        names.update(match[0] for item in line.split() if (match := ALPINE_ATOM.match(item)))
    dependents = []
    for item in alpine_records():
        if item.get("P") == [name]:
            continue
        needs = {
            match[0]
            for line in item.get("D", ())
            for value in line.split()
            if not value.startswith("!") and (match := ALPINE_ATOM.match(value))
        }
        if needs & names:
            dependents.extend(item.get("P", ()))
    return dependents


def package_present(kind: str, name: str) -> bool:
    if kind == "slackware":
        return slackware_entry(name) is not None
    if kind == "pisi":
        return pisi_entry(name) is not None
    if kind == "eopkg":
        return pisi_entry(name, EOPKG_DATABASE) is not None
    if kind == "slitaz":
        return slitaz_entry(name) is not None
    if kind == "gentoo":
        return bool(gentoo_entries(name))
    if kind == "apk":
        return alpine_record(name) is not None
    command = {
        "rpm": ["rpm", "-q", name],
        "deb": ["dpkg-query", "-W", name],
        "arch": ["pacman", "-Q", name],
        "xbps": ["xbps-query", "-p", "pkgver", "--", name],
    }[kind]
    return (
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        == 0
    )


def remove_package(kind: str, name: str) -> dict:
    if kind == "pisi" and package_present(kind, name):
        # pisi would also remove the packages that depend on it.
        dependents = pisi_dependents(name)
        if dependents:
            raise RuntimeError("Other packages depend on it: " + ", ".join(dependents))
        pisi_bus()
    if kind == "eopkg" and package_present(kind, name):
        # Like pisi, eopkg would also remove the packages that depend on it.
        dependents = pisi_dependents(name, EOPKG_DATABASE)
        if dependents:
            raise RuntimeError("Other packages depend on it: " + ", ".join(dependents))
    if kind == "slitaz" and package_present(kind, name):
        # Without a terminal, tazpkg removes the package and leaves its dependents broken.
        dependents = slitaz_dependents(name)
        if dependents:
            raise RuntimeError("Other packages depend on it: " + ", ".join(dependents))
    if kind == "gentoo" and package_present(kind, name):
        # emerge --unmerge does not check whether other packages need it.
        dependents = gentoo_dependents(name)
        if dependents:
            raise RuntimeError("Other packages depend on it: " + ", ".join(dependents))
        atoms = [f"={entry.parent.name}/{entry.name}" for entry in gentoo_entries(name)]
        subprocess.run(
            ["emerge", "--ask=n", "--quiet", "--unmerge", *atoms],
            check=True,
            stdin=subprocess.DEVNULL,
        )
    if kind == "apk" and package_present(kind, name):
        # apk del keeps a package others need and still succeeds; say why instead.
        dependents = alpine_dependents(name)
        if dependents:
            raise RuntimeError("Other packages depend on it: " + ", ".join(dependents))
    if kind == "slackware" and package_present(kind, name):
        # removepkg runs no scripts; a converted PET's puninstall.sh runs here.
        run_puppy_script(name, "puninstall.sh")
    if package_present(kind, name):
        # Native dependency checks remain enabled. Never remove existing dependents or
        # autoremove shared dependencies just to discard one application's installation.
        command = {
            "rpm": ["rpm", "-e", name],
            "deb": ["dpkg", "--purge", name],
            "arch": ["pacman", "-R", "--noconfirm", name],
            # Without --force-revdeps, XBPS refuses to break installed dependents.
            "xbps": ["xbps-remove", "-y", "--", name],
            # Slackware does not track dependents; only this package is removed.
            "slackware": ["/sbin/removepkg", name],
            "pisi": ["pisi", "remove", "--no-color", "--yes-all", name],
            "eopkg": ["eopkg", "remove", "--no-color", "--yes-all", name],
            "slitaz": ["tazpkg", "remove", name],
            "gentoo": ["emerge", "--ask=n", "--quiet", "--unmerge", name],
            # Removes it from apk's world; dependencies nothing else needs go with it.
            "apk": ["apk", "del", "--no-progress", name],
        }[kind]
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    return {"present": package_present(kind, name)}


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def transaction_busy(marker: str) -> bool:
    path = Path(marker)
    if not path.exists():
        return False
    processes = json.loads(path.read_text())
    return any(
        identity is not None and process_identity(int(pid)) == identity
        for pid, identity in processes.items()
    )


def run_transaction(marker: str, command: list[str]) -> int:
    import os

    path = Path(marker)
    identity = process_identity(os.getpid())
    if identity is None:
        raise RuntimeError("Could not monitor the operation in the shared environment.")
    identities = {str(os.getpid()): identity}

    def record():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(identities))
        temporary.replace(path)

    record()
    try:
        with subprocess.Popen(command) as process:
            identities[str(process.pid)] = process_identity(process.pid)
            record()
            return process.wait()
    finally:
        path.unlink(missing_ok=True)


def collect(kind: str, name: str) -> dict:
    if kind == "deb":
        status = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Status}", name],
            text=True,
        )
        if status.strip() != "install ok installed":
            raise RuntimeError("The package is not fully installed.")
        command = ["dpkg-query", "-L", name]
    elif kind == "arch":
        subprocess.run(["pacman", "-Q", name], check=True, stdout=subprocess.DEVNULL)
        command = ["pacman", "-Qlq", name]
    elif kind == "rpm":
        subprocess.run(["rpm", "-q", name], check=True, stdout=subprocess.DEVNULL)
        command = ["rpm", "-ql", name]
    elif kind == "xbps":
        state = subprocess.check_output(["xbps-query", "-p", "state", "--", name], text=True)
        if state.strip() != "installed":
            raise RuntimeError("The package is not fully installed.")
        command = ["xbps-query", "-f", "--", name]
    elif kind in ("slackware", "pisi", "eopkg", "slitaz", "gentoo", "apk"):
        command = None
    else:
        raise ValueError("Unsupported package type.")
    if command is None:
        readers = {
            "slackware": slackware_files,
            "pisi": pisi_files,
            "eopkg": eopkg_files,
            "slitaz": slitaz_files,
            "gentoo": gentoo_files,
            "apk": alpine_files,
        }
        files = readers[kind](name)
    else:
        files = subprocess.check_output(command, text=True).splitlines()
    if kind == "xbps":
        # xbps-query -f shows symbolic links as "path -> target".
        files = [value.partition(" -> ")[0] for value in files]
    desktops = []
    for value in files:
        path = Path(value)
        if not path.is_absolute() or path.suffix != ".desktop" or not path.is_file():
            continue
        if path.stat().st_size > 1024 * 1024 or len(desktops) >= 32:
            continue
        desktops.append(
            {
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    return {"desktops": desktops}


if __name__ == "__main__":
    if sys.argv[1] == "--run":
        raise SystemExit(run_transaction(sys.argv[2], sys.argv[3:]))
    if sys.argv[1] == "--busy":
        result = {"busy": any(transaction_busy(marker) for marker in sys.argv[2:])}
    elif sys.argv[1] == "--state":
        result = {"present": package_present(sys.argv[2], sys.argv[3])}
    elif sys.argv[1] == "--remove":
        result = remove_package(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "--pisi-bus":
        result = pisi_bus()
    elif sys.argv[1] == "--puppy-deb-dependencies":
        result = puppy_deb_dependencies(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "--slackware-dependencies":
        result = slackware_dependencies(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        result = collect(sys.argv[1], sys.argv[2])
    print(MARKER + json.dumps(result))

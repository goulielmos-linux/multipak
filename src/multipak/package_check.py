#!/usr/bin/env python3
"""Run as root inside a container: which packages have a newer version available.

Usage: package_check.py KIND [--refresh] NAME... Without --refresh nothing is
downloaded. Prints MARK and a JSON object. Standard library only, so it runs with the
container's own Python."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MARK = "MULTIPAK_PACKAGE_UPDATES"


def run(*command):
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # The parsers read English output.
        env=dict(os.environ, LC_ALL="C", LANG="C"),
        check=False,
    )


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text):
    """Output without the color codes pisi and eopkg may add."""
    return ANSI.sub("", text)


def entry(status, installed="", version="", error=""):
    return {"status": status, "installed": installed, "version": version, "error": error}


def decide(names, installed, available, in_repositories):
    """available: name -> newer version; in_repositories: names some repository offers."""
    result = {}
    for name in names:
        if name in available:
            result[name] = entry("available", installed.get(name, ""), available[name])
        elif name not in in_repositories:
            result[name] = entry("no-source", installed.get(name, ""))
        else:
            result[name] = entry("current", installed.get(name, ""))
    return result


def package_of(token, names):
    """The name a name-version token, such as gimp-2.10-r0, starts with: the longest."""
    matches = [name for name in names if token.startswith(name + "-")]
    return max(matches, key=len) if matches else None


# Debian and Ubuntu


def parse_apt_policy(text, names):
    blocks, current = {}, None
    for line in text.splitlines():
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            current = line.rstrip()[:-1].split(":")[0]
            blocks[current] = {"installed": "", "candidate": "", "sources": set()}
        elif current is not None:
            stripped = line.strip()
            if stripped.startswith("Installed:"):
                blocks[current]["installed"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Candidate:"):
                blocks[current]["candidate"] = stripped.split(":", 1)[1].strip()
            elif stripped[:1].isdigit() and " " in stripped and line.startswith("        "):
                # "500 http://deb.debian.org/debian trixie/main amd64 Packages"
                blocks[current]["sources"].add(stripped.split(None, 1)[1].split()[0])
    installed, available, in_repositories = {}, {}, set()
    for name in names:
        block = blocks.get(name)
        if not block or block["installed"] in ("", "(none)"):
            continue
        installed[name] = block["installed"]
        if block["candidate"] not in ("", "(none)", block["installed"]):
            available[name] = block["candidate"]
        # The installed copy's own record: dpkg's status file, or APT-RPM's RPM database.
        if block["sources"] - {"/var/lib/dpkg/status", "RPM"}:
            in_repositories.add(name)
    result = decide(names, installed, available, in_repositories)
    for name in names:
        if name not in installed:
            result[name] = entry("failed", error="not installed")
    return result


def check_apt(names, refresh):
    if refresh:
        run("apt-get", "update", "-qq")
    return parse_apt_policy(run("apt-cache", "policy", "--", *names).stdout, names)


# Fedora and other distributions with DNF


def parse_rpm_versions(text):
    versions = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            versions[parts[0]] = parts[1]
    return versions


def parse_dnf_check_update(text, names):
    available = {}
    for line in text.splitlines():
        if line.strip().lower().startswith("obsoleting"):
            break
        parts = line.split()
        if len(parts) < 3 or "." not in parts[0]:
            continue
        name = parts[0].rsplit(".", 1)[0]
        if name in names:
            available[name] = parts[1]
    return available


def check_dnf(names, refresh):
    tool = "dnf5" if shutil.which("dnf5") else "dnf"
    cache = "--refresh" if refresh else "--cacheonly"
    update = run(tool, "-q", cache, "check-update", *names)
    if update.returncode not in (0, 100):
        raise RuntimeError(update.stdout.strip().splitlines()[-1:] or ["dnf failed"])
    offered = run(tool, "-q", cache, "repoquery", "--available", "--qf", "%{name}\n", *names)
    in_repositories = {line.strip() for line in offered.stdout.splitlines()} & set(names)
    installed = parse_rpm_versions(
        run("rpm", "-q", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n", *names).stdout
    )
    return decide(names, installed, parse_dnf_check_update(update.stdout, names), in_repositories)


# openSUSE


def table_rows(text):
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 5 and not set(line.strip()) <= set("-+|"):
            yield cells


def parse_zypper_updates(text, names):
    # S | Repository | Name | Current Version | Available Version | Arch
    return {
        cells[2]: cells[4] for cells in table_rows(text) if cells[2] in names and cells[2] != "Name"
    }


def parse_zypper_search(text, names):
    # S | Name | Type | Version | Arch | Repository
    return {
        cells[1]
        for cells in table_rows(text)
        if len(cells) >= 6 and cells[1] in names and cells[5] not in ("(System Packages)", "")
    }


def check_zypper(names, refresh):
    zypper = ("zypper", "--non-interactive", "--quiet")
    if refresh:
        run(*zypper, "refresh")
    else:
        zypper += ("--no-refresh",)
    updates = run(*zypper, "list-updates").stdout
    search = run(
        *zypper, "search", "--details", "--match-exact", "--type", "package", *names
    ).stdout
    installed = parse_rpm_versions(
        run("rpm", "-q", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n", *names).stdout
    )
    return decide(
        names, installed, parse_zypper_updates(updates, names), parse_zypper_search(search, names)
    )


# Arch Linux


def parse_pacman_upgrades(text, names):
    # "gimp 2.10.38-1 -> 3.0.0-1"
    available = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->" and parts[0] in names:
            available[parts[0]] = parts[3]
    return available


def check_pacman(names, refresh):
    # A separate database, like checkupdates: syncing the real one without upgrading
    # would leave a partial upgrade behind for the next installation.
    database = tempfile.mkdtemp(prefix="multipak-pacman-")
    try:
        # pacman 7 downloads as its DownloadUser (alpm): it must be able to enter the folder.
        os.chmod(database, 0o755)
        os.symlink("/var/lib/pacman/local", os.path.join(database, "local"))
        sync = run("pacman", "-Sy", "--dbpath", database, "--logfile", "/dev/null")
        if sync.returncode != 0:
            raise RuntimeError(sync.stdout.strip().splitlines()[-1:] or ["pacman failed"])
        available = parse_pacman_upgrades(
            run("pacman", "-Qu", "--dbpath", database, *names).stdout, names
        )
        in_repositories = {
            name
            for name in names
            if run("pacman", "-Si", "--dbpath", database, name).returncode == 0
        }
    finally:
        shutil.rmtree(database, ignore_errors=True)
    installed = parse_rpm_versions(run("pacman", "-Q", *names).stdout)
    return decide(names, installed, available, in_repositories)


# Alpine Linux


def parse_apk_version(text, names):
    # "gimp-2.10.38-r0   < 2.10.38-r1"; "?" when no repository has the package.
    installed, available, in_repositories = {}, {}, set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ("<", "=", ">", "?"):
            continue
        name = package_of(parts[0], names)
        if name is None:
            continue
        installed[name] = parts[0][len(name) + 1 :]
        if parts[1] != "?":
            in_repositories.add(name)
        if parts[1] == "<" and len(parts) >= 3:
            available[name] = parts[2]
    return decide(names, installed, available, in_repositories)


def check_apk(names, refresh):
    if refresh:
        run("apk", "update", "--quiet")
    return parse_apk_version(run("apk", "version", *names).stdout, names)


# Void Linux


def parse_xbps_updates(text, names):
    # "gimp-3.0.0_1 update x86_64 https://repo-default.voidlinux.org/current ..."
    available = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "update":
            name = package_of(parts[0], names)
            if name is not None:
                available[name] = parts[0][len(name) + 1 :]
    return available


def check_xbps(names, refresh):
    if refresh:
        run("xbps-install", "-S")
    available = parse_xbps_updates(run("xbps-install", "-un", *names).stdout, names)
    in_repositories = {
        name for name in names if run("xbps-query", "-R", "-p", "pkgver", "--", name).stdout.strip()
    }
    installed = {}
    for name in names:
        pkgver = run("xbps-query", "-p", "pkgver", "--", name).stdout.strip()
        if pkgver.startswith(name + "-"):
            installed[name] = pkgver[len(name) + 1 :]
    return decide(names, installed, available, in_repositories)


# Solus


def parse_eopkg_upgrades(text, names):
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] in names:
            found[parts[0]] = ""
    return found


def check_pisi_family(tool, color, names, refresh):
    """eopkg (Solus) comes from pisi (Pisi Linux): the same commands and output."""
    if refresh:
        run(tool, color, "update-repo")
    available = parse_eopkg_upgrades(plain(run(tool, color, "list-upgrades").stdout), names)
    in_repositories = set()
    for name in names:
        info = plain(run(tool, color, "info", name).stdout).lower()
        if "found in" in info and "repository" in info:
            in_repositories.add(name)
    return decide(names, {}, available, in_repositories)


def check_eopkg(names, refresh):
    return check_pisi_family("eopkg", "-N", names, refresh)


def check_pisi(names, refresh):
    return check_pisi_family("pisi", "--no-color", names, refresh)


# Slackware: slackpkg's list of the repository's packages


SLACKWARE_SERIES = ("patches", "slackware64", "extra", "pasture")


def parse_slackpkg_list(text):
    """{name: name-version-arch-build}; a patch replaces the release's package."""
    offered = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[0] not in SLACKWARE_SERIES:
            continue
        if fields[1] not in offered or fields[0] == "patches":
            offered[fields[1]] = fields[5]
    return offered


def slackware_installed(records, names):
    """{name: name-version-arch-build} from the names of /var/log/packages."""
    installed = {}
    for record in records:
        parts = record.rsplit("-", 3)
        if len(parts) == 4 and parts[0] in names:
            installed[parts[0]] = record
    return installed


def slackware_version(record):
    return "-".join(record.rsplit("-", 3)[1::2])  # version-build, without the arch


def decide_slackware(names, installed, offered):
    # slackpkg upgrade-all installs the repository's package whenever it differs.
    available = {
        name: slackware_version(offered[name])
        for name in names
        if name in installed and name in offered and offered[name] != installed[name]
    }
    return decide(
        names,
        {name: slackware_version(record) for name, record in installed.items()},
        available,
        set(offered) & set(names),
    )


def check_slackpkg(names, refresh):
    if refresh:
        run("/usr/sbin/slackpkg", "-batch=on", "-default_answer=y", "update")
    try:
        with open("/var/lib/slackpkg/pkglist", encoding="utf-8", errors="replace") as stream:
            offered = parse_slackpkg_list(stream.read())
    except OSError as error:
        raise RuntimeError("slackpkg has no package list") from error
    records = os.listdir("/var/log/packages") if os.path.isdir("/var/log/packages") else []
    return decide_slackware(names, slackware_installed(records, names), offered)


# SliTaz: tazpkg's lists, one tab-separated line per package


def parse_tazpkg_info(text):
    """{name: version} from packages.info or installed.info."""
    versions = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) > 1 and fields[0]:
            versions[fields[0]] = fields[1]
    return versions


def check_tazpkg(names, refresh):
    if refresh:
        run("tazpkg", "recharge")
    lists = {}
    for name in ("installed.info", "packages.info"):
        try:
            with open(f"/var/lib/tazpkg/{name}", encoding="utf-8", errors="replace") as stream:
                lists[name] = parse_tazpkg_info(stream.read())
        except OSError:
            lists[name] = {}
    installed = {
        name: lists["installed.info"][name] for name in names if name in lists["installed.info"]
    }
    offered = lists["packages.info"]
    # tazpkg up installs the mirror's package whenever its version differs.
    available = {
        name: offered[name]
        for name in installed
        if name in offered and offered[name] != installed[name]
    }
    return decide(names, installed, available, set(offered) & set(names))


# Gentoo: Portage itself, through the container's Python


def gentoo_atom(name):
    """category/name of the installed package, from Portage's database."""
    for entry in sorted(glob.glob(f"/var/db/pkg/*/{glob.escape(name)}-*")):
        category, folder = entry.split("/")[-2:]
        if folder[len(name) + 1 : len(name) + 2].isdigit():
            return f"{category}/{name}"
    return None


def check_portage(names, refresh):
    if refresh:
        # The same signed snapshot as installation and the update.
        run("emerge-webrsync", "--quiet")
    import portage  # noqa: PLC0415 - only on Gentoo
    from portage.versions import cpv_getversion, vercmp  # noqa: PLC0415

    trees = portage.db[portage.root]
    installed_db, tree_db = trees["vartree"].dbapi, trees["porttree"].dbapi
    installed, available, in_repositories = {}, {}, set()
    for name in names:
        atom = gentoo_atom(name)
        if atom is None:
            continue
        current = portage.best(installed_db.match(atom))
        if not current:
            continue
        installed[name] = cpv_getversion(current)
        best = tree_db.xmatch("bestmatch-visible", atom)
        if best:
            in_repositories.add(name)
            if vercmp(cpv_getversion(best), installed[name]) > 0:
                available[name] = cpv_getversion(best)
    return decide(names, installed, available, in_repositories)


def checker(kind):
    if kind == "deb":
        return check_apt
    if kind == "rpm":
        if shutil.which("dnf5") or shutil.which("dnf"):
            return check_dnf
        if shutil.which("zypper"):
            return check_zypper
        if shutil.which("apt-get"):  # ALT Linux: APT-RPM
            return check_apt
        return None
    return {
        "arch": check_pacman,
        "apk": check_apk,
        "xbps": check_xbps,
        "eopkg": check_eopkg,
        "pisi": check_pisi,
        "slackware": check_slackpkg,
        "slitaz": check_tazpkg,
        "gentoo": check_portage,
    }.get(kind)


def main(argv):
    kind, rest = argv[1], argv[2:]
    refresh = bool(rest) and rest[0] == "--refresh"
    names = list(dict.fromkeys(rest[1:] if refresh else rest))
    check = checker(kind)
    if check is None:
        result = {name: entry("unsupported") for name in names}
    else:
        try:
            result = check(names, refresh)
        except Exception as error:  # noqa: BLE001 - every failure is reported per package
            result = {name: entry("failed", error=str(error)) for name in names}
    print(MARK + " " + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

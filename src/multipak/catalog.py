"""Discover Distrobox images and resolve version tags without pulling images."""

import datetime
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from multipak.localization import tr

from . import alpine, host
from .models import (
    ALPINE_ENVIRONMENTS,
    ALPINE_REPOSITORY,
    ALT_REPOSITORY,
    ENVIRONMENTS,
    GENTOO_ENVIRONMENT,
    MAGEIA_REPOSITORY,
    OPENSUSE_LEAP_REPOSITORY,
    OPENSUSE_TUMBLEWEED_REPOSITORY,
    PISI_ENVIRONMENT,
    ROLLING_RELEASES,
    RPM_FAMILIES,
    SLACKWARE_ENVIRONMENTS,
    SLITAZ_ENVIRONMENTS,
    SOLUS_ENVIRONMENT,
    SOLUS_REPOSITORY,
    VOID_ENVIRONMENTS,
    VOID_REPOSITORY,
    Environment,
    alpine_environment,
    environment_release,
    rpm_environment,
    rpm_fixed_release,
    rpm_release_key,
)
from .process import Cancelled, CommandError, Runner

# Catalog entries select repositories, never executable fragments or arbitrary registries.
REPOSITORIES = {
    "fedora": (
        "quay.io/fedora/fedora-toolbox",
        "registry.fedoraproject.org/fedora-toolbox",
        "quay.io/fedora/fedora",
        "registry.fedoraproject.org/fedora",
    ),
    "ubuntu": ("docker.io/library/ubuntu",),
    "debian": ("docker.io/library/debian",),
    "arch": ("docker.io/library/archlinux",),
    # Unofficial image, kept up to date with Slackware's security patches.
    "slackware": ("docker.io/aclemons/slackware",),
    "pisilinux": ("docker.io/pisilinux/chroot",),
    # Built locally from the official Base ISO (slitaz_image.py).
    "slitaz": ("localhost/multipak-slitaz",),
    "gentoo": ("docker.io/gentoo/stage3",),
    # Official image: the releases Alpine supports, and edge.
    "alpine": (ALPINE_REPOSITORY,),
    "void": (VOID_REPOSITORY,),
    # Built locally from an unofficial image of the official repository (solus_image.py).
    "solus": (SOLUS_REPOSITORY,),
    # Official images. openSUSE publishes Leap and Tumbleweed in separate repositories.
    "mageia": (MAGEIA_REPOSITORY,),
    "opensuse": (OPENSUSE_LEAP_REPOSITORY, OPENSUSE_TUMBLEWEED_REPOSITORY),
    "alt": (ALT_REPOSITORY,),
}
FAMILY_KINDS = {
    "fedora": "rpm",
    "ubuntu": "deb",
    "debian": "deb",
    "arch": "arch",
    "slackware": "slackware",
    "pisilinux": "pisi",
    "slitaz": "slitaz",
    "gentoo": "gentoo",
    "alpine": "apk",
    "void": "xbps",
    "solus": "eopkg",
    "mageia": "rpm",
    "opensuse": "rpm",
    "alt": "rpm",
}
# The oldest release still supported in November 2026; newer ones come from the registry.
# Older ones stay available to packages built for them (package_environment).
RPM_OLDEST = {"mageia": "9", "opensuse": "16.0", "alt": "p10"}
CACHE_SECONDS = 24 * 60 * 60


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.removeprefix("p").split("."))


def release_tag(family: str, tag: str) -> bool:
    pattern = {
        "ubuntu": r"[0-9]+\.[0-9]{2}",
        "slackware": r"[0-9]+\.[0-9]+",
        "alpine": r"3\.[0-9]+",
        "opensuse": r"[0-9]+\.[0-9]+",
        "alt": r"p[0-9]+",
    }.get(family, r"[0-9]+")
    return bool(re.fullmatch(pattern, tag))


def parse_catalog(text: str) -> dict[str, dict[str, set[str]]]:
    result = {family: {} for family in REPOSITORIES}
    for line in text.splitlines():
        image = line.strip()
        for family, allowed in REPOSITORIES.items():
            for repo in allowed:
                if image == repo or image.startswith(repo + ":"):
                    tag = image[len(repo) :].removeprefix(":") or "latest"
                    if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", tag):
                        result[family].setdefault(repo, set()).add(tag)
    return result


def read_cached(path: Path | None) -> tuple[Environment, ...]:
    if path is None:
        return ()
    try:
        rows = json.loads(path.read_text())
        profiles = tuple(Environment(**row) for row in rows)
        for profile in profiles:
            family, _, version = profile.id.partition("-")
            repo, _, tag = profile.image.rpartition(":")
            if family == "slackware":
                # Only the fixed light and full images MultiPak offers.
                if profile not in SLACKWARE_ENVIRONMENTS:
                    return ()
                continue
            if family == "pisilinux":
                if profile != PISI_ENVIRONMENT:
                    return ()
                continue
            if family == "slitaz":
                if profile not in SLITAZ_ENVIRONMENTS:
                    return ()
                continue
            if family == "void":
                if profile not in VOID_ENVIRONMENTS:
                    return ()
                continue
            if family in ("gentoo", "solus"):
                fixed = {"gentoo": GENTOO_ENVIRONMENT, "solus": SOLUS_ENVIRONMENT}[family]
                if profile != fixed:
                    return ()
                continue
            if family == "alpine":
                # A release or edge, from Alpine's official image.
                if not (
                    release_tag(family, version) or version == alpine.EDGE
                ) or profile != alpine_environment(version):
                    return ()
                continue
            if family in RPM_FAMILIES:
                # A release or the rolling line, from the distribution's official image.
                if not (
                    rpm_fixed_release(family, version) or ROLLING_RELEASES.get(family) == version
                ) or profile != rpm_environment(family, version):
                    return ()
                continue
            if repo not in REPOSITORIES.get(family, ()):
                return ()
            if not release_tag(family, tag) and tag not in ("latest", "stable"):
                return ()
            if not release_tag(family, version) and version not in ("latest", "stable"):
                if family != "arch" or version != "rolling" or tag != "latest":
                    return ()
            if family == "arch" and (
                profile.id != "arch-rolling" or tag != "latest" or profile.resolved_version
            ):
                return ()
            if profile.kind != FAMILY_KINDS[family]:
                return ()
        # Existing installations may have a fresh cache predating Arch, Slackware, PiSi,
        # SliTaz, Gentoo, Alpine, Void Linux, Solus, Mageia, openSUSE or ALT Linux support.
        later = ("arch", "slackware", "pisilinux", "slitaz", "gentoo", "alpine", "void", "solus")
        present = {environment_release(profile.id)[0] for profile in profiles}
        # Or predating Void Linux's musl and 32-bit environments.
        known = {profile.id for profile in profiles}
        return profiles + tuple(
            item
            for item in ENVIRONMENTS
            if (
                (family := environment_release(item.id)[0]) in (*later, *RPM_FAMILIES)
                and family not in present
            )
            or (item in VOID_ENVIRONMENTS and "void" in present and item.id not in known)
        )
    except (OSError, ValueError, TypeError, KeyError):
        return ()


def registry_version(family: str, metadata: dict) -> str:
    labels = metadata.get("Labels") or {}
    for key in ("version", "org.opencontainers.image.version"):
        candidate = labels.get(key, "")
        if isinstance(candidate, str) and release_tag(family, candidate):
            return candidate
    return ""


def debian_stable_version() -> str:
    """The toolbox 'latest' tag can be unstable, so confirm Debian's stable release."""
    try:
        with urllib.request.urlopen(
            "https://deb.debian.org/debian/dists/stable/Release", timeout=10
        ) as response:
            release = response.read(131072).decode("utf-8")
        match = re.search(r"^Version:\s*([0-9]+)(?:\.[0-9]+)*\s*$", release, re.M)
        return match[1] if match else ""
    except (OSError, ValueError):
        return ""


def alpine_end_of_support() -> dict[str, str]:
    """Alpine's end-of-support dates: the current file, over the copy MultiPak ships."""
    result = dict(alpine.END_OF_SUPPORT)
    try:
        with urllib.request.urlopen(
            "https://alpinelinux.org/releases.json", timeout=10
        ) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
        for branch in data.get("release_branches", ()):
            release = str(branch.get("rel_branch", "")).removeprefix("v")
            end = branch.get("eol_date")
            if release_tag("alpine", release) and isinstance(end, str):
                datetime.date.fromisoformat(end)
                result[release] = end
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def registry_tags(runner: Runner, repository: str) -> set[str]:
    """Every tag of an image repository, without pulling it; empty when unavailable."""
    if not host.which("skopeo"):
        return set()
    try:
        data = json.loads(runner.run(["skopeo", "list-tags", "docker://" + repository], timeout=30))
        return {tag for tag in data.get("Tags", ()) if isinstance(tag, str)}
    except Cancelled:
        raise
    except (CommandError, OSError, TimeoutError, ValueError, TypeError, AttributeError):
        return set()


def rpm_family_profiles(
    runner: Runner, family: str, cached: tuple[Environment, ...] = ()
) -> tuple[list[Environment], bool]:
    """Supported Mageia, openSUSE Leap or ALT releases, newest first, then the rolling line."""
    fallback = [item for item in ENVIRONMENTS if environment_release(item.id)[0] == family]
    repository = OPENSUSE_LEAP_REPOSITORY if family == "opensuse" else REPOSITORIES[family][0]
    tags = registry_tags(runner, repository)
    if not tags:
        prior = [item for item in cached if environment_release(item.id)[0] == family]
        return prior or fallback, False
    oldest = rpm_release_key(RPM_OLDEST[family])
    releases = sorted(
        (
            tag
            for tag in tags
            if rpm_fixed_release(family, tag)
            and rpm_release_key(tag) >= oldest
            # Leap 42.x came before Leap 15.
            and not (family == "opensuse" and tag.startswith("42."))
        ),
        key=rpm_release_key,
        reverse=True,
    )
    if not releases:
        return fallback, False
    rolling = ROLLING_RELEASES.get(family)
    # Tumbleweed has its own repository, with the tag latest only.
    if rolling and (family == "opensuse" or rolling in tags):
        releases.append(rolling)
    return [rpm_environment(family, item) for item in releases], True


def alpine_profiles(
    runner: Runner, cached: tuple[Environment, ...] = ()
) -> tuple[list[Environment], bool]:
    """The Alpine releases still supported today, newest first, then edge.

    Older releases stay available to packages built for them (package_environment).
    """
    tags = registry_tags(runner, ALPINE_REPOSITORY)
    if not tags:
        prior = [item for item in cached or ENVIRONMENTS if item.kind == "apk"]
        return prior or list(ALPINE_ENVIRONMENTS), False
    today = datetime.date.today()
    end_of_support = alpine_end_of_support()
    releases = sorted(
        (
            tag
            for tag in tags
            if release_tag("alpine", tag) and alpine.supported(tag, today, end_of_support)
        ),
        key=version_key,
        reverse=True,
    )
    if alpine.EDGE in tags:
        releases.append(alpine.EDGE)
    if not releases:
        return list(ALPINE_ENVIRONMENTS), False
    return [alpine_environment(item) for item in releases], True


def load_catalog(runner: Runner, cache: Path | None = None) -> tuple[Environment, ...]:
    cached = read_cached(cache)
    if cached and cache is not None:
        try:
            if time.time() - cache.stat().st_mtime < CACHE_SECONDS:
                return cached
        except OSError:
            pass
    text = ""
    if host.which("distrobox"):
        try:
            text = runner.run(["distrobox", "create", "-C"], timeout=30)
        except Cancelled:
            raise
        except (CommandError, OSError, TimeoutError):
            pass
    entries = parse_catalog(text)
    profiles = []
    refreshed = False
    for family, allowed in REPOSITORIES.items():
        if family == "slackware":
            # Slackware 15.0 is the stable release; the full image is a variant of it.
            profiles.extend(SLACKWARE_ENVIRONMENTS)
            continue
        if family == "pisilinux":
            profiles.append(PISI_ENVIRONMENT)
            continue
        if family == "slitaz":
            profiles.extend(SLITAZ_ENVIRONMENTS)
            continue
        if family == "gentoo":
            profiles.append(GENTOO_ENVIRONMENT)
            continue
        if family == "void":
            profiles.extend(VOID_ENVIRONMENTS)
            continue
        if family == "solus":
            profiles.append(SOLUS_ENVIRONMENT)
            continue
        if family == "alpine":
            releases, alpine_refreshed = alpine_profiles(runner, cached)
            profiles.extend(releases)
            refreshed = refreshed or alpine_refreshed
            continue
        if family in RPM_FAMILIES:
            releases, rpm_refreshed = rpm_family_profiles(runner, family, cached)
            profiles.extend(releases)
            refreshed = refreshed or rpm_refreshed
            continue
        repository = next((repo for repo in allowed if repo in entries[family]), allowed[0])
        if family == "arch":
            profiles.append(
                Environment(
                    "arch-rolling", "Arch Linux (rolling release)", "arch", repository + ":latest"
                )
            )
            continue
        tags = set(entries[family].get(repository, ()))
        alias = (
            "stable"
            if family == "debian" and repository == "docker.io/library/debian"
            else "latest"
        )
        metadata = {}
        if host.which("skopeo"):
            try:
                metadata = json.loads(
                    runner.run(
                        [
                            "skopeo",
                            "inspect",
                            "docker://" + repository + ":" + alias,
                        ],
                        timeout=30,
                    )
                )
                if not isinstance(metadata, dict):
                    raise ValueError(tr("Invalid image metadata."))
                tags.update(tag for tag in metadata.get("RepoTags", ()) if isinstance(tag, str))
                refreshed = True
            except Cancelled:
                raise
            except (CommandError, OSError, TimeoutError, ValueError, TypeError):
                metadata = {}
        if not tags:
            prior = [item for item in cached or ENVIRONMENTS if item.id.startswith(family + "-")]
            profiles.extend(prior)
            continue
        kind = FAMILY_KINDS[family]
        resolved = registry_version(family, metadata)
        if family == "debian":
            stable = debian_stable_version()
            if not stable:
                stable = next(
                    (
                        item.resolved_version
                        for item in cached or ENVIRONMENTS
                        if item.id == "debian-stable"
                    ),
                    "",
                )
            if not stable or stable not in tags:
                profiles.extend(
                    item for item in cached or ENVIRONMENTS if item.id.startswith("debian-")
                )
                continue
            alias, resolved = "stable", stable
        alias_id = "stable" if family == "debian" else "latest"
        title = family.title() + (f" {resolved}" if resolved else tr(" — stable version"))
        profiles.append(
            Environment(
                f"{family}-{alias_id}",
                title,
                kind,
                repository + ":" + alias,
                resolved,
            )
        )
        versions = sorted(
            (tag for tag in tags if release_tag(family, tag)), key=version_key, reverse=True
        )
        profiles.extend(
            Environment(
                f"{family}-{version}",
                f"{family.title()} {version}",
                kind,
                repository + ":" + version,
            )
            for version in versions
        )
    result = tuple(profiles)
    if cache is not None and result and (text or refreshed):
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(json.dumps([asdict(item) for item in result]))
            temporary.replace(cache)
        except OSError:
            pass
    return result or cached or ENVIRONMENTS


def package_environment(hint: str, profiles: list[Environment]) -> Environment | None:
    if hint == "arch-rolling":
        return next((item for item in profiles if item.id == hint and item.kind == "arch"), None)
    if hint.startswith("slackware-") or hint in (
        PISI_ENVIRONMENT.id,
        *(item.id for item in SLITAZ_ENVIRONMENTS),
        GENTOO_ENVIRONMENT.id,
        *(item.id for item in VOID_ENVIRONMENTS),
        SOLUS_ENVIRONMENT.id,
    ):
        return next((item for item in profiles if item.id == hint), None)
    family, _, version = hint.partition("-")
    if family == "alpine":
        return alpine_package_environment(version, profiles)
    if family in RPM_FAMILIES:
        return rpm_package_environment(family, version, profiles)
    if family not in REPOSITORIES or not release_tag(family, version):
        return None
    base = next((item for item in profiles if item.id.startswith(family + "-")), None)
    if base is None:
        return None
    repository = base.image.rpartition(":")[0]
    return Environment(hint, f"{family.title()} {version}", base.kind, repository + ":" + version)


def alpine_package_environment(release: str, profiles: list[Environment]) -> Environment | None:
    """The environment for a package built for this Alpine release.

    The catalog lists the supported releases. An older one is still offered, since its
    packages need its libraries. A release newer than the stable ones is edge's.
    """
    exact = next((item for item in profiles if item.id == f"alpine-{release}"), None)
    if exact is not None or not release_tag("alpine", release):
        return exact
    offered = [item for item in profiles if item.kind == "apk"]
    stable = [
        item.id.partition("-")[2]
        for item in offered
        if release_tag("alpine", item.id.partition("-")[2])
    ]
    if not offered:
        return None
    if stable and version_key(release) > max(version_key(item) for item in stable):
        return next((item for item in offered if item.id == "alpine-edge"), offered[0])
    return alpine_environment(release)


def rpm_package_environment(
    family: str, release: str, profiles: list[Environment]
) -> Environment | None:
    """The environment for an RPM built for this Mageia, openSUSE or ALT Linux release.

    An empty release means the package names the distribution only: its newest stable
    release is used. An older release is still offered, since the package needs its
    libraries. A release newer than the stable ones is the rolling line's, if any.
    """
    offered = [item for item in profiles if environment_release(item.id)[0] == family]
    if not offered:
        return None
    exact = next((item for item in offered if item.id == f"{family}-{release}"), None)
    if exact is not None:
        return exact
    stable = [item for item in offered if rpm_fixed_release(family, item.id.partition("-")[2])]
    rolling = next(
        (item for item in offered if item.id.partition("-")[2] == ROLLING_RELEASES.get(family)),
        None,
    )
    if not release:
        return (stable or offered)[0]
    if release == ROLLING_RELEASES.get(family):
        return rpm_environment(family, release)
    if not rpm_fixed_release(family, release):
        return None
    newest = max((rpm_release_key(item.id.partition("-")[2]) for item in stable), default=())
    if stable and rpm_release_key(release) > newest:
        return rolling or stable[0]
    return rpm_environment(family, release)


def alternatives(profiles: list[Environment], recommended: Environment) -> list[Environment]:
    family, _, version = recommended.id.partition("-")
    current = recommended.resolved_version or version
    if not release_tag(family, current):
        current = ""
    result = {}
    for profile in profiles:
        other_family, _, other_version = profile.id.partition("-")
        if other_family != family or not release_tag(family, other_version):
            continue
        if current and version_key(other_version) >= version_key(current):
            continue
        result.setdefault(other_version, profile)
    return [result[key] for key in sorted(result, key=version_key, reverse=True)]

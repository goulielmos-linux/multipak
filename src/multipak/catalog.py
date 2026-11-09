"""Discover Distrobox images and resolve version tags without pulling images."""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from multipak.localization import tr

from . import host
from .models import ENVIRONMENTS, Environment
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
}
FAMILY_KINDS = {"fedora": "rpm", "ubuntu": "deb", "debian": "deb", "arch": "arch"}
CACHE_SECONDS = 24 * 60 * 60


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def release_tag(family: str, tag: str) -> bool:
    pattern = r"[0-9]+\.[0-9]{2}" if family == "ubuntu" else r"[0-9]+"
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
        # Existing installations may have a fresh cache predating Arch support.
        return profiles + tuple(
            item
            for item in ENVIRONMENTS
            if item.kind == "arch" and not any(profile.kind == "arch" for profile in profiles)
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
    family, _, version = hint.partition("-")
    if family not in REPOSITORIES or not release_tag(family, version):
        return None
    base = next((item for item in profiles if item.id.startswith(family + "-")), None)
    if base is None:
        return None
    repository = base.image.rpartition(":")[0]
    return Environment(hint, f"{family.title()} {version}", base.kind, repository + ":" + version)


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

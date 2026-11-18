"""Read-only discovery of reusable Distrobox environments in the same Podman store."""

import json
import re
import shlex
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from multipak.localization import tr

from . import alpine, host
from .models import (
    ENVIRONMENTS,
    FULL_LABEL,
    Environment,
    ExistingEnvironment,
    void_variant,
)
from .process import Cancelled, CommandError, Runner

OWNER_LABEL = "io.github.multipak.transaction"
SHARED_LABEL = "io.github.multipak.shared-environment"
CUSTOM_LABEL = "io.github.multipak.custom-name"


def parse_os_release(value: str) -> dict[str, str]:
    result = {}
    for line in value.splitlines():
        key, separator, raw = line.partition("=")
        if separator and key in ("ID", "VERSION_ID", "ALT_BRANCH_ID"):
            words = shlex.split(raw, comments=True)
            result[key] = words[0] if words else ""
    return result


# Distributions MultiPak can reuse, by MultiPak's name; os-release IDs that differ.
FAMILIES = (
    "fedora",
    "ubuntu",
    "debian",
    "arch",
    "slackware",
    "pisilinux",
    "slitaz",
    "gentoo",
    "alpine",
    "void",
    "solus",
    "mageia",
    "opensuse",
    "alt",
)
OS_RELEASE_FAMILIES = {
    "opensuse-leap": "opensuse",
    "opensuse-tumbleweed": "opensuse",
    "altlinux": "alt",
}


def release_identity(release: dict[str, str]) -> tuple[str, str]:
    """MultiPak's distribution and release for an os-release record: (opensuse, 16.1)."""
    release_id = release.get("ID", "")
    version = release.get("VERSION_ID", "")
    if release_id == "alpine":
        # Alpine's VERSION_ID adds the patch level (3.24.3), or is the next
        # release's alpha on edge (3.25.0_alpha20260805): 3.24 or edge.
        version = alpine.release_version(version)
    elif release_id == "opensuse-tumbleweed":
        # Tumbleweed's VERSION_ID is the date of its snapshot.
        version = "tumbleweed"
    elif release_id == "altlinux":
        # ALT names its branch (p11, sisyphus); VERSION_ID is 11 or a date on Sisyphus.
        version = release.get("ALT_BRANCH_ID", "")
        if not re.fullmatch(r"p[0-9]+|sisyphus", version):
            major = release.get("VERSION_ID", "").partition(".")[0]
            version = f"p{major}" if major.isdigit() and len(major) <= 2 else "sisyphus"
    return OS_RELEASE_FAMILIES.get(release_id, release_id), version


def root_exec(container: str) -> list[str]:
    """podman exec as uid 0. A numeric user needs no lookup in the container's passwd,
    which podman can lose for a long-running container ("unable to find user root")."""
    return ["podman", "exec", "--user", "0:0", container]


def copy_into(runner: Runner, container: str, source, destination: str, timeout=60) -> None:
    """podman cp into a container, or with cp from /run/host when podman cp fails on a
    running container whose users podman can no longer look up."""
    try:
        runner.run(["podman", "cp", str(source), f"{container}:{destination}"], timeout=timeout)
    except CommandError as error:
        inside = f"/run/host{Path(source).resolve()}"
        try:
            runner.run([*root_exec(container), "cp", "--", inside, destination], timeout=timeout)
        except CommandError:
            raise error from None


def _copy_out(runner: Runner, container: str, source: str, path: Path) -> None:
    try:
        runner.run(["podman", "cp", f"{container}:{source}", str(path)], timeout=30)
    except CommandError as error:
        # Same podman failure as in copy_into; a stopped container keeps the first error.
        try:
            text = runner.run([*root_exec(container), "cat", "--", source], timeout=30)
        except CommandError:
            raise error from None
        path.write_text(text, encoding="utf-8")


def read_os_release(runner: Runner, container: str) -> dict[str, str]:
    # Works for stopped containers too, without starting or modifying them.
    with TemporaryDirectory(
        prefix="multipak-inspect-", dir=host.temporary_directory()
    ) as directory:
        path = Path(directory) / "os-release"
        try:
            _copy_out(runner, container, "/usr/lib/os-release", path)
        except CommandError:
            # Slackware, Pisi Linux and SliTaz keep the file only in /etc.
            path.unlink(missing_ok=True)
            _copy_out(runner, container, "/etc/os-release", path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError(
                tr(
                    "The environment does not contain a valid os-release file, or "
                    "the file is not a regular file."
                )
            )
        return parse_os_release(path.read_text(encoding="utf-8"))


def container_names(runner: Runner) -> tuple[str, ...]:
    """Every container name in use, created by Distrobox or not."""
    if not host.which("podman"):
        return ()
    rows = json.loads(runner.run(["podman", "ps", "--all", "--format", "json"], timeout=30))
    return tuple(name for row in rows for name in row.get("Names") or [])


def discover(
    runner: Runner, profiles: tuple[Environment, ...] = ENVIRONMENTS
) -> list[ExistingEnvironment]:
    if not host.which("podman"):
        return []
    rows = json.loads(
        runner.run(
            [
                "podman",
                "ps",
                "--all",
                "--filter",
                "label=manager=distrobox",
                "--format",
                "json",
            ],
            timeout=30,
        )
    )
    if not rows:
        return []
    images = json.loads(runner.run(["podman", "images", "--no-trunc", "--format", "json"]))
    image_names = {
        row.get("Id", row.get("ID", "")).removeprefix("sha256:"): tuple(row.get("Names") or [])
        for row in images
    }
    environments = []
    for row in rows:
        names = row.get("Names") or []
        if not names:
            continue
        name = names[0]
        try:
            data = json.loads(runner.run(["podman", "inspect", name], timeout=30))[0]
            labels = data.get("Config", {}).get("Labels") or {}
            if labels.get("manager") != "distrobox":
                continue
            # Do not share an owned container unless its home is marked as shared.
            if OWNER_LABEL in labels and labels.get(SHARED_LABEL) != "1":
                continue
            release = read_os_release(runner, name)
            family, version = release_identity(release)
            if family not in FAMILIES:
                continue
            image_id = data.get("Image", "").removeprefix("sha256:")
            if family == "void":
                # Void's os-release names no variant; the container's image does.
                version = void_variant((data.get("ImageName", ""), *image_names.get(image_id, ())))
            environments.append(
                ExistingEnvironment(
                    name,
                    data["Id"],
                    family,
                    version,
                    image_id,
                    image_names.get(image_id, ()),
                    labels.get(SHARED_LABEL) == "1",
                    custom_named=labels.get(CUSTOM_LABEL) == "1",
                    owned=OWNER_LABEL in labels,
                    full_variant=labels.get(FULL_LABEL) == "1",
                )
            )
        except Cancelled:
            raise
        except (CommandError, ValueError, OSError, TimeoutError):
            continue  # An unreadable environment is not guessed compatible.
    return resolve_floating_profiles(environments, images, profiles)


def resolve_floating_profiles(
    environments: list[ExistingEnvironment],
    images: list[dict],
    profiles: tuple[Environment, ...] = ENVIRONMENTS,
) -> list[ExistingEnvironment]:
    """Resolve floating tags using local image tags and actual container releases."""
    resolved = {}
    for profile in profiles:
        family, _, version = profile.id.partition("-")
        if version not in ("latest", "stable"):
            continue
        versions = {
            item.version
            for item in environments
            if item.family == family and item.version and profile.image in item.image_names
        }
        repository = profile.image.rpartition(":")[0]
        for row in images:
            names = row.get("Names") or []
            if profile.image not in names:
                continue
            for name in names:
                repo, _, tag = name.rpartition(":")
                if repo == repository and tag and all(part.isdigit() for part in tag.split(".")):
                    versions.add(tag)
        if len(versions) == 1:
            resolved[profile.id] = (family, versions.pop())
    return [
        replace(
            item,
            compatible_profiles=tuple(
                profile
                for profile, release in resolved.items()
                if release == (item.family, item.version)
            ),
        )
        for item in environments
    ]

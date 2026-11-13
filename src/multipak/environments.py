"""Read-only discovery of reusable Distrobox environments in the same Podman store."""

import json
import shlex
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from multipak.localization import tr

from . import host
from .models import ENVIRONMENTS, Environment, ExistingEnvironment
from .process import Cancelled, CommandError, Runner

OWNER_LABEL = "io.github.multipak.transaction"
SHARED_LABEL = "io.github.multipak.shared-environment"
CUSTOM_LABEL = "io.github.multipak.custom-name"


def parse_os_release(value: str) -> dict[str, str]:
    result = {}
    for line in value.splitlines():
        key, separator, raw = line.partition("=")
        if separator and key in ("ID", "VERSION_ID"):
            words = shlex.split(raw, comments=True)
            result[key] = words[0] if words else ""
    return result


def read_os_release(runner: Runner, container: str) -> dict[str, str]:
    # Works for stopped containers too, without starting or modifying them.
    with TemporaryDirectory(
        prefix="multipak-inspect-", dir=host.temporary_directory()
    ) as directory:
        path = Path(directory) / "os-release"
        runner.run(["podman", "cp", f"{container}:/usr/lib/os-release", str(path)], timeout=30)
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
            if release.get("ID") not in ("fedora", "ubuntu", "debian", "arch"):
                continue
            image_id = data.get("Image", "").removeprefix("sha256:")
            environments.append(
                ExistingEnvironment(
                    name,
                    data["Id"],
                    release["ID"],
                    release.get("VERSION_ID", ""),
                    image_id,
                    image_names.get(image_id, ()),
                    labels.get(SHARED_LABEL) == "1",
                    custom_named=labels.get(CUSTOM_LABEL) == "1",
                    owned=OWNER_LABEL in labels,
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

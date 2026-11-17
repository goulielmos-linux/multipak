"""The 32-bit Void Linux image: the official glibc image for linux/386, under a local name.

Void publishes its 64- and 32-bit glibc images under one tag. Pulling the 32-bit one by
its digest leaves that tag on the 64-bit image."""

import json
import re
from collections.abc import Callable

from multipak.localization import tr

from .container_progress import ImageProgress
from .models import VOID_ENVIRONMENT, VOID_REPOSITORY
from .slitaz_image import image_exists


def platform_digest(manifest: dict, architecture: str) -> str:
    """The digest of the image for one architecture in a manifest list."""
    for item in manifest.get("manifests") or ():
        platform = item.get("platform") or {}
        digest = item.get("digest", "")
        if (
            platform.get("os") == "linux"
            and platform.get("architecture") == architecture
            and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            return digest
    raise ValueError(tr("Void Linux publishes no 32-bit image."))


def ensure_image(
    runner,
    image: str,
    *,
    progress: Callable[[int, str], None] = lambda *_: None,
) -> None:
    """Pull the 32-bit image and name it locally, unless Podman already has it."""
    if image_exists(runner, image):
        return
    manifest = json.loads(
        runner.run(["podman", "manifest", "inspect", VOID_ENVIRONMENT.image], timeout=60)
    )
    reference = f"{VOID_REPOSITORY}@{platform_digest(manifest, '386')}"
    ImageProgress(progress).run(
        runner, ["podman", "pull", "--policy=missing", reference], terminal_output=True
    )
    runner.run(["podman", "tag", reference, image], timeout=60)

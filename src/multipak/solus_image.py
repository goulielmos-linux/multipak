"""The Solus container image: the unofficial base image, completed locally.

The base image skips Solus's setup scripts, so it lacks the user database and the
os-release links. MultiPak adds them from Solus's own baselayout in one small layer."""

import tempfile
from collections.abc import Callable
from pathlib import Path

from multipak.localization import tr

from .container_progress import ImageProgress
from .models import SOLUS_BASE_IMAGE
from .slitaz_image import image_exists

LABEL = "io.github.multipak.solus-base"
CONTAINERFILE = f"""\
FROM {SOLUS_BASE_IMAGE}
RUN for name in passwd group shadow gshadow; do \\
        [ -e /etc/$name ] || cp -p /usr/share/baselayout/$name /etc/$name; \\
    done \\
    && chmod 600 /etc/shadow /etc/gshadow \\
    && {{ [ -e /etc/os-release ] || ln -s ../usr/share/os-release/os-release /etc/os-release; }} \\
    && {{ [ -e /usr/lib/os-release ] || \\
        ln -s ../../usr/share/os-release/os-release /usr/lib/os-release; }}
LABEL {LABEL}={SOLUS_BASE_IMAGE}
CMD ["/bin/bash"]
"""


def ensure_image(
    runner,
    workspace: Path,
    image: str,
    *,
    progress: Callable[[int, str], None] = lambda *_: None,
) -> None:
    """Build the local Solus image unless Podman already has it."""
    if image_exists(runner, image):
        return
    ImageProgress(progress).run(
        runner,
        ["podman", "pull", "--policy=missing", SOLUS_BASE_IMAGE],
        terminal_output=True,
    )
    progress(-1, tr("Preparing the Solus image…"))
    # A folder Podman on the host can read, also from the Flatpak.
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="solus-image-", dir=workspace) as directory:
        containerfile = Path(directory) / "Containerfile"
        containerfile.write_text(CONTAINERFILE)
        runner.run(
            [
                "podman",
                "build",
                "--pull=never",
                "--network=none",
                "--tag",
                image,
                "--file",
                str(containerfile),
                directory,
            ],
            timeout=600,
        )

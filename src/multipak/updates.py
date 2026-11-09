"""Update one container through Distrobox, with output streamed by the worker."""

from multipak.localization import tr

from .models import valid_environment_name
from .process import CommandError


def upgrade_command(name: str) -> list[str]:
    if not valid_environment_name(name):
        raise ValueError(tr("Invalid container name."))
    return ["distrobox", "upgrade", name]


def update_container(engine, name: str) -> None:
    command = upgrade_command(name)
    engine.progress(-1, tr("Updating {0}… Please wait.").format(name))
    # Do not let Distrobox create a replacement for a container removed since listing.
    try:
        engine.runner.run(["podman", "container", "exists", name], timeout=30)
    except CommandError as exc:
        if exc.code == 1:
            raise RuntimeError(
                tr("The container is no longer available. Reload the list before updating.")
            ) from exc
        raise
    # Runner disables stdin interaction and fixes the backend to rootless Podman.
    # Keep the package manager's output and exit status; do not infer success from launch.
    engine.runner.run(command, timeout=7200)

"""Update one container through Distrobox, with output streamed by the worker."""

from multipak.localization import tr

from .container_progress import PackageProgress
from .models import valid_environment_name
from .process import CommandError


def upgrade_command(name: str) -> list[str]:
    if not valid_environment_name(name):
        raise ValueError(tr("Invalid container name."))
    return ["distrobox", "upgrade", name]


def update_container(engine, name: str) -> None:
    command = upgrade_command(name)
    with engine.store.lock():
        _update(engine, name, command)


def _update(engine, name, command):
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
    _check_identity(engine, name)
    # Runner disables stdin interaction and fixes the backend to rootless Podman.
    # Keep the package manager's output and exit status; do not infer success from launch.
    PackageProgress(engine.progress, updating=True).run(engine.runner, command, timeout=7200)


def _check_identity(engine, name):
    """A container recorded by MultiPak must still be the one that answers to its name."""
    from .containers import _read_record

    store = getattr(engine, "store", None)
    if store is None:
        return
    try:
        record = _read_record(store, name)
    except (OSError, TypeError, ValueError):
        record = None
    if record is None:
        return
    current = engine.runner.run(
        ["podman", "container", "inspect", "--format", "{{.Id}}", "--", name], timeout=30
    ).strip()
    if current != record.container_id:
        raise RuntimeError(
            tr(
                "Another container is now using the name {0}. It was not updated. "
                "Reload the list before updating."
            ).format(name)
        )

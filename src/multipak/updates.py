"""Update one container through Distrobox, with output streamed by the worker."""

from pathlib import Path

from multipak.localization import tr

from . import host
from .container_progress import PackageProgress
from .environments import copy_into, read_os_release
from .models import valid_environment_name
from .process import CommandError

# distrobox upgrade knows neither pisi nor tazpkg, and only syncs Gentoo's tree;
# MultiPak's setup scripts update Pisi Linux, SliTaz and Gentoo. On Void, Distrobox's
# xbps-install -Syu asks for confirmation and, without a terminal, aborts with status 0.
# Distrobox does not know Solus's eopkg either.
SCRIPTS = {
    "void": ("void-setup.sh", "/usr/libexec/multipak-void-setup.sh"),
    "pisilinux": ("pisi-setup.sh", "/usr/libexec/multipak-pisi-setup.sh"),
    "slitaz": ("slitaz-setup.sh", "/usr/libexec/multipak-slitaz-setup.sh"),
    "gentoo": ("gentoo-setup.sh", "/usr/libexec/multipak-gentoo-setup.sh"),
    # Solus has no /usr/libexec.
    "solus": ("solus-setup.sh", "/usr/lib/multipak-solus-setup.sh"),
}
PISI_SCRIPT = SCRIPTS["pisilinux"][1]


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
    family = _family(engine, name)
    if family in SCRIPTS:
        asset, target = SCRIPTS[family]
        script = Path(__file__).with_name("assets") / asset
        with host.accessible_file(script) as visible:
            copy_into(engine.runner, name, visible, target)
        command = [
            "distrobox",
            "enter",
            "--name",
            name,
            "--no-tty",
            "--no-workdir",
            "--",
            "sudo",
            "-n",
            "sh",
            target,
            "--upgrade",
        ]
    # Runner disables stdin interaction and fixes the backend to rootless Podman.
    # Keep the package manager's output and exit status; do not infer success from launch.
    PackageProgress(engine.progress, updating=True).run(engine.runner, command, timeout=7200)


def _family(engine, name) -> str:
    try:
        return read_os_release(engine.runner, name).get("ID", "")
    except (CommandError, ValueError, OSError, TimeoutError):
        return ""


def _check_identity(engine, name):
    """A container recorded by MultiPak must still be the one that answers to its name."""
    from .containers import read_record

    store = getattr(engine, "store", None)
    if store is None:
        return
    try:
        record = read_record(store, name)
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

"""Launch optional companion applications without personal installation paths."""

from PySide6.QtCore import QProcess

from multipak.localization import tr

from . import host
from .process import CommandError

KONTAINER_ID = "io.github.DenysMb.Kontainer"


def open_kontainer(runner):
    if executable := host.which("kontainer"):
        command = [executable]
    elif executable := host.which("flatpak"):
        try:
            runner.run([executable, "info", "--show-ref", KONTAINER_ID], timeout=15)
        except CommandError as exc:
            raise RuntimeError(tr("Kontainer was not found. Check that it is installed.")) from exc
        command = [executable, "run", KONTAINER_ID]
    else:
        raise RuntimeError(tr("Kontainer was not found. Check that it is installed."))
    # Detached applications must remain open when the manager is closed.
    invocation = host.command(command, watch_bus=False)
    result = QProcess.startDetached(invocation[0], invocation[1:])
    started = result[0] if isinstance(result, tuple) else result
    if not started:
        raise RuntimeError(tr("Could not open Kontainer."))

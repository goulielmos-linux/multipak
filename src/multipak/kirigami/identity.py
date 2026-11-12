"""Desktop identity shared by the native installer and manager."""

from pathlib import Path

from PySide6.QtGui import QIcon

from multipak import host
from multipak.localization import tr


def configure_application(app, *, manager=False):
    desktop_id = (
        (host.APP_ID if host.is_flatpak() else "io.github.multipak.manager")
        if manager
        else host.installer_desktop_id()
    )
    role = "manager" if manager else "installer"
    display = tr("Manager") if manager else tr("Installer")
    app.setApplicationName(desktop_id)
    app.setApplicationDisplayName(tr("Application {0} — MultiPak").format(display))
    app.setDesktopFileName(desktop_id)
    asset = Path(__file__).with_name("assets") / f"multipak-{role}.svg"
    icon_name = f"{host.APP_ID}.{role}" if host.is_flatpak() else f"io.github.multipak.{role}"
    app.setWindowIcon(QIcon.fromTheme(icon_name, QIcon(str(asset))))

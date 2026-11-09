"""Called by install_local.py using the newly installed Python environment."""

import shutil
import sys
from pathlib import Path

from multipak.desktop import quote_exec


def main() -> None:
    command, data, project = map(Path, sys.argv[1:])
    data = data.resolve()
    multipak_command = command.with_name("multipak-installer")
    desktop = data / "applications/io.github.multipak.installer.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    text = (project / "data/io.github.multipak.installer.desktop").read_text()
    text = text.replace(
        "Exec=multipak-installer %f", f"Exec={quote_exec(str(multipak_command))} %f"
    )
    icons = {}
    for role in ("installer", "manager"):
        multipak_icon = data / f"icons/hicolor/scalable/apps/io.github.multipak.{role}.svg"
        multipak_icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            project / f"src/multipak/kirigami/assets/multipak-{role}.svg", multipak_icon
        )
        launcher_icon = data / f"icons/hicolor/256x256/apps/io.github.multipak.{role}.png"
        launcher_icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            project / f"src/multipak/kirigami/assets/multipak-{role}.png", launcher_icon
        )
        icons[role] = launcher_icon
    # Local launchers use the installed file, independent of the icon theme cache.
    text = text.replace("Icon=io.github.multipak.installer", f"Icon={icons['installer']}")
    desktop.write_text(text)
    mime = data / "mime/packages/io.github.multipak.packages.xml"
    mime.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / "src/multipak/assets/multipak-packages.xml", mime)
    manager = (project / "data/io.github.multipak.manager.desktop").read_text()
    manager_command = command.with_name("multipak-manager")
    manager = manager.replace("Exec=multipak-manager", f"Exec={quote_exec(str(manager_command))}")
    manager = manager.replace("Icon=io.github.multipak.manager", f"Icon={icons['manager']}")
    (desktop.parent / "io.github.multipak.manager.desktop").write_text(manager)


if __name__ == "__main__":
    main()

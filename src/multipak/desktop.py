"""Namespaced desktop integration, without overwriting an existing application."""

import base64
import configparser
import io

from multipak.localization import tr

from . import host
from .models import Transaction
from .store import Store


def quote_exec(value: str) -> str:
    """Escape two layers: Exec argument quoting, then desktop string escaping."""
    if any(c in value for c in ("\n", "\r", "\0")):
        raise ValueError(tr("Invalid shortcut argument."))
    escaped = "".join("\\" + c if c in '\\"`$' else c for c in value)
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def render_desktop(content: str, container: str, executable: str) -> str | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(content)
    if "Desktop Entry" not in parser:
        return None
    main = parser["Desktop Entry"]
    if main.get("Type") != "Application" or main.get("Hidden", "false").lower() == "true":
        return None
    if main.get("NoDisplay", "false").lower() == "true" or not main.get("Exec"):
        return None
    prefix = " ".join(
        quote_exec(x)
        for x in (
            executable,
            "enter",
            "--name",
            container,
            "--no-tty",
            "--no-workdir",
            "--",
        )
    )
    for section in parser.sections():
        entry = parser[section]
        if section != "Desktop Entry" and not section.startswith("Desktop Action "):
            continue
        if "Exec" in entry:
            entry["Exec"] = prefix + " " + entry["Exec"]
        # These refer to the guest environment, and cannot be checked on the host.
        for key in ("TryExec", "Path", "OnlyShowIn", "NotShowIn"):
            entry.pop(key, None)
        # Container icon paths are not host paths. A theme icon can still resolve on host.
        if entry.get("Icon", "").startswith("/"):
            entry["Icon"] = "application-x-executable"
    main["DBusActivatable"] = "false"
    main["X-MultiPak-Container"] = container
    stream = io.StringIO()
    parser.write(stream, space_around_delimiters=False)
    return stream.getvalue()


def export_desktops(store: Store, tx: Transaction, desktops: list[dict]) -> int:
    executable = host.which("distrobox")
    if not executable:
        raise RuntimeError(tr("Distrobox was not found."))
    count = 0
    for index, entry in enumerate(desktops):
        content = base64.b64decode(entry["content"], validate=True).decode("utf-8")
        rendered = render_desktop(content, tx.container, executable)
        if rendered is None:
            continue
        name = f"multipak-{tx.id}-{index}.desktop"
        path = store.launcher(tx, name)
        if name not in tx.launchers:
            tx.launchers.append(name)
            store.save(tx)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
        count += 1
    return count


def register_container_icon(store: Store, tx: Transaction) -> None:
    """Kontainer reads each container's Icon from <container>.desktop on the host."""
    from pathlib import Path

    from .models import valid_environment_name

    if (
        tx.custom_container_name
        or not tx.container_created
        or not tx.container_id
        or not valid_environment_name(tx.container)
    ):
        return
    path = store.applications / f"{tx.container}.desktop"
    # Respect shortcuts/icons already created or customized outside this operation.
    if path.exists() or path.is_symlink():
        return
    executable = host.which("distrobox")
    if not executable:
        return
    # Kontainer's Flatpak explicitly permits this Distrobox icon directory.
    directory = store.applications.parent / "icons" / "distrobox"
    directory.mkdir(parents=True, exist_ok=True)
    logo = directory / "multipak-environment.svg"
    if not logo.exists():
        try:
            with logo.open("xb") as stream:
                stream.write(
                    (Path(__file__).with_name("kirigami") / "assets" / "container.svg").read_bytes()
                )
        except FileExistsError:
            pass
    title = tx.container
    if title.startswith("MultiPak-"):
        title = "MultiPak - " + title.removeprefix("MultiPak-").replace("-", " ")
    text = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={title}\n"
        f"Exec={quote_exec(executable)} enter --name {quote_exec(tx.container)}\n"
        f"Icon={logo}\nTerminal=true\nNoDisplay=true\n"
        "X-MultiPak-ContainerIcon=true\n"
        f"X-MultiPak-ContainerID={tx.container_id}\n"
    )
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        pass


def remove_container_icon(store: Store, tx: Transaction) -> None:
    from .models import valid_environment_name

    if (
        tx.custom_container_name
        or not tx.container_created
        or not tx.container_id
        or not valid_environment_name(tx.container)
    ):
        return
    path = store.applications / f"{tx.container}.desktop"
    if not path.is_file() or path.is_symlink():
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (configparser.Error, UnicodeError):
        return
    if (
        parser.get("Desktop Entry", "X-MultiPak-ContainerIcon", fallback="") == "true"
        and parser.get("Desktop Entry", "X-MultiPak-ContainerID", fallback="") == tx.container_id
    ):
        path.unlink()

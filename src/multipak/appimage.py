"""MultiPak's own AppImage reader and desktop integration, based on the format specification."""

import hashlib
import io
import logging
import os
import platform
import re
import shlex
import shutil
import stat
import struct
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from multipak.localization import current_language, tr

from . import host
from .desktop import parse_desktop, quote_exec
from .models import Package, Transaction
from .process import CommandError
from .storage import record_install_failure

LOG = logging.getLogger(__name__)

MAX_METADATA = 1024 * 1024
MAX_ICON = 4 * 1024 * 1024


def _member(name):
    value = PurePosixPath(name)
    if (
        value.is_absolute()
        or (value.parts and value.parts[0].startswith("-"))
        or ".." in value.parts
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError(tr("Invalid internal AppImage path."))
    return str(value)


def elf_info(path):
    """Read the ELF tables; never invoke the embedded runtime to discover metadata."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
        if (
            len(header) < 52
            or header[:4] != b"\x7fELF"
            or header[8:11] not in (b"AI\x01", b"AI\x02")
        ):
            raise ValueError(tr("The file is not a valid type 1 or type 2 AppImage."))
        if header[4] not in (1, 2) or header[5] not in (1, 2):
            raise ValueError(tr("Invalid ELF header."))
        endian = "<" if header[5] == 1 else ">"
        machine = struct.unpack_from(endian + "H", header, 18)[0]
        architecture = {62: "x86_64", 183: "aarch64", 3: "i686", 40: "armv7l"}.get(machine)
        if not architecture or architecture != platform.machine():
            raise ValueError(
                tr("Architecture {0} is incompatible with {1}.").format(
                    architecture or machine, platform.machine()
                )
            )
        kind = header[10]
        if kind == 1:
            return kind, architecture, 0
        if header[4] == 2:
            offset = struct.unpack_from(endian + "Q", header, 40)[0]
            entry, count = struct.unpack_from(endian + "HH", header, 58)
        else:
            offset = struct.unpack_from(endian + "I", header, 32)[0]
            entry, count = struct.unpack_from(endian + "HH", header, 46)
        end = offset + entry * count
        if not offset or not count or not entry or end < len(header) or end >= size:
            raise ValueError(tr("Could not locate the AppImage contents."))
        stream.seek(end)
        # Some runtimes align their payload after the ELF section table.
        padding = stream.read(min(65536, size - end))
        position = padding.find(b"hsqs")
        if position < 0 or any(padding[:position]):
            raise ValueError(
                tr("This AppImage does not contain a recognized type 2 SquashFS payload.")
            )
        return kind, architecture, end + position


class Archive:
    def __init__(self, path, runner):
        self.path, self.runner = Path(path), runner
        self.kind, self.architecture, self.offset = elf_info(self.path)
        names = ("unsquashfs",) if self.kind == 2 else ("7zz", "7z", "bsdtar")
        self.tool = next((shutil.which(name) for name in names if shutil.which(name)), None)
        if not self.tool:
            raise RuntimeError(
                tr("Could not find unsquashfs to read the type 2 AppImage.")
                if self.kind == 2
                else tr("Could not find 7-Zip or bsdtar to read the type 1 AppImage.")
            )

    def names(self):
        if self.kind == 2:
            output = self.runner.run(
                [self.tool, "-o", str(self.offset), "-l", str(self.path)], timeout=30
            )
            rows = [
                line.removeprefix("squashfs-root/")
                for line in output.splitlines()
                if line.startswith("squashfs-root/")
            ]
        elif Path(self.tool).name == "bsdtar":
            rows = self.runner.run([self.tool, "-tf", str(self.path)], timeout=30).splitlines()
        else:
            output = self.runner.run([self.tool, "l", "-slt", "-tiso", str(self.path)], timeout=30)
            rows = [
                line[7:]
                for line in output.partition("----------")[2].splitlines()
                if line.startswith("Path = ")
            ]
        return [_member(name) for name in rows if name and name != "."]

    def listing(self):
        """Type ("-" file, "d", "l", other) and size of each member that can be parsed.

        Used to stay within size limits before extracting. Unparsed members are omitted.
        """
        entries = {}
        if self.kind == 2:
            output = self.runner.run(
                [self.tool, "-o", str(self.offset), "-lls", str(self.path)], timeout=30
            )
            pattern = re.compile(
                r"^([-dlcbps])\S{9}\s+\S+\s+([0-9]+)\s+\d{4}-\d\d-\d\d \d\d:\d\d "
                r"squashfs-root/(.+)$"
            )
            for line in output.splitlines():
                match = pattern.match(line)
                if not match:
                    continue
                kind, size, name = match[1], int(match[2]), match[3]
                if kind == "l":
                    name = name.partition(" -> ")[0]
                entries[name] = (kind, size)
        elif Path(self.tool).name == "bsdtar":
            output = self.runner.run([self.tool, "-tvf", str(self.path)], timeout=30)
            pattern = re.compile(
                r"^([-dlcbps])\S{9}\s+\S+\s+\S+\s+\S+\s+([0-9]+)\s+\w{3}\s+\d+\s+"
                r"(?:\d{4}|\d\d:\d\d)\s+(.+)$"
            )
            for line in output.splitlines():
                if match := pattern.match(line):
                    name = match[3].partition(" -> ")[0] if match[1] == "l" else match[3]
                    entries[name] = (match[1], int(match[2]))
        else:
            output = self.runner.run([self.tool, "l", "-slt", "-tiso", str(self.path)], timeout=30)
            for block in output.partition("----------")[2].split("\n\n"):
                fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
                if "Path" in fields and fields.get("Size", "").isdigit():
                    kind = "d" if fields.get("Folder") == "+" else "-"
                    entries[fields["Path"]] = (kind, int(fields["Size"]))
        result = {}
        for name, value in entries.items():
            try:
                result[_member(name)] = value
            except ValueError:
                continue
        return result

    def read(self, name, limit=MAX_METADATA):
        name = _member(name)
        if self.kind == 2:
            argv = [self.tool, "-o", str(self.offset), "-cat", str(self.path), name]
        elif Path(self.tool).name == "bsdtar":
            argv = [self.tool, "-xOf", str(self.path), name]
        else:
            argv = [self.tool, "x", "-so", "-tiso", str(self.path), name]
        return self.runner.read_bytes(argv, limit=limit)

    def extract_paths(self, destination, names):
        """Extract only the named members, for reading. Links are extracted as links."""
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise ValueError(tr("The extraction directory already exists."))
        names = [_member(name) for name in names]
        if not names:
            destination.mkdir()
            return
        if self.kind == 2:
            command = [
                self.tool,
                "-o",
                str(self.offset),
                "-d",
                str(destination),
                "-no-xattrs",
                "-no-progress",
                "-processors",
                "2",
                "-mem",
                "64M",
                str(self.path),
                *names,
            ]
        elif Path(self.tool).name == "bsdtar":
            destination.mkdir()
            command = [
                self.tool,
                "-xf",
                str(self.path),
                "-C",
                str(destination),
                "--no-same-owner",
                "--no-same-permissions",
                "--",
                *names,
            ]
        else:
            command = [
                self.tool,
                "x",
                "-tiso",
                "-y",
                f"-o{destination}",
                str(self.path),
                "--",
                *names,
            ]
        self.runner.run(command, timeout=600)
        destination.mkdir(exist_ok=True)

    def extract(self, destination):
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise ValueError(tr("The extraction directory already exists."))
        if self.kind == 2:
            command = [
                self.tool,
                "-o",
                str(self.offset),
                "-d",
                str(destination),
                "-no-xattrs",
                "-no-progress",
                "-processors",
                "2",
                "-mem",
                "64M",
                str(self.path),
            ]
        elif Path(self.tool).name == "bsdtar":
            destination.mkdir()
            command = [
                self.tool,
                "-xf",
                str(self.path),
                "-C",
                str(destination),
                "--no-same-owner",
                "--no-same-permissions",
            ]
        else:
            command = [self.tool, "x", "-tiso", "-y", f"-o{destination}", str(self.path)]
        self.runner.run(command, timeout=600)
        entry = destination / "AppRun"
        if not entry.is_file() or not entry.resolve().is_relative_to(destination.resolve()):
            raise ValueError(tr("The extracted AppImage does not contain a valid AppRun."))
        entry.chmod(entry.stat().st_mode | 0o100)


def inspect_appimage(path, runner):
    archive = Archive(path, runner)
    names = archive.names()
    desktops = sorted(name for name in names if "/" not in name and name.endswith(".desktop"))
    if len(desktops) != 1:
        raise ValueError(tr("The AppImage must contain one .desktop file at its root."))
    text = archive.read(desktops[0]).decode("utf-8")
    parser = parse_desktop(text)
    entry = parser["Desktop Entry"]
    if entry.get("Type") != "Application":
        raise ValueError(tr("The AppImage does not contain application metadata."))
    language = current_language()
    name = (
        entry.get(f"Name[{language}]")
        or entry.get(f"Name[{language.split('_')[0]}]")
        or entry.get("Name")
    )
    if not name or len(name) > 256 or any(ord(c) < 32 for c in name):
        raise ValueError(tr("Invalid application name in the AppImage."))
    version = entry.get("X-AppImage-Version", "Not provided")
    if not version or len(version) > 128 or any(ord(c) < 32 for c in version):
        version = "Not provided"
    icon_name = entry.get("Icon", "")
    icon_stem = PurePosixPath(icon_name).stem
    candidates = sorted(
        (
            item
            for item in names
            if PurePosixPath(item).stem == icon_stem and item.endswith((".svg", ".png"))
        ),
        key=lambda item: (not item.endswith(".svg"), "hicolor" not in item, len(item)),
    )
    icon, suffix = b"", ""
    for candidate in candidates[:8] + ([".DirIcon"] if ".DirIcon" in names else []):
        try:
            data = archive.read(candidate, MAX_ICON)
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                icon, suffix = data, ".png"
            elif b"<svg" in data[:1024]:
                icon, suffix = data, ".svg"
            if icon:
                break
        except (CommandError, ValueError, OSError):
            continue
    return Package(
        path,
        "appimage",
        name,
        version,
        archive.architecture,
        desktop_entry=text,
        icon_data=icon,
        icon_extension=suffix,
        appimage_type=archive.kind,
    )


def _desktop(store, tx, package):
    directory = store.appimage_directory(tx.id)
    icon = "application-x-executable"
    if package.icon_data and package.icon_extension in (".svg", ".png"):
        path = directory / ("icon" + package.icon_extension)
        path.write_bytes(package.icon_data)
        icon = str(path)
    parser = parse_desktop(package.desktop_entry)
    entry = parser["Desktop Entry"]
    launcher = Path(__file__).with_name("appimage_launcher.py")
    invocation = (
        ["/usr/bin/flatpak", "run", "--command=multipak-appimage", host.APP_ID]
        if host.is_flatpak()
        else ["/usr/bin/python3", str(launcher)]
    )
    for section in list(parser.sections()):
        if section != "Desktop Entry" and not section.startswith("Desktop Action "):
            parser.remove_section(section)
            continue
        current = parser[section]
        tokens = shlex.split(current.get("Exec", "AppRun"))
        environment = []
        if tokens and tokens[0] == "env":
            tokens.pop(0)
            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
                environment.append(tokens.pop(0))
        prefix = (
            "env " + " ".join(quote_exec(value) for value in environment) + " "
            if environment and not host.is_flatpak()
            else ""
        )
        command_args = list(invocation)
        if host.is_flatpak():
            for value in environment:
                command_args.extend(("--setenv", value))
            command_args.append("--")
        command_args.append(tx.id)
        command = " ".join(quote_exec(value) for value in command_args)
        arguments = []
        for word in tokens[1:]:
            if word in ("%f", "%F", "%u", "%U"):
                arguments.append(word)
            elif word == "%i":
                arguments.extend((quote_exec("--icon"), quote_exec(icon)))
            elif word == "%c":
                arguments.append(quote_exec(package.name))
            elif word != "%k":
                arguments.append(quote_exec(word.replace("%%", "%")))
        current["Exec"] = prefix + command + (" " + " ".join(arguments) if arguments else "")
        current["Icon"] = icon
        for key in ("TryExec", "Path", "OnlyShowIn", "NotShowIn"):
            current.pop(key, None)
    entry["Icon"], entry["DBusActivatable"] = icon, "false"
    entry["Hidden"], entry["NoDisplay"] = "false", "false"
    entry["X-MultiPak-Method"] = "appimage"
    text = io.StringIO()
    parser.write(text, space_around_delimiters=False)
    filename = f"multipak-{tx.id}-appimage.desktop"
    if filename not in tx.launchers:
        tx.launchers.append(filename)
        store.save(tx)
    target = store.launcher(tx, filename)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(text.getvalue(), encoding="utf-8")
    temporary.replace(target)


def migrate_legacy_launchers(store):
    """Retarget AppImage shortcuts written by the Alpha Flatpak."""
    prefix = re.compile(
        r'^(Exec[ \t]*=[ \t]*(?:"/usr/bin/flatpak"|/usr/bin/flatpak)[ \t]+'
        r'(?:"run"|run)[ \t]+(?:"--command=multipak-appimage"|--command=multipak-appimage)[ \t]+)'
        r'("?)' + re.escape(host.LEGACY_APP_ID) + r"\2(?=[ \t\r\n]|$)"
    )
    with store.lock():
        for tx in store.transactions():
            if tx.method != "appimage" or not isinstance(tx.launchers, list):
                continue
            for name in tx.launchers:
                if not isinstance(name, str):
                    continue
                try:
                    path = store.launcher(tx, name)
                except ValueError:
                    continue
                try:
                    attrs = path.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(attrs.st_mode):
                    continue
                try:
                    original = path.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    LOG.warning("Skipping an unreadable AppImage shortcut: %s", name)
                    continue
                section = ""
                lines = []
                for line in original.splitlines(keepends=True):
                    content = line.strip()
                    if content.startswith("[") and content.endswith("]"):
                        section = content[1:-1]
                    if section == "Desktop Entry" or section.startswith("Desktop Action "):
                        line = prefix.sub(
                            lambda match: f"{match[1]}{match[2]}{host.APP_ID}{match[2]}",
                            line,
                            count=1,
                        )
                    lines.append(line)
                migrated = "".join(lines)
                if migrated == original:
                    continue
                with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(migrated.encode("utf-8"))
                        stream.flush()
                        os.fchmod(stream.fileno(), stat.S_IMODE(attrs.st_mode))
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)


def _complete(engine, package):
    tx = engine.tx
    tx.warnings.clear()
    directory = engine.store.appimage_directory(tx.id)
    application = directory / "application.AppImage"
    application.chmod(0o755)
    engine._stage(45, tr("Preparing the AppImage…"))
    # Integration never executes code supplied by the application. Extraction
    # also avoids depending on a particular runtime's FUSE implementation.
    engine._stage(60, tr("Extracting the application so it can run…"))
    destination = directory / "AppDir"
    if destination.is_symlink():
        raise ValueError(tr("The application directory must not be a symbolic link."))
    if destination.exists():
        shutil.rmtree(destination)
    Archive(application, engine.runner).extract(destination)
    container = None
    if tx.appimage_container:
        from .appimage_container import ensure_container, install_packages

        engine._stage(70, tr("Preparing the container for the application…"))
        container = ensure_container(engine)
        tx.container, tx.container_id = container.name, container.container_id
        engine.store.save(tx)
        engine._stage(80, tr("Installing the dependencies in the container…"))
        unavailable = install_packages(engine, container, tx.appimage_packages)
        if unavailable:
            tx.warnings.append(
                tr("These dependencies could not be installed automatically: {0}.").format(
                    ", ".join(unavailable)
                )
            )
        tx.appimage_mode = "container"
    else:
        tx.appimage_mode = "extracted"
    engine._stage(90, tr("Adding the application to the application launcher…"))
    _desktop(engine.store, tx, package)
    if container is not None and tx.appimage_verify:
        from .appimage_container import verify_and_complete

        engine._stage(95, tr("Checking the application in an isolated environment…"))
        try:
            tx.warnings.extend(verify_and_complete(engine, container, destination, tx.id))
        except (CommandError, OSError, RuntimeError, TimeoutError, ValueError) as error:
            # The application is installed; an incomplete check must not undo that.
            tx.warnings.append(tr("The isolated check could not be completed: {0}").format(error))
    tx.status, tx.error = "installed", ""
    engine.store.save(tx)
    engine.progress(100, tr("Installation completed."))
    return tx


def install(engine, package, *, container=False, packages=(), verify=False):
    """container: install packages in MultiPak's AppImage container and run the
    application there; verify: afterwards, start it briefly in an isolated copy."""
    with engine.store.lock():
        tx = Transaction(
            uuid.uuid4().hex,
            "",
            "",
            package.name,
            package.version,
            "appimage",
            "",
            method="appimage",
            container_created=False,
            appimage_container=bool(container),
            appimage_packages=list(packages) if container else [],
            appimage_verify=bool(container and verify),
        )
        engine.tx = tx
        try:
            engine.store.save(tx)
            engine._stage(5, tr("Copying the AppImage…"))
            directory = engine.store.appimage_directory(tx.id)
            directory.mkdir(parents=True, mode=0o700)
            target = directory / "application.AppImage"
            engine._copy_package(package.path, target)
            with target.open("rb") as stream:
                tx.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            engine.store.save(tx)
            copied = inspect_appimage(target, engine.runner)
            if (copied.name, copied.version, copied.architecture) != (
                package.name,
                package.version,
                package.architecture,
            ):
                raise ValueError(tr("The file has changed. Open the AppImage again."))
            return _complete(engine, copied)
        except Exception as error:
            reported = record_install_failure(engine.store, tx, error)
            if reported is not error:
                raise reported from error
            raise


def retry(engine, tx):
    with engine.store.lock():
        engine.tx = tx
        try:
            target = engine.store.appimage_directory(tx.id) / "application.AppImage"
            if target.is_symlink():
                raise ValueError(tr("The managed AppImage must not be a symbolic link."))
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if not tx.sha256 or digest != tx.sha256:
                raise ValueError(tr("The AppImage copy has changed. Open the file again."))
            return _complete(engine, inspect_appimage(target, engine.runner))
        except Exception as error:
            reported = record_install_failure(engine.store, tx, error)
            if reported is not error:
                raise reported from error
            raise


def cleanup(engine, tx):
    with engine.store.lock():
        engine.tx = tx
        try:
            for name in tx.launchers:
                engine.store.launcher(tx, name).unlink(missing_ok=True)
                engine.store.launcher(tx, name).with_suffix(".tmp").unlink(missing_ok=True)
            application = engine.store.appimage_directory(tx.id)
            if application.exists():
                shutil.rmtree(application)
            engine.store.remove_directory(tx)
            engine.tx = None
        except Exception as error:
            tx.status, tx.error = "cleanup_failed", str(error)
            engine.store.save(tx)
            raise


def launch(transaction_id, arguments, extra_environment=None):
    from .store import Store

    store = Store()
    tx = next((item for item in store.transactions() if item.id == transaction_id), None)
    if tx is None or tx.method != "appimage" or tx.status != "installed":
        raise RuntimeError(tr("This AppImage installation is unavailable."))
    directory = store.appimage_directory(tx.id)
    application = directory / "application.AppImage"
    if application.is_symlink() or not application.is_file():
        raise RuntimeError(tr("The managed AppImage file is unavailable."))
    if tx.appimage_mode == "direct":
        if host.is_flatpak():
            invocation = host.command(
                [str(application), *arguments], environment=extra_environment, watch_bus=False
            )
            os.execv(invocation[0], invocation)
        os.execv(str(application), [str(application), *arguments])
    elif tx.appimage_mode == "extracted":
        appdir = directory / "AppDir"
        entry = appdir / "AppRun"
        if (
            appdir.is_symlink()
            or not entry.is_file()
            or not entry.resolve().is_relative_to(appdir.resolve())
        ):
            raise RuntimeError(tr("The application entry point is unavailable."))
        environment = os.environ | {
            "APPIMAGE": str(application),
            "APPDIR": str(appdir),
            "OWD": os.getcwd(),
            "ARGV0": str(application),
        }
        environment.update(extra_environment or {})
        if host.is_flatpak():
            overrides = {key: environment[key] for key in ("APPIMAGE", "APPDIR", "OWD", "ARGV0")}
            overrides.update(extra_environment or {})
            invocation = host.command(
                [str(entry), *arguments], environment=overrides, watch_bus=False
            )
            os.execv(invocation[0], invocation)
        os.execve(str(entry), [str(entry), *arguments], environment)
    elif tx.appimage_mode == "container":
        from .appimage_container import launch_command

        appdir = directory / "AppDir"
        entry = appdir / "AppRun"
        if (
            appdir.is_symlink()
            or not entry.is_file()
            or not entry.resolve().is_relative_to(appdir.resolve())
            or not re.fullmatch(r"[0-9a-f]{64}", tx.container_id)
        ):
            raise RuntimeError(tr("The application entry point is unavailable."))
        # The name must still lead to the recorded container: never let Distrobox create
        # a replacement, and never open the application in another container that has
        # taken the name.
        try:
            result = host.run(
                ["podman", "container", "inspect", "--format", "{{.Id}}", "--", tx.container],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            current = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            current = ""
        if not current:
            raise RuntimeError(
                tr("The container for this AppImage is unavailable. Install the AppImage again.")
            )
        if current != tx.container_id:
            raise RuntimeError(
                tr("The AppImage container identity or ownership has changed. It was not used.")
            )
        environment = {
            "APPIMAGE": str(application),
            "APPDIR": str(appdir),
            "OWD": os.getcwd(),
            "ARGV0": str(application),
            **(extra_environment or {}),
        }
        argv = launch_command(tx.container, entry, arguments, environment)
        if host.is_flatpak():
            invocation = host.command(argv, watch_bus=False)
            os.execv(invocation[0], invocation)
        executable = shutil.which("distrobox")
        if not executable:
            raise RuntimeError(tr("Distrobox was not found."))
        os.execv(executable, [executable, *argv[1:]])
    else:
        raise RuntimeError(tr("Invalid AppImage launch method."))

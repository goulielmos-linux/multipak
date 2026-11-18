# SPDX-License-Identifier: GPL-3.0-or-later
"""Sending files between Linux and Android, like KDE Connect.

Linux → Android: the owner writes the file into a pipe; root has Android save it in Download.
Android → Linux: the helper app leaves shared files in files/sent; root checks them and the
owner's session copies them to Downloads."""

import errno
import fcntl
import itertools
import logging
import os
import re
import select
import stat
import threading
import unicodedata

import multipak_outbox as outbox
from multipak_input import MAPPER as HELPER
from multipak_instance import current as instance

SENT = ("data", HELPER, "files", "sent")
SENT_NAME = re.compile(r"[0-9]{1,20}-[0-9a-f]{1,64}-(.+)", re.S)
DOWNLOAD = "/mnt/user/0/emulated/0/Download"  # Android's storage view, as the apps see it.
DOWNLOAD_DATA = ("media", "0", "Download")  # The same folder below Android's data.
# Android's shell user with the media_rw group: without the group, the storage view refuses it.
SHELL, MEDIA_RW = "2000", "1023"
NAME_BYTES = 255
MAX_TAKE = 16  # Files the helper keeps waiting at most.
RESERVE = 1024**3  # Space left free in Downloads.
CHUNK = 1024 * 1024
# Writes the pipe to a new file ($1, never replacing one) and keeps it only when it has the
# announced size ($2); a cancelled send ends the pipe early.
SCRIPT = (
    "PATH=/system/bin; set -C; exec 3>\"$1\" || exit 2; "
    "if cat >&3 && exec 3>&- && [ $(wc -c <\"$1\") -eq \"$2\" ]; then exit 0; fi; "
    "rm -f \"$1\"; exit 1"
)



def scan_command(name):
    """Android's command that lists one sent file for the apps and galleries: what the shell
    user writes is not listed by itself (as with "adb push")."""
    return ("/system/bin/content", "call", "--uri", "content://media/", "--method", "scan_file",
            "--arg", "/storage/emulated/0/Download/" + check_name(name))


TEXTS = {
    "en": {
        "off": "Android is off. Start a session to send files.",
        "folder": "Only files can be sent: {name} is a folder.",
        "sending": "Sending to {label}",
        "not_sent": "Not sent to {label}",
        "file": "File",
        "sent": "{name} is in the Download folder of {label}.",
        "done": "Sent to {label}",
        "failed": "Could not send {name}: {error}",
        "received": "Received from Android",
        "saved": "{name} is in {folder}.",
        "not_received": "Could not receive {name} from Android: {error}",
        "menu": "Send to Android",
    },
    "pt": {
        "off": "O Android está desligado. Inicie uma sessão para enviar arquivos.",
        "folder": "Só arquivos podem ser enviados: {name} é uma pasta.",
        "sending": "Enviando para {label}",
        "not_sent": "Não enviado para {label}",
        "file": "Arquivo",
        "sent": "{name} está na pasta Download de {label}.",
        "done": "Enviado para {label}",
        "failed": "Não foi possível enviar {name}: {error}",
        "received": "Recebido do Android",
        "saved": "{name} está em {folder}.",
        "not_received": "Não foi possível receber {name} do Android: {error}",
        "menu": "Enviar para o Android",
    },
}
MENU_NAMES = {texts["menu"] for texts in TEXTS.values()}
# The Waydroid logo in one color: KDE paints "-symbolic" icons in the theme's text color.
ICON = "multipak-android-symbolic"


def text(key, **values):
    import multipak_desktop

    language = (multipak_desktop.desktop_locale() or "en").partition("-")[0]
    return TEXTS.get(language, TEXTS["en"])[key].format(**values)


def check_name(name):
    """Return a plain file name (no folders, control characters or empty names), or
    raise ValueError."""
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:
        raise ValueError("Invalid file name")
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in name):
        raise ValueError("File names must not contain control characters")
    if len(name.encode("utf-8")) > NAME_BYTES:
        raise ValueError("File name too long")
    return name


def numbered(name, number):
    """name (2).ext, shortened to stay a valid file name."""
    stem, extension = os.path.splitext(name)
    while stem and len(f"{stem} ({number}){extension}".encode("utf-8")) > NAME_BYTES:
        stem = stem[:-1]
    return f"{stem} ({number}){extension}"


def free_name(name, taken):
    """name, or the first name (n).ext not in taken."""
    if name not in taken:
        return name
    return next(n for n in (numbered(name, i) for i in itertools.count(1)) if n not in taken)


# Root service.


def _listing(parts):
    data = os.open(instance().data, outbox.DIRECTORY)
    try:
        folder = outbox._descend(data, parts)
    finally:
        os.close(data)
    try:
        return set(os.listdir(folder))
    finally:
        os.close(folder)


def check_pipe(descriptor):
    """The owner's end must be a pipe Android can read from; reopening it is never needed."""
    info = os.fstat(descriptor)
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if not stat.S_ISFIFO(info.st_mode) or flags & os.O_PATH or flags & os.O_ACCMODE == os.O_WRONLY:
        raise ValueError("Files reach Android through a pipe")


def send(descriptor, name, size, attach, run):
    """Write what arrives on the pipe to Android's Download folder; return the name used.

    attach is the lxc-attach command for this environment and run is subprocess.run."""
    name = check_name(name)
    if type(size) is not int or not 0 <= size < 2**63:
        raise ValueError("Invalid file size")
    check_pipe(descriptor)
    chosen = free_name(name, _listing(DOWNLOAD_DATA))
    command = [*attach, "-u", SHELL, "-g", MEDIA_RW, "--",
               "/system/bin/sh", "-c", SCRIPT, "sh", f"{DOWNLOAD}/{chosen}", str(size)]
    result = run(command, stdin=descriptor, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RuntimeError("Android did not receive the whole file" + (": " + detail if detail else ""))
    return chosen


def _open_sent(folder, name, uid):
    descriptor = os.open(name, outbox.FILE, dir_fd=folder)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
            raise ValueError("Not a file the MultiPak helper wrote")
        os.set_blocking(descriptor, True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def take():
    """The files the helper left in files/sent, checked and deleted: [(name, open file)]."""
    try:
        data = os.open(instance().data, outbox.DIRECTORY)
    except OSError:
        return []
    try:
        uid = outbox.helper_uid(data)
        folder = outbox._descend(data, SENT)
    except FileNotFoundError:
        return []  # Nothing sent yet.
    except (OSError, ValueError) as error:
        logging.warning("Files from Android refused: %s", error)
        return []
    finally:
        os.close(data)
    found = []
    try:
        with os.scandir(folder) as entries:
            names = sorted(entry.name for entry in itertools.islice(entries, MAX_TAKE))
        for name in names:
            try:
                match = SENT_NAME.fullmatch(name)
                if match:
                    shown = check_name(match[1])
                    found.append((shown, _open_sent(folder, name, uid)))
            except Exception as error:  # Anything Android wrote may be malformed.
                logging.warning("Ignoring a file from Android: %s", error)
            try:
                os.unlink(name, dir_fd=folder)
            except OSError as error:
                logging.warning("Could not remove a file from Android: %s", error)
    finally:
        os.close(folder)
    return found


def watch(notify):
    return outbox.monitor(SENT, notify)


# The owner's session and command line.


def notify(summary, body, replaces=0):
    """A desktop notification; returns its number, or 0 when there is no notification server."""
    import dbus

    try:
        server = dbus.Interface(
            dbus.SessionBus().get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications"),
            "org.freedesktop.Notifications",
        )
        hints = {"desktop-entry": "io.github.multipak.manager"}
        return int(server.Notify("MultiPak", dbus.UInt32(replaces), "io.github.multipak.manager",
                                 summary, body, [], hints, -1))
    except dbus.DBusException as error:
        logging.warning("Could not show a notification: %s", error)
        return 0


def downloads():
    from gi.repository import GLib

    folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    return folder or os.path.join(os.path.expanduser("~"), "Downloads")


def save(name, source, folder):
    """Copy an open file to a new file in folder (never replacing one); return its name."""
    name = check_name(name)
    size = os.fstat(source).st_size
    os.makedirs(folder, exist_ok=True)
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        space = os.statvfs(directory)
        if space.f_bavail * space.f_frsize - size < RESERVE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        for number in itertools.count():
            chosen = numbered(name, number) if number else name
            try:
                target = os.open(chosen, flags, 0o644, dir_fd=directory)
                break
            except FileExistsError:
                continue
        try:
            with os.fdopen(target, "wb") as output:
                while chunk := os.read(source, CHUNK):
                    output.write(chunk)
        except BaseException:
            os.unlink(chosen, dir_fd=directory)
            raise
        return chosen
    finally:
        os.close(directory)


def receive(files):
    """In the owner's session: copy the files the root service took to Downloads, each in
    its own thread so a big one never holds the session."""

    def work(name, source):
        folder = downloads()
        try:
            saved = save(name, source, folder)
            notify(text("received"), text("saved", name=saved, folder=folder))
        except Exception as error:
            logging.warning("Could not receive %s from Android: %s", name, error)
            notify(text("received"), text("not_received", name=name, error=error))
        finally:
            os.close(source)

    for name, descriptor in files:
        threading.Thread(target=work, args=(str(name), descriptor.take()), daemon=True).start()


class _Progress:
    """KDE's transfer notification (bar, Cancel), plain notifications elsewhere, or
    lines for the Manager's window."""

    def __init__(self, lines):
        self.lines, self.view, self.replaces, self.cancelled = lines, None, 0, False

    def start(self, name, total):
        label = instance().label
        if self.lines:
            print(f"START {total} {name}", flush=True)
            return
        import dbus

        try:
            server = dbus.SessionBus().get_object("org.kde.JobViewServer", "/JobViewServer")
            path = server.requestView("io.github.multipak.manager", 1, {},
                                      dbus_interface="org.kde.JobViewServerV2")
            self.view = dbus.Interface(dbus.SessionBus().get_object("org.kde.JobViewServer", path),
                                       "org.kde.JobViewV3")
            self.view.connect_to_signal("cancelRequested", self.cancel)
            self.view.update({"title": text("sending", label=label),
                              "descriptionLabel1": text("file"), "descriptionValue1": name,
                              "totalAmount.bytes": dbus.UInt64(total)})
        except dbus.DBusException:
            self.view = None
            self.replaces = notify(text("sending", label=label), name)

    def cancel(self):
        self.cancelled = True

    def poll(self):
        """Deliver Cancel from KDE's notification."""
        if self.view is not None:
            from gi.repository import GLib

            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)

    def step(self, done, total):
        if self.lines:
            print(f"PROGRESS {done} {total}", flush=True)
        elif self.view is not None:
            import dbus

            percent = 100 * done // total if total else 100
            self.view.update({"processedAmount.bytes": dbus.UInt64(done),
                              "percent": dbus.UInt32(percent)})

    def finish(self, name, error=None):
        label = instance().label
        if self.lines:
            # Codes, not texts: the Manager shows its own translations.
            print(f"DONE {name}" if error is None else f"ERROR failed {name}", flush=True)
            return
        if self.view is not None:
            import dbus

            try:
                if error is None:
                    self.view.terminate(dbus.UInt32(0), "", {})
                elif self.cancelled:
                    self.view.terminate(dbus.UInt32(1), "", {})  # KJob::KilledJobError
                else:
                    self.view.terminate(dbus.UInt32(100), text("failed", name=name, error=error), {})
            except dbus.DBusException as problem:
                logging.warning("Could not update the transfer notification: %s", problem)
        elif error is None:
            notify(text("done", label=label), text("sent", name=name, label=label), self.replaces)
        elif not self.cancelled:
            notify(text("not_sent", label=label), text("failed", name=name, error=error), self.replaces)


def _pump(source, pipe, total, progress, finished):
    """Write the file into the pipe until it ends, the root service stops or the user cancels."""
    done, last = 0, -1
    chunk = b""
    while not finished.is_set() and not progress.cancelled:
        progress.poll()
        if not chunk:
            chunk = os.read(source, CHUNK)
            if not chunk:
                return done
        _, ready, _ = select.select([], [pipe], [], 0.2)
        if ready:
            try:
                written = os.write(pipe, chunk)
            except BrokenPipeError:
                return done
            chunk = chunk[written:]
            done += written
            if done * 100 // max(total, 1) != last:
                last = done * 100 // max(total, 1)
                progress.step(done, total)
    return done


def send_file(manager, path, lines=False):
    """Send one file; return the name it got in Android. Raises on failure."""
    import dbus

    name = os.path.basename(os.path.normpath(path))
    name = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    source = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        info = os.fstat(source)
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError(text("folder", name=name))
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file")
        progress = _Progress(lines)
        progress.start(name, info.st_size)
        reading, writing = os.pipe2(os.O_CLOEXEC)
        os.set_blocking(writing, False)
        outcome, finished = {}, threading.Event()
        handle = dbus.types.UnixFd(reading)
        os.close(reading)

        def call():
            nonlocal handle
            try:
                outcome["name"] = str(manager.SendFile(handle, check_name(name), dbus.UInt64(info.st_size),
                                                       timeout=7 * 24 * 3600))
            except Exception as error:
                outcome["error"] = error
            finally:
                handle = None  # Drops the last reading end on this side.
                finished.set()

        caller = threading.Thread(target=call, daemon=True)
        caller.start()
        try:
            _pump(source, writing, info.st_size, progress, finished)
        finally:
            os.close(writing)  # Ends the file for Android; a cancelled send arrives short.
        caller.join()
        if "error" in outcome or progress.cancelled:
            error = outcome.get("error") or RuntimeError("Cancelled")
            message = error.get_dbus_message() if isinstance(error, dbus.DBusException) else str(error)
            progress.finish(name, message.strip().splitlines()[-1] if message.strip() else "?")
            raise RuntimeError(message)
        progress.finish(outcome["name"])
        return outcome["name"]
    finally:
        os.close(source)


def run(args):
    """send [--progress] FILE...: send files to this environment's Download folder."""
    import dbus
    import dbus.mainloop.glib
    import tools.helpers.ipc

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    try:
        manager = tools.helpers.ipc.DBusContainerService()
        running = manager.GetSession().get("state") in ("RUNNING", "FROZEN")
    except dbus.DBusException:
        running = False
    if not running:
        if args.progress:
            print("ERROR off", flush=True)
        else:
            notify(text("not_sent", label=instance().label), text("off"))
        raise SystemExit(1)
    failed = False
    for path in args.FILE:
        try:
            send_file(manager, path, args.progress)
        except IsADirectoryError as error:
            failed = True
            if args.progress:
                print(f"ERROR folder {os.path.basename(os.path.normpath(path))}", flush=True)
            else:
                notify(text("not_sent", label=instance().label), str(error))
        except Exception as error:
            failed = True
            logging.error("Could not send %s: %s", path, error)
    if failed:
        raise SystemExit(1)


# Menus of the file managers, written when Android is ready.


def _escape(value):
    return value.replace("\\", "\\\\")


def service_menu():
    """Dolphin's menu: one "Send to Android" submenu, one item per environment."""
    label = _escape(instance().label)
    names = "".join(f"X-KDE-Submenu[{code}]={texts['menu']}\n"
                    for code, texts in TEXTS.items() if code != "en")
    return (
        "[Desktop Entry]\nType=Service\nX-KDE-ServiceTypes=KonqPopupMenu/Plugin\n"
        "MimeType=application/octet-stream;\nActions=send\n"
        f"X-KDE-Submenu={TEXTS['en']['menu']}\n{names}\n"
        f"[Desktop Action send]\nName={label}\nIcon={ICON}\n"
        f"Exec={instance().command('send')} %F\n"
    )


def nautilus_script():
    """GNOME Files' script; it gives the selected files one per line."""
    return (
        "#!/bin/sh\n"
        "# MultiPak: send the selected files to Android.\n"
        "IFS='\n'\nset -f\n"
        f"exec {instance().command('send')} -- $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS\n"
    )


def _write(path, content, mode=0o755):
    try:
        if path.read_text() == content:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(content)
    temporary.chmod(mode)  # KDE and GNOME run only executable menus from the home folder.
    temporary.replace(path)


def menu_paths(data_home):
    """(Dolphin's menu, GNOME Files' script folders) for this environment."""
    from pathlib import Path

    data_home = Path(data_home)
    menu = data_home / "kio/servicemenus" / f"multipak-waydroid.{instance().id}.send.desktop"
    scripts = [data_home / "nautilus/scripts" / name for name in sorted(MENU_NAMES)]
    return menu, scripts


def register(data_home):
    """Write the menus of the file managers found on this computer."""
    import shutil

    from pathlib import Path

    menu, scripts = menu_paths(data_home)
    if shutil.which("dolphin"):
        icon = Path(data_home) / "icons/hicolor/scalable/apps" / (ICON + ".svg")
        _write(icon, (Path(__file__).parent / "assets" / (ICON + ".svg")).read_text(), mode=0o644)
        _write(menu, service_menu())
    marker = instance().command("send") + " "
    for folder in scripts:  # The script of this environment, under an older name or language.
        for old in folder.glob("*") if folder.is_dir() else ():
            try:
                if marker in old.read_text(errors="replace"):
                    old.unlink()
            except OSError:
                pass
    if shutil.which("nautilus"):
        title = instance().label.replace("/", "-")
        _write(scripts[0].with_name(text("menu")) / title, nautilus_script())

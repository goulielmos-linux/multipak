"""snapctl without snapd: a shell client inside the bubble, answered by MultiPak.

Requests go through a directory shared with the bubble. MultiPak answers as snapd would
where it can, and keeps "snapctl set" configuration in the installation."""

import errno
import fcntl
import json
import os
import re
import select
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from multipak.localization import tr

from .store import remove_tree

# Where the bubble sees the client and the request directory.
TOOLS = "/snap/.multipak/bin"
CHANNEL = "/snap/.multipak/channel"
MAX_ARGUMENTS = 1024 * 1024
REPLY_SECONDS = 10
MAX_REQUESTS = 16
MAX_CONFIG = 1024 * 1024  # snapctl set: the whole configuration, as JSON.
MAX_REPLY = 4 * 1024 * 1024
STOP = "multipak-stop"
CALL = re.compile(r"call\.[A-Za-z0-9]{6,32}")
KEY = re.compile(r"[a-z0-9](?:-?[a-z0-9])*(?:\.[a-z0-9](?:-?[a-z0-9])*)*")
HEALTH = ("okay", "waiting", "blocked", "error")

CLIENT = r"""#!/bin/sh
# snapctl (and zenity) from MultiPak: passes the request to MultiPak, which answers.
channel=/snap/.multipak/channel
tool=${0##*/}
if [ ! -p "$channel/requests" ]; then
    echo "error: $tool is not available here" >&2
    exit 1
fi
call=$(mktemp -d "$channel/call.XXXXXXXX") || exit 1
trap 'rm -rf "$call"' EXIT
mkfifo -m 600 "$call/reply" || exit 1
printf '%s' "$tool" > "$call/tool"
for argument in "$@"; do printf '%s\0' "$argument"; done > "$call/args"
printf '%s\n' "${call##*/}" > "$channel/requests" || exit 1
exec 3< "$call/reply"
IFS=' ' read -r code where <&3 || code=1
case "$code" in ''|*[!0-9]*) code=1 ;; esac
if [ "$code" = 0 ] || [ "$where" = out ]; then cat <&3; else cat <&3 >&2; fi
exec 3<&-
exit "$code"
"""


# The same client answers as each of these names. zenity: some apps run it to show
# a file chooser or a message, expecting it on any GNOME desktop; neither the snap
# nor its base ships it, so MultiPak shows the dialog itself (see snap_dialog).
CLIENT_NAMES = ("snapctl", "zenity")


def install_client(directory):
    """Write the client where the bubble will see it as snapctl and zenity."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in CLIENT_NAMES:
        target = directory / name
        if target.is_symlink():
            target.unlink()
        if not target.is_file() or target.read_text(encoding="utf-8") != CLIENT:
            temporary = directory / (name + ".tmp")
            temporary.write_text(CLIENT, encoding="utf-8")
            temporary.chmod(0o755)
            temporary.replace(target)
    return directory


# Configuration


class State:
    """An installation's snapctl data: configuration and health."""

    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self, name):
        try:
            value = json.loads((self.directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, name, value):
        temporary = self.directory / (name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.directory / name)

    def config(self):
        with self._locked():
            return self._read("config.json")

    def change_config(self, change):
        with self._locked():
            config = self._read("config.json")
            change(config)
            self._write("config.json", config)

    def set_health(self, status, message, code):
        with self._locked():
            self._write("health.json", {"status": status, "message": message, "code": code})

    def note_unsupported(self, arguments):
        with self._locked():
            with (self.directory / "unsupported.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(arguments) + "\n")


def _lookup(config, key):
    value = config
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _assign(config, key, value):
    parts = key.split(".")
    current = config
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    if value is None:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = value


def _document(value):
    # snapd prints JSON indented with tabs.
    return json.dumps(value, indent="\t", ensure_ascii=False) + "\n"


class Failure(Exception):
    pass


def _split(arguments):
    options, values = [], []
    for argument in arguments:
        if argument.startswith("-") and not values:
            options.append(argument)
        else:
            values.append(argument)
    return options, values


def _get(state, arguments):
    options, keys = _split(arguments)
    unknown = [o for o in options if o not in ("-d", "-t", "--previous")]
    if unknown:
        raise Failure(tr("unknown option {0}").format(unknown[0]))
    if "--previous" in options:
        raise Failure(tr("previous values are only available while a configuration changes"))
    for key in keys:
        if not KEY.fullmatch(key):
            raise Failure(tr("invalid option name: {0}").format(key))
    config = state.config()
    if not keys:
        return _document(config)
    if len(keys) == 1 and "-d" not in options:
        value = _lookup(config, keys[0])
        if value is None:
            return ""
        if isinstance(value, str) and "-t" not in options:
            return value + "\n"
        return _document(value)
    found = {key: _lookup(config, key) for key in keys}
    return _document({key: value for key, value in found.items() if value is not None})


def _set(state, arguments):
    options, values = _split(arguments)
    unknown = [o for o in options if o not in ("-s", "-t")]
    if unknown:
        raise Failure(tr("unknown option {0}").format(unknown[0]))
    if not values:
        raise Failure(tr("set which option?"))
    changes = []
    for item in values:
        if item.endswith("!") and "=" not in item:
            key, value = item[:-1], None
        else:
            key, separator, text = item.partition("=")
            if not separator:
                raise Failure(tr("invalid configuration: {0} (want key=value)").format(item))
            if "-s" in options:
                value = text
            else:
                try:
                    value = json.loads(text)
                except ValueError:
                    if "-t" in options:
                        raise Failure(tr("invalid JSON value: {0}").format(text)) from None
                    value = text
        if not KEY.fullmatch(key):
            raise Failure(tr("invalid option name: {0}").format(key))
        changes.append((key, value))

    def change(config):
        for key, value in changes:
            _assign(config, key, value)
        if len(json.dumps(config, ensure_ascii=False).encode()) > MAX_CONFIG:
            raise Failure(tr("the configuration is too large"))

    state.change_config(change)
    return ""


def _unset(state, arguments):
    if not arguments:
        raise Failure(tr("unset which option?"))
    for key in arguments:
        if not KEY.fullmatch(key):
            raise Failure(tr("invalid option name: {0}").format(key))

    def change(config):
        for key in arguments:
            _assign(config, key, None)

    state.change_config(change)
    return ""


def _connections(info):
    names = set(info.raw_plugs)
    for app in info.apps:
        names.update(app.plugs)
    return names


def _is_connected(info, arguments):
    options, names = _split(arguments)
    if "--list" in options:
        return "".join(name + "\n" for name in sorted(_connections(info)))
    if [o for o in options if o != "--list"]:
        # --pid and --apparmor-label ask about other snaps' processes: never connected.
        raise Failure("", 1)
    if len(names) != 1:
        raise Failure(tr("is-connected needs the name of one plug or slot"))
    # Everything a snap declares is available under MultiPak, so it counts as connected.
    if names[0] in _connections(info):
        return ""
    raise Failure("", 1)


def _component_names(info, items):
    names = []
    for item in items:
        snap_name, separator, component = item.partition("+")
        if not separator or (snap_name and snap_name != info.name) or not component:
            raise Failure(tr("name a component as +component"))
        names.append(component)
    return names


def answer(arguments, info, state, components=None, opener=None, daemons=None):
    """(exit code, output) for one snapctl call. components installs and removes
    components on request; opener(text) -> (code, message) opens a link or file for
    the snap's xdg-open; daemons (snap_daemons.Services) lists, starts and stops the
    snap's services. Without them, such requests fail."""
    if not arguments:
        return 1, tr("error: which command?") + "\n"
    command, rest = arguments[0], arguments[1:]
    if command == "user-open":
        # As in snapctl itself: one link or file, handled without snapd.
        if len(rest) != 1:
            return 1, "error: user-open takes one link or file\n"
        if opener is None:
            return 1, "user-open error: nothing can be opened here\n"
        return opener(rest[0])
    try:
        if command == "get":
            return 0, _get(state, rest)
        if command == "set":
            return 0, _set(state, rest)
        if command == "unset":
            return 0, _unset(state, rest)
        if command == "is-connected":
            return 0, _is_connected(info, rest)
        if command in ("services", "start", "stop", "restart"):
            if daemons is None:
                raise Failure(tr("the snap's services cannot be managed from here"))
            try:
                if command == "services":
                    return 0, daemons.table(rest)
                return 0, daemons.act(command, rest)
            except (OSError, RuntimeError, ValueError) as error:
                raise Failure(str(error)) from error
        if command == "system-mode":
            return 0, "system-mode: run\n"
        if command == "refresh":
            if "--pending" in rest:
                return 0, "pending: none\n"
            return 0, ""
        if command == "set-health":
            if not rest or rest[0] not in HEALTH:
                raise Failure(tr("invalid health status"))
            message = " ".join(item for item in rest[1:] if not item.startswith("--code="))
            code = next((item[7:] for item in rest if item.startswith("--code=")), "")
            state.set_health(rest[0], message, code)
            return 0, ""
        if command in ("install", "remove") and rest and all("+" in item for item in rest):
            names = _component_names(info, [item for item in rest if not item.startswith("-")])
            if components is None:
                raise Failure(tr("components cannot be installed now"))
            try:
                (components.install if command == "install" else components.remove)(names)
            except (OSError, RuntimeError, ValueError) as error:
                raise Failure(str(error)) from error
            return 0, ""
        state.note_unsupported(arguments)
        if command in ("mount", "umount"):
            # mount-control lets a snap ask for mounts; MultiPak makes none, and a
            # request never reports success for a mount that is not there.
            raise Failure(
                tr("MultiPak does not mount or unmount anything for snaps; nothing was changed")
            )
        raise Failure(tr("MultiPak has no equivalent for snapctl {0}").format(command))
    except Failure as failure:
        message, *code = failure.args
        return (code[0] if code else 1), (f"error: {message}\n" if message else "")


# Serving requests from the bubble

READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _read_call(directory_fd):
    # O_NONBLOCK: a named pipe in place of the file must not hold the thread.
    fd = os.open("args", READ_FLAGS, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a file")
        data = os.read(fd, MAX_ARGUMENTS + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_ARGUMENTS:
        raise OSError("request too large")
    items = data.split(b"\0")
    if items and items[-1] == b"":
        items.pop()
    return [item.decode("utf-8", "replace") for item in items]


def _read_tool(directory_fd):
    """The name the client was called by; snapctl when it does not say."""
    try:
        fd = os.open("tool", READ_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return "snapctl"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a file")
        return os.read(fd, 64).decode("utf-8", "replace")
    finally:
        os.close(fd)


def _reply(directory_fd, code, text, to_output=False, deadline=REPLY_SECONDS):
    deadline = time.monotonic() + deadline
    while True:
        try:
            fd = os.open(
                "reply",
                os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            break
        except OSError as error:
            # No reader yet: the client opens the pipe right after sending the request.
            # Anything else, such as a link in place of the pipe, gets no answer.
            if error.errno != errno.ENXIO or time.monotonic() > deadline:
                return
            time.sleep(0.02)
    try:
        # Only a named pipe the client made: never a file a link could point at.
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            return
        # A failure's text goes to standard error unless the tool prints it, as zenity
        # prints the extra button pressed while reporting 1.
        where = " out" if to_output else ""
        data = f"{code}{where}\n{text}".encode()[:MAX_REPLY]
        # Written without blocking, within the deadline: a client that stops reading
        # must not hold the thread.
        deadline = time.monotonic() + REPLY_SECONDS
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            select.select([], [fd], [], remaining)
            try:
                data = data[os.write(fd, data) :]
            except BlockingIOError:
                continue
    finally:
        os.close(fd)


class Server:
    """Answers snapctl for one run of a snap's app or hook."""

    def __init__(
        self, directory, info, state, components=None, dialogs=None, opener=None, daemons=None
    ):
        # dialogs(arguments) -> (code, text, to_output): zenity's dialogs, and
        # opener(text) -> (code, message): user-open. None where nothing can be shown,
        # such as the snap's hooks.
        self.directory = Path(directory)
        self.info, self.state, self.components = info, state, components
        self.dialogs, self.opener, self.daemons = dialogs, opener, daemons
        self._thread = None
        # The app decides how many requests it sends: a limit on those answered at
        # once, and one dialog at a time, so that it cannot flood the desktop.
        self._slots = threading.BoundedSemaphore(MAX_REQUESTS)
        self._dialog = threading.Lock()

    def __enter__(self):
        self.directory.mkdir(parents=True, mode=0o700)
        os.mkfifo(self.directory / "requests", 0o600)
        self._fd = os.open(self.directory / "requests", os.O_RDWR | os.O_CLOEXEC)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        try:
            os.write(self._fd, (STOP + "\n").encode())
            self._thread.join(timeout=5)
        finally:
            os.close(self._fd)
            # The app may have left folders it made unreadable.
            remove_tree(self.directory, ignore_errors=True)

    def _serve(self):
        buffer = b""
        while True:
            chunk = os.read(self._fd, 4096)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                name = line.decode("utf-8", "replace").strip()
                if name == STOP:
                    return
                if CALL.fullmatch(name):
                    if not self._slots.acquire(blocking=False):
                        self._refuse(name)
                        continue
                    threading.Thread(target=self._handle, args=(name,), daemon=True).start()

    def _refuse(self, name):
        try:
            directory_fd = os.open(
                self.directory / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError:
            return
        try:
            # Never waits: the client may not be reading yet, and the loop must go on.
            _reply(directory_fd, 1, "error: too many requests at once\n", deadline=0)
        except OSError:
            pass
        finally:
            os.close(directory_fd)

    def _handle(self, name):
        try:
            self._answer(name)
        finally:
            self._slots.release()

    def _answer(self, name):
        try:
            directory_fd = os.open(
                self.directory / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError:
            return
        try:
            try:
                tool = _read_tool(directory_fd)
                arguments = _read_call(directory_fd)
            except OSError:
                _reply(directory_fd, 1, "error: unreadable request\n")
                return
            if tool == "zenity":
                if self.dialogs is None:
                    _reply(directory_fd, 1, "zenity: dialogs cannot be shown here\n")
                    return
                with self._dialog:
                    result = self.dialogs(arguments)
                _reply(directory_fd, *result)
                return
            if arguments[:1] == ["user-open"] and self.opener is not None:
                with self._dialog:  # The portal may ask which app to use.
                    code, text = answer(arguments, self.info, self.state, None, self.opener)
                _reply(directory_fd, code, text)
                return
            code, text = answer(
                arguments, self.info, self.state, self.components, self.opener, self.daemons
            )
            _reply(directory_fd, code, text)
        except OSError:
            pass
        finally:
            os.close(directory_fd)


def channel_directory(snap_directory):
    return Path(snap_directory) / "channels" / uuid.uuid4().hex


def _channel_alive(path, now, grace):
    """Whether a Server may still answer in this folder: its request pipe has a
    reader, or the folder was made a moment ago and the pipe is not there yet."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    requests = path / "requests"
    try:
        pipe = os.lstat(requests)
    except FileNotFoundError:
        return now - info.st_mtime < grace
    if not stat.S_ISFIFO(pipe.st_mode):
        return False
    try:
        descriptor = os.open(requests, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        # ENXIO: nobody reads the pipe, so the Server that made it has ended.
        # Anything else cannot be confirmed, and counts as running.
        return error.errno != errno.ENXIO
    os.close(descriptor)
    return True


def clear_orphan_channels(snap_directory, *, now=time.time, grace=10):
    """Remove the folders that runs ended without removing, as when a launcher is
    killed. Returns the folders still in use, which are left alone. The caller holds
    the snap's application and service locks, so no new run can start meanwhile."""
    root = Path(snap_directory) / "channels"
    if root.is_symlink() or not root.is_dir():
        return []
    alive, timestamp = [], now()
    for path in sorted(root.iterdir()):
        if _channel_alive(path, timestamp, grace):
            alive.append(path.name)
        elif path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            remove_tree(path)
    return alive


def state(snap_directory):
    return State(Path(snap_directory) / "state")

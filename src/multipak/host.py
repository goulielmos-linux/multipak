"""Host commands and shared paths for Flatpak and native development builds."""

import os
import shutil
import subprocess
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

APP_ID = "io.github.goulielmos_linux.multipak"
LEGACY_APP_ID = "io.github.multipak.manager"
HOST_TOOLS = frozenset(
    {
        "distrobox",
        "podman",
        "rpm-ostree",
        "systemctl",
        "skopeo",
        "bazaar",
        "bazaar-daemon",
        "gnome-software",
        "plasma-discover",
        "flatpak",
        "kontainer",
        "xdg-mime",
        "update-mime-database",
        "update-desktop-database",
        "kbuildsycoca6",
        "setsid",
        "waydroid",
        "multipak-waydroid",
        "lxc-start",
        "lxc-info",
        "test",
        "bwrap",
        "pkexec",
        "cat",
        # Snap services and system users.
        "systemd-run",
        "unshare",
        "newuidmap",
        "newgidmap",
        "busctl",
        # Only MultiPak's own trusted dependency probe uses the host's Python.
        "python3",
    }
)

# setsid gives every operation its own host session. The guard also handles the
# launcher dying before it can forward cancellation. It holds the process group
# until cleanup, so its ID cannot be reused by an unrelated process.
#
# From a Flatpak, $PPID is the host's setsid, not MultiPak's launcher, and
# flatpak-spawn outlives a launcher killed outright, so --watch-bus never fires
# either. The lifeline is what tells: the read end of a pipe whose only write end
# the launcher holds, forwarded as $MULTIPAK_LIFELINE. It reaches end of file as
# soon as the launcher ends, however it ends, and the group is ended with it.
# dash, the host shell of some systems, redirects only descriptors 0 to 9.
_SUPERVISOR = r"""
leader=$$
launcher=$PPID
trap '' INT TERM HUP
lifeline=${MULTIPAK_LIFELINE-}
unset MULTIPAK_LIFELINE
case "$lifeline" in
    [3-9]) eval "true <&$lifeline" 2>/dev/null || lifeline= ;;
    *) lifeline= ;;
esac
reader=
if [ -n "$lifeline" ]; then
    eval "cat <&$lifeline" >/dev/null 2>&1 &
    reader=$!
    eval "exec $lifeline<&-"
fi
(
    trap '' INT TERM HUP
    alive() {
        kill -0 "$1" 2>/dev/null || return 1
        IFS= read -r stat < "/proc/$1/stat" || return 1
        state=${stat##*) }
        case "$state" in Z\ *|X\ *) return 1 ;; esac
    }
    while alive "$leader" && alive "$launcher" && { [ -z "$reader" ] || alive "$reader"; }; do
        sleep 0.1
    done
    kill -TERM 0 2>/dev/null
    sleep 0.2
    kill -KILL 0
) </dev/null >/dev/null 2>&1 &
finish() {
    result=$1
    trap '' INT TERM HUP
    kill -TERM 0 2>/dev/null
    exit "$result"
}
trap 'finish 130' INT
trap 'finish 143' TERM HUP
# A command in the background reads /dev/null unless told otherwise: it gets the
# launcher's input, so that a snap's command-line app can read a pipe.
exec 9<&0
"$@" 0<&9 9<&- &
exec 9<&-
wait "$!"
finish "$?"
"""
LIFELINE_VARIABLE = "MULTIPAK_LIFELINE"
# The lifeline must reach the supervisor as one of these: see _SUPERVISOR.
LIFELINE_NUMBERS = range(3, 10)
_lifeline = None


def lifeline():
    """The read end of this process's lifeline, for supervised host commands. Its
    write end stays here, never inherited: when this process ends, the pipe does."""
    global _lifeline
    if _lifeline is None or _lifeline[2] != os.getpid():
        read, write = os.pipe()
        _lifeline = (read, write, os.getpid())
    return _lifeline[0]


def is_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


def command(
    argv, *, environment=None, watch_bus=True, supervise=False, forward_fds=(), lifeline=None
):
    """Keep host environment defaults; forward only deliberate overrides. forward_fds:
    open file descriptors the command receives under the same numbers. lifeline: the
    number flatpak-spawn has the lifeline's read end under (see lifeline()), for a
    supervised command that must end with this process."""
    if not is_flatpak():
        return list(argv)
    prefix = ["/usr/bin/flatpak-spawn", "--host", f"--directory={Path.home()}"]
    prefix += [f"--forward-fd={number}" for number in forward_fds]
    if supervise and lifeline is not None:
        if lifeline not in LIFELINE_NUMBERS:
            raise ValueError(f"the lifeline must be descriptor 3 to 9, not {lifeline}")
        prefix += [f"--forward-fd={lifeline}", f"--env={LIFELINE_VARIABLE}={lifeline}"]
    if watch_bus:
        prefix.append("--watch-bus")
    for key, value in (environment or {}).items():
        prefix.append(f"--env={key}={value}")
    if supervise:
        argv = ["setsid", "--fork", "--wait", "sh", "-c", _SUPERVISOR, "multipak-host", *argv]
    return [*prefix, "--", *map(str, argv)]


def run(argv, *, environment=None, **kwargs):
    if not is_flatpak() and environment is not None:
        kwargs["env"] = os.environ | environment
    return subprocess.run(command(argv, environment=environment), **kwargs)


def which(executable):
    if not is_flatpak() or Path(executable).name not in HOST_TOOLS:
        return shutil.which(executable)
    try:
        result = run(
            ["sh", "-c", 'command -v "$1"', "multipak-lookup", executable],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = result.stdout.strip()
    return path if result.returncode == 0 and path.startswith("/") and "\n" not in path else None


def directories():
    """Read the session user's XDG directories rather than Flatpak's private ones."""
    if is_flatpak():
        return _flatpak_directories()
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")),
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")),
    )


@lru_cache(maxsize=1)
def _flatpak_directories():
    result = run(
        [
            "sh",
            "-c",
            'printf "%s\\n" "${XDG_DATA_HOME:-$HOME/.local/share}" '
            '"${XDG_CONFIG_HOME:-$HOME/.config}"',
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    paths = result.stdout.splitlines()
    if len(paths) != 2 or not all(Path(path).is_absolute() for path in paths):
        from .localization import tr

        raise RuntimeError(tr("Could not identify the host's user directories."))
    return tuple(map(Path, paths))


def temporary_directory():
    """A temporary location that host tools can also access."""
    if not is_flatpak():
        return None
    path = Path(os.environ["XDG_CACHE_HOME"]) / "host-tmp"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


@contextmanager
def accessible_file(source):
    """Stage sandbox-only resources before passing their paths to host tools."""
    source = Path(source)
    if not is_flatpak() or source.resolve().is_relative_to(Path.home().resolve()):
        yield source
        return
    with TemporaryDirectory(prefix="multipak-file-", dir=temporary_directory()) as directory:
        target = Path(directory) / source.name
        shutil.copyfile(source, target)
        yield target


def installer_desktop_id():
    return APP_ID + ".installer" if is_flatpak() else "io.github.multipak.installer"

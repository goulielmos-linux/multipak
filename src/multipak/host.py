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
        "kbuildsycoca6",
        "setsid",
        "pkexec",
        "cat",
        # Only MultiPak's own trusted dependency probe uses the host's Python.
        "python3",
    }
)

# setsid gives every operation its own host session. The guard also handles the
# launcher dying before it can forward cancellation. It holds the process group
# until cleanup, so its ID cannot be reused by an unrelated process.
_SUPERVISOR = r"""
leader=$$
launcher=$PPID
trap '' INT TERM HUP
(
    trap '' INT TERM HUP
    alive() {
        kill -0 "$1" 2>/dev/null || return 1
        IFS= read -r stat < "/proc/$1/stat" || return 1
        state=${stat##*) }
        case "$state" in Z\ *|X\ *) return 1 ;; esac
    }
    while alive "$leader" && alive "$launcher"; do
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
"$@" &
wait "$!"
finish "$?"
"""


def is_flatpak() -> bool:
    return Path("/.flatpak-info").is_file()


def command(argv, *, environment=None, watch_bus=True, supervise=False):
    """Keep host environment defaults; forward only deliberate overrides."""
    if not is_flatpak():
        return list(argv)
    prefix = ["/usr/bin/flatpak-spawn", "--host", f"--directory={Path.home()}"]
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

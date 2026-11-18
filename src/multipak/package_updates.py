"""Whether the applications installed in containers have newer versions.

The container's own package manager answers, through package_check.py. Updating still
updates the whole container; this only tells which applications have something new.
"""

import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import host
from .environments import copy_into
from .models import valid_environment_name
from .package_check import MARK

# Package kinds whose package manager can answer.
SUPPORTED = frozenset(
    {"deb", "rpm", "arch", "apk", "xbps", "eopkg", "pisi", "slackware", "slitaz", "gentoo"}
)
# Distrobox shares /tmp with the host, so a fixed name there could be created or
# replaced by another user of the computer before it runs. Each check uses a new
# folder with a random name, created with mode 700 (mkdir fails if it already exists).
FOLDER = "/tmp/multipak-check-"
SCRIPT = "package-check.py"
# Results older than this are shown as not checked.
FRESH = 24 * 60 * 60


@dataclass(frozen=True)
class Check:
    # available, current, no-source (installed from a file), failed or unsupported
    status: str
    installed: str = ""
    version: str = ""
    error: str = ""
    checked: float = 0.0


def check_container(runner, name, kind, packages, *, refresh=False, now=time.time):
    """{package name: Check} for the packages of one kind in container name."""
    if not valid_environment_name(name):
        raise ValueError(name)
    packages = list(dict.fromkeys(packages))
    if not packages:
        return {}
    enter = ["distrobox", "enter", "--name", name, "--no-tty", "--no-workdir", "--"]
    folder = FOLDER + secrets.token_hex(8)
    target = f"{folder}/{SCRIPT}"
    runner.run([*enter, "mkdir", "-m", "700", "--", folder], timeout=120)
    try:
        script = Path(__file__).with_name("package_check.py")
        with host.accessible_file(script) as visible:
            copy_into(runner, name, visible, target)
        command = [*enter, "sudo", "-n", "python3", target, kind]
        command += ["--refresh"] if refresh else []
        output = runner.run([*command, *packages], timeout=900 if refresh else 300)
    finally:
        # The copied script belongs to the container's root.
        runner.run([*enter, "sudo", "-n", "rm", "-rf", "--", folder], check=False, timeout=60)
    return parse_output(output, packages, now())


def parse_output(output, packages, checked):
    line = next(
        (line for line in reversed(str(output).splitlines()) if line.startswith(MARK + " ")), None
    )
    if line is None:
        raise RuntimeError("The container did not answer.")
    data = json.loads(line[len(MARK) + 1 :])
    results = {}
    for name in packages:
        value = data.get(name) if isinstance(data, dict) else None
        if not isinstance(value, dict):
            results[name] = Check("failed", error="no answer", checked=checked)
            continue
        results[name] = Check(
            str(value.get("status", "failed")),
            str(value.get("installed", "")),
            str(value.get("version", "")),
            str(value.get("error", "")),
            checked,
        )
    return results


def _path(store):
    return store.root / "package-updates.json"


def cached(store, *, now=time.time):
    """{transaction id: Check} saved by the last checks, still recent."""
    try:
        data = json.loads(_path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return {}
    results = {}
    for identifier, value in data.items() if isinstance(data, dict) else ():
        try:
            check = Check(**value)
        except TypeError:
            continue
        if now() - float(check.checked) < FRESH:
            results[identifier] = check
    return results


def remember(store, results):
    """Save {transaction id: Check}, replacing the saved results."""
    path = _path(store)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            json.dump({key: asdict(value) for key, value in results.items()}, out)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

"""Executed INSIDE the container. Only installed package files are inspected."""

import base64
import json
import subprocess
import sys
from pathlib import Path

MARKER = "MULTIPAK_RESULT="


def package_present(kind: str, name: str) -> bool:
    command = {
        "rpm": ["rpm", "-q", name],
        "deb": ["dpkg-query", "-W", name],
        "arch": ["pacman", "-Q", name],
    }[kind]
    return (
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        == 0
    )


def remove_package(kind: str, name: str) -> dict:
    if package_present(kind, name):
        # Native dependency checks remain enabled. Never remove existing dependents or
        # autoremove shared dependencies just to discard one application's installation.
        command = {
            "rpm": ["rpm", "-e", name],
            "deb": ["dpkg", "--purge", name],
            "arch": ["pacman", "-R", "--noconfirm", name],
        }[kind]
        subprocess.run(command, check=True)
    return {"present": package_present(kind, name)}


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def transaction_busy(marker: str) -> bool:
    path = Path(marker)
    if not path.exists():
        return False
    processes = json.loads(path.read_text())
    return any(
        identity is not None and process_identity(int(pid)) == identity
        for pid, identity in processes.items()
    )


def run_transaction(marker: str, command: list[str]) -> int:
    import os

    path = Path(marker)
    identity = process_identity(os.getpid())
    if identity is None:
        raise RuntimeError("Could not monitor the operation in the shared environment.")
    identities = {str(os.getpid()): identity}

    def record():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(identities))
        temporary.replace(path)

    record()
    try:
        with subprocess.Popen(command) as process:
            identities[str(process.pid)] = process_identity(process.pid)
            record()
            return process.wait()
    finally:
        path.unlink(missing_ok=True)


def collect(kind: str, name: str) -> dict:
    if kind == "deb":
        status = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Status}", name],
            text=True,
        )
        if status.strip() != "install ok installed":
            raise RuntimeError("The package is not fully installed.")
        command = ["dpkg-query", "-L", name]
    elif kind == "arch":
        subprocess.run(["pacman", "-Q", name], check=True, stdout=subprocess.DEVNULL)
        command = ["pacman", "-Qlq", name]
    elif kind == "rpm":
        subprocess.run(["rpm", "-q", name], check=True, stdout=subprocess.DEVNULL)
        command = ["rpm", "-ql", name]
    else:
        raise ValueError("Unsupported package type.")
    files = subprocess.check_output(command, text=True).splitlines()
    desktops = []
    for value in files:
        path = Path(value)
        if not path.is_absolute() or path.suffix != ".desktop" or not path.is_file():
            continue
        if path.stat().st_size > 1024 * 1024 or len(desktops) >= 32:
            continue
        desktops.append(
            {
                "name": path.name,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    return {"desktops": desktops}


if __name__ == "__main__":
    if sys.argv[1] == "--run":
        raise SystemExit(run_transaction(sys.argv[2], sys.argv[3:]))
    if sys.argv[1] == "--busy":
        result = {"busy": transaction_busy(sys.argv[2])}
    elif sys.argv[1] == "--state":
        result = {"present": package_present(sys.argv[2], sys.argv[3])}
    elif sys.argv[1] == "--remove":
        result = remove_package(sys.argv[2], sys.argv[3])
    else:
        result = collect(sys.argv[1], sys.argv[2])
    print(MARKER + json.dumps(result))

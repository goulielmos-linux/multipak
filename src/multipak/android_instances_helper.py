"""Standalone privileged deployment and registry for named Android environments."""

import fcntl
import hashlib
import io
import ipaddress
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

if __package__:
    from .android_data_helper import migrate, prepare_layout
    from .privileged_files import CLEAN_ENV, cancellation_watch
else:
    from android_data_helper import migrate, prepare_layout
    from privileged_files import CLEAN_ENV, cancellation_watch

RUNTIME_SHA256 = "c66654bbe487fed6c0907230be7fd72c09685655a05d134cd064e7e5b4c2b2d1"
BASE = Path("/var/lib/multipak-waydroid")
CLI = Path("/usr/local/bin/multipak-waydroid")
RELEASES = {"android-13": "20.0", "android-11": "18.1"}


def caller():
    if os.geteuid() != 0:
        raise PermissionError("System authorization is required")
    value = os.environ.get("PKEXEC_UID", "")
    if not value.isdigit() or int(value) == 0:
        raise PermissionError("A regular user must request this action")
    return pwd.getpwuid(int(value))


def run(args):
    result = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    print(result.stdout, flush=True)
    result.check_returncode()
    return result.stdout


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError("Unexpected managed path")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".multipak-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            os.fchmod(f.fileno(), mode)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def deploy(archive):
    with Path(archive).open("rb") as f:
        data = f.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != RUNTIME_SHA256:
        raise ValueError("The Android runtime failed its integrity check")
    runtime = BASE / "runtime" / RUNTIME_SHA256
    if CLI.exists() or CLI.is_symlink():
        if not CLI.is_symlink() or not CLI.readlink().is_relative_to(BASE / "runtime"):
            raise ValueError("The launcher already belongs to another installation")
    if not runtime.exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".runtime-", dir=runtime.parent) as temp:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not (member.isdir() or member.isfile()) or member.size > 2 * 1024 * 1024:
                        raise ValueError("Unexpected runtime entry")
                tar.extractall(temp, filter="data")
            for p in Path(temp).rglob("*"):
                p.chmod(
                    0o755
                    if p.is_dir() or p.suffix == ".sh" or p.name == "multipak-waydroid"
                    else 0o644
                )
            Path(temp).chmod(0o755)
            Path(temp).rename(runtime)
    CLI.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLI.with_name(".multipak-waydroid-new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(runtime / "multipak-waydroid")
    temporary.replace(CLI)
    unit = """[Unit]
Description=MultiPak Android instance %i
After=network.target
[Service]
Type=dbus
BusName=id.waydro.MultiPak.Container.%i
ExecStart=/usr/bin/python3 -E -s -u /usr/local/bin/multipak-waydroid --instance %i container start
UMask=0022
[Install]
WantedBy=multi-user.target
"""
    atomic(Path("/etc/systemd/system/multipak-waydroid@.service"), unit.encode())
    run(["/usr/bin/systemctl", "daemon-reload"])
    print("MULTIPAK_ANDROID_RUNTIME_READY", flush=True)


def validate_id(identifier):
    if not re.fullmatch(r"[a-z][a-z0-9]{0,10}", identifier):
        raise ValueError("Invalid Android instance ID")
    return identifier


def metadata(identifier, owner):
    path = BASE / "instances" / validate_id(identifier) / "instance.json"
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError("Unexpected Android instance path")
    permissions = path.stat()
    if permissions.st_uid != 0 or permissions.st_mode & 0o022:
        raise PermissionError("Android metadata is not root-owned")
    data = json.loads(path.read_text())
    if data["id"] != identifier or data["owner"] != owner.pw_uid:
        raise PermissionError("Android instance belongs to another user")
    return data


def label_layout(directory):
    """Reuse an existing Waydroid SELinux type; never disable enforcement."""
    restorecon = shutil.which("restorecon", path=CLEAN_ENV["PATH"])
    if not restorecon:
        return
    try:
        import selinux
    except ImportError:
        selinux = None
    if selinux and selinux.is_selinux_enabled() > 0:
        context = selinux.matchpathcon("/var/lib/waydroid", 0)[1]
        if context.split(":")[2] == "waydroid_data_t":
            semanage = shutil.which("semanage", path=CLEAN_ENV["PATH"])
            if not semanage:
                raise RuntimeError("SELinux labeling requires the system semanage utility")
            pattern = r"/var/lib/multipak-waydroid/instances/[^/]+/(private|exchange)(/.*)?"
            local = run([semanage, "fcontext", "-l", "-C"])
            existing = next(
                (
                    line
                    for line in local.splitlines()
                    if line.split() and line.split()[0] == pattern
                ),
                None,
            )
            if existing and ":waydroid_data_t:" not in existing:
                raise RuntimeError("An existing Android SELinux mapping needs inspection")
            if not existing:
                run([semanage, "fcontext", "-a", "-t", "waydroid_data_t", pattern])
    run([restorecon, "-R", str(directory / "private"), str(directory / "exchange")])


def allocate(owner, identifier, label, profile):
    validate_id(identifier)
    if profile not in RELEASES:
        raise ValueError("Unsupported Android version")
    if not label or len(label) > 64 or any(ord(c) < 32 for c in label):
        raise ValueError("Invalid environment name")
    root = BASE / "instances"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / identifier
    if directory.exists():
        info = metadata(identifier, owner)
        if info["release"] != RELEASES[profile]:
            raise ValueError("The existing Android version was preserved")
        return info
    with (BASE / "registry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if directory.exists():
            raise RuntimeError("Android instance already exists")
        used = set()
        for p in root.glob("*/instance.json"):
            used.add(json.loads(p.read_text())["network_slot"])
        routes = json.loads(
            run(
                [
                    shutil.which("ip", path="/usr/sbin:/usr/bin:/sbin:/bin") or "/usr/sbin/ip",
                    "-j",
                    "route",
                    "show",
                    "table",
                    "all",
                ]
            )
        )
        networks = [
            ipaddress.ip_network(r["dst"], strict=False)
            for r in routes
            if "dst" in r and r["dst"] not in ("default", "0.0.0.0/0") and ":" not in r["dst"]
        ]
        free = next(
            (
                i
                for i in range(1, 201)
                if i not in used
                and not any(ipaddress.ip_network(f"10.203.{i}.0/24").overlaps(n) for n in networks)
            ),
            None,
        )
        if free is None:
            raise RuntimeError("No available Android subnet was found")
        info = {
            "id": identifier,
            "owner": owner.pw_uid,
            "label": label,
            "release": RELEASES[profile],
            "network_slot": free,
            "home": str(Path(owner.pw_dir).resolve()),
            "data_layout": 2,
        }
        directory.mkdir(mode=0o755)
        data_path = prepare_layout(directory, owner)
        data_path.mkdir(mode=0o771)
        (data_path / "icons").mkdir(mode=0o755)
        (data_path / "waydroid_tmp").mkdir(mode=0o755)
        label_layout(directory)
        atomic(directory / "instance.json", json.dumps(info).encode())
        atomic(directory / "waydroid.cfg", b"[properties]\npersist.waydroid.multi_windows = true\n")
        bus = "id.waydro.MultiPak.Container." + identifier
        policy = f'''<!DOCTYPE busconfig PUBLIC
"-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><policy user="root"><allow own="{bus}"/><allow send_destination="{bus}"/></policy>
<policy user="{owner.pw_uid}"><allow send_destination="{bus}"/></policy></busconfig>'''
        atomic(
            Path("/etc/dbus-1/system.d") / f"multipak-waydroid-{identifier}.conf", policy.encode()
        )
    run(
        [
            "/usr/bin/dbus-send",
            "--system",
            "--type=method_call",
            "--print-reply",
            "--dest=org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.ReloadConfig",
        ]
    )
    return info


def initialize(identifier, watch_file):
    # Keep cancellation supervision outside Waydroid's privileged child process.
    import signal
    import time

    parent = os.getppid()
    command = [
        "/usr/bin/python3",
        "-E",
        "-s",
        "-u",
        str(CLI),
        "--instance",
        identifier,
        "--details-to-stdout",
        "init",
        "-s",
        "VANILLA",
        "-c",
        "https://ota.waydro.id/system",
        "-v",
        "https://ota.waydro.id/vendor",
        "-r",
        "lineage",
    ]
    with (
        cancellation_watch(caller(), watch_file) as cancelled,
        subprocess.Popen(
            command, start_new_session=True, stdin=subprocess.DEVNULL, env=CLEAN_ENV, cwd="/"
        ) as child,
    ):
        try:
            while child.poll() is None:
                if cancelled() or os.getppid() != parent:
                    return 130
                time.sleep(0.1)
            return child.returncode
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                if child.poll() is not None:
                    break
                os.killpg(child.pid, sig)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


def scoped_rmtree(path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        shutil.rmtree(path.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def mounted_below(root, mountinfo):
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise RuntimeError("Invalid mount information")
        mountpoint = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4]))
        if mountpoint == root or root in mountpoint.parents:
            return True
    return False


def require_empty(identifier):
    prefix = ["-P", str(BASE / "instances" / identifier / "lxc"), "-n", identifier]
    state = run(["/usr/bin/lxc-info", *prefix, "-sH"]).strip()
    if state == "FROZEN":
        run(["/usr/bin/lxc-unfreeze", *prefix])
    elif state != "RUNNING":
        raise RuntimeError("Could not check Android applications. The container was preserved.")
    attach = ["/usr/bin/lxc-attach", *prefix, "--clear-env", "--", "/system/bin/cmd", "package"]
    installed = run([*attach, "list", "packages", "--user", "0"]).splitlines()
    if "package:android" not in installed:
        raise RuntimeError("Android did not confirm its package list. The container was preserved.")
    for line in run([*attach, "list", "packages", "-3", "--user", "0"]).splitlines():
        if not line:
            continue
        if line != "package:io.github.multipak.gestures":
            raise RuntimeError(
                "Android applications are still installed. The container was preserved."
            )


def remove(owner, identifier, delete_data, *, only_empty=False):
    info = metadata(identifier, owner)
    if only_empty:
        require_empty(identifier)
    run(["/usr/bin/systemctl", "disable", "--now", f"multipak-waydroid@{identifier}.service"])
    directory = BASE / "instances" / identifier
    if os.path.ismount(directory / "rootfs"):
        raise RuntimeError("Android is still mounted")
    binder = Path("/dev/multipak-binder-" + identifier)
    if os.path.ismount(binder):
        run(["/usr/bin/umount", str(binder)])
    # Never follow user-owned symlinks or recursively remove mounted directories.
    for root in (directory,):
        if root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError("Unexpected Android data path")
        if mounted_below(root, Path("/proc/self/mountinfo").read_text()):
            raise RuntimeError("Android data is still mounted")
    if not delete_data and (directory / "private").is_dir():
        retained = BASE / "retained"
        retained.mkdir(mode=0o700, exist_ok=True)
        destination = retained / identifier
        if destination.exists():
            raise ValueError("Retained Android data already exists; nothing was deleted")
        (directory / "private").rename(destination)
        atomic(destination / "instance.json", json.dumps(info).encode(), mode=0o600)
    scoped_rmtree(directory)
    (Path("/etc/dbus-1/system.d") / f"multipak-waydroid-{identifier}.conf").unlink(missing_ok=True)
    if binder.exists():
        binder.rmdir()
    (Path("/var/lib/misc") / f"dnsmasq.mpw{identifier}.leases").unlink(missing_ok=True)
    runtime_dir = Path("/run/multipak-waydroid-" + identifier)
    if runtime_dir.is_dir() and not any(runtime_dir.iterdir()):
        runtime_dir.rmdir()
    run(
        [
            "/usr/bin/dbus-send",
            "--system",
            "--type=method_call",
            "--print-reply",
            "--dest=org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.ReloadConfig",
        ]
    )
    print("MULTIPAK_ANDROID_REMOVED", flush=True)


def reopen_application(owner, identifier, package):
    metadata(identifier, owner)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
        raise ValueError("Invalid Android package ID")
    prefix = ["-P", str(BASE / "instances" / identifier / "lxc"), "-n", identifier]
    state = run(["/usr/bin/lxc-info", *prefix, "-sH"]).strip()
    if state == "FROZEN":
        run(["/usr/bin/lxc-unfreeze", *prefix])
    elif state != "RUNNING":
        raise RuntimeError("Open the Android application before reopening it.")
    attach = ["/usr/bin/lxc-attach", *prefix, "--clear-env", "--"]
    resolved = run(
        [
            *attach,
            "/system/bin/cmd",
            "package",
            "resolve-activity",
            "--brief",
            "--user",
            "0",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            package,
        ]
    )
    components = [
        line.strip()
        for line in resolved.splitlines()
        if re.fullmatch(re.escape(package) + r"/[A-Za-z0-9_.$]+", line.strip())
    ]
    if len(components) != 1:
        raise RuntimeError("Android did not find the application's launcher.")
    # Resolve first, so an application without a launcher is never force-stopped.
    run([*attach, "/system/bin/am", "force-stop", "--user", "0", package])
    output = run(
        [
            *attach,
            "/system/bin/am",
            "start",
            "-W",
            "--user",
            "0",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-n",
            components[0],
        ]
    )
    if not re.search(r"^Status:\s*ok\s*$", output, re.M):
        raise RuntimeError("Android did not confirm that the application was reopened.")
    print("MULTIPAK_ANDROID_APP_REOPENED", flush=True)


def check_migration_space(owner):
    required = 0
    for entry in (BASE / "instances").glob("*/instance.json"):
        info = json.loads(entry.read_text())
        if info.get("owner") != owner.pw_uid or info.get("data_layout", 1) == 2:
            continue
        metadata(info["id"], owner)
        old = Path(info["home"]) / ".local/share/multipak/android-instances" / info["id"] / "data"
        if old.is_dir():
            output = run(
                ["/usr/bin/du", "--apparent-size", "--block-size=1", "-sx", "--", str(old)]
            )
            required += int(output.split()[0])
    reserve = max(256 * 1024**2, required // 20)
    if required and required + reserve > shutil.disk_usage(BASE).free:
        raise RuntimeError("Not enough free space to migrate Android safely; nothing was stopped")


def migrate_owned(owner):
    for entry in sorted((BASE / "instances").glob("*/instance.json")):
        info = json.loads(entry.read_text())
        if info.get("owner") != owner.pw_uid:
            continue
        identifier = validate_id(info["id"])
        metadata(identifier, owner)
        service = f"multipak-waydroid@{identifier}.service"
        # Stop old in-memory daemons as well as Android before changing the layout.
        user_bus = f"unix:path=/run/user/{owner.pw_uid}/bus"
        subprocess.run(
            [
                "/usr/sbin/runuser",
                "-u",
                owner.pw_name,
                "--",
                "/usr/bin/env",
                f"XDG_RUNTIME_DIR=/run/user/{owner.pw_uid}",
                f"DBUS_SESSION_BUS_ADDRESS={user_bus}",
                "/usr/bin/systemctl",
                "--user",
                "stop",
                f"multipak-android-session-{identifier}.service",
            ],
            stdin=subprocess.DEVNULL,
            env=CLEAN_ENV,
            cwd="/",
            timeout=60,
            check=False,
        )
        run(["/usr/bin/systemctl", "stop", service])

        def mounted(root):
            return mounted_below(root, Path("/proc/self/mountinfo").read_text())

        migrated = migrate(entry.parent, info, owner, is_mounted=mounted)
        atomic(entry, json.dumps(migrated).encode())
        label_layout(entry.parent)
        run(["/usr/bin/systemctl", "start", service])


def main():
    owner = caller()
    os.umask(0o022)
    action, *args = sys.argv[1:]
    if action == "deploy" and len(args) == 1:
        check_migration_space(owner)
        deploy(args[0])
        migrate_owned(owner)
    elif action == "create" and len(args) == 4:
        identifier, label, profile, watch_file = args
        allocate(owner, identifier, label, profile)
        result = initialize(identifier, watch_file)
        if result:
            raise SystemExit(result)
        run(["/usr/bin/systemctl", "enable", "--now", f"multipak-waydroid@{identifier}.service"])
        print("MULTIPAK_ANDROID_CREATED", flush=True)
    elif (
        action in ("remove", "remove-empty")
        and len(args) == 2
        and args[1] in ("keep-data", "delete-data")
    ):
        remove(owner, args[0], args[1] == "delete-data", only_empty=action == "remove-empty")
    elif action == "reopen-app" and len(args) == 2:
        reopen_application(owner, *args)
    else:
        raise ValueError("Unsupported Android management action")


if __name__ == "__main__":
    main()

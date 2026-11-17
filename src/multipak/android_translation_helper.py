"""Install one pinned Android 13 native bridge; invoked through PolicyKit.

Standalone standard-library helper. No downloaded Python or shell code is run.
Overlay layout and properties follow waydroid_script/stuff/ndk.py (GPL-3.0+).
The proprietary libraries themselves are downloaded separately, after consent.
"""

import configparser
import hashlib
import io
import json
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

REVISION = "68734c52556d3d7a6db34c603dd9276915c29f2f"
ARCHIVE_URL = (
    "https://github.com/supremegamers/"
    f"vendor_google_proprietary_ndk_translation-prebuilt/archive/{REVISION}.zip"
)
ARCHIVE_SHA256 = "a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0"
MAX_ARCHIVE = 64 * 1024 * 1024
PROPERTIES = {
    "ro.product.cpu.abilist": "x86_64,x86,arm64-v8a,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist32": "x86,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist64": "x86_64,arm64-v8a",
    "ro.dalvik.vm.native.bridge": "libndk_translation.so",
    "ro.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec64": "1",
    "ro.ndk_translation.version": "0.2.3",
    "ro.dalvik.vm.isa.arm": "x86",
    "ro.dalvik.vm.isa.arm64": "x86_64",
}
WAYDROID = ["/usr/bin/python3", "-E", "-s", "-u", "/usr/bin/waydroid"]


def verified_archive(path):
    # Read once, then use these verified bytes (not a mutable user-owned path).
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("The ARM translation download failed its integrity check.")
    return data


def payload(data):
    prefix = f"vendor_google_proprietary_ndk_translation-prebuilt-{REVISION}/prebuilts/"
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 1024:
            raise ValueError("Unexpected ARM translation archive size.")
        for item in archive.infolist():
            if not item.filename.startswith(prefix) or item.is_dir():
                continue
            name = item.filename.removeprefix(prefix)
            path = PurePosixPath(name)
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (
                not name
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or name != path.as_posix()
                or path.parts[0] not in ("bin", "etc", "lib", "lib64")
                or kind not in (0, stat.S_IFREG)
                or item.flag_bits & 1
                or name in result
                or item.file_size > 32 * 1024 * 1024
            ):
                raise ValueError("Unexpected ARM translation archive entry.")
            result[name] = archive.read(item)
            if sum(map(len, result.values())) > 128 * 1024 * 1024:
                raise ValueError("Unexpected ARM translation archive size.")
    required = {"lib/libndk_translation.so", "lib64/libndk_translation.so"}
    if not required <= result.keys():
        raise ValueError("The ARM translation archive is incomplete.")
    return result


def safe_target(root, name):
    if root.resolve() != root.absolute():
        raise ValueError("A Waydroid overlay path is a symbolic link.")
    target = root / name
    for parent in [target, *target.parents]:
        if parent.is_symlink():
            raise ValueError("A Waydroid overlay path is a symbolic link.")
        if parent == root:
            return target
    raise ValueError("Invalid Waydroid overlay path.")


def write_atomic(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=".multipak-arm-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command(arguments):
    result = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        cwd="/",
    )
    print(result.stdout, flush=True)
    result.check_returncode()
    if re.search(r"^(?:\[[^\]]*\]\s*)*ERROR\b", result.stdout, re.M):
        raise RuntimeError(result.stdout[-4000:])


def apply(data, *, base=Path("/var/lib/waydroid"), run=command):
    config = base / "waydroid.cfg"
    if config.is_symlink() or base.is_symlink():
        raise ValueError("Unexpected Waydroid configuration path.")
    original = config.read_bytes()
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_string(original.decode())
    if cfg.get("waydroid", "arch", fallback="") != "x86_64":
        raise ValueError("Automatic ARM translation requires x86_64 Android.")
    if not cfg.getboolean("waydroid", "mount_overlays", fallback=False):
        raise ValueError("Automatic ARM translation requires Waydroid overlays.")
    bridge = cfg.get("properties", "ro.dalvik.vm.native.bridge", fallback="")
    if bridge not in ("", "0", "libndk_translation.so"):
        raise ValueError("An existing Android native bridge was preserved.")
    builds = (base / "rootfs/system/build.prop", base / "rootfs/build.prop")
    if not any(
        path.is_file() and re.search(r"^ro.build.version.sdk=33\s*$", path.read_text(), re.M)
        for path in builds
    ):
        raise ValueError("Automatic ARM translation currently supports Android 13 only.")
    files = payload(data)
    root = base / "overlay/system"
    # Check every destination before stopping Android or changing its files.
    for name, content in files.items():
        target = safe_target(root, name)
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            raise ValueError("Existing Waydroid overlay files were preserved.")
    backup = base / "waydroid.cfg.before-multipak-arm"
    if backup.is_symlink():
        raise ValueError("Unexpected Waydroid backup path.")
    if not backup.exists():
        write_atomic(backup, original, 0o600)
    run([*WAYDROID, "container", "stop"])
    if os.path.ismount(base / "rootfs"):
        raise RuntimeError("Android could not stop. Its overlay files were preserved.")
    created = []
    try:
        for name, content in files.items():
            target = safe_target(root, name)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(target, content, 0o755 if name.startswith("bin/") else 0o644)
                created.append(target)
        if not cfg.has_section("properties"):
            cfg.add_section("properties")
        for name, value in PROPERTIES.items():
            cfg.set("properties", name, value)
        output = io.StringIO()
        cfg.write(output)
        write_atomic(config, output.getvalue().encode(), stat.S_IMODE(config.stat().st_mode))
        if restorecon := shutil.which("restorecon"):
            run([restorecon, "-R", str(base / "overlay")])
        # Regenerate Waydroid properties and overlays without downloading images.
        run([*WAYDROID, "upgrade", "-o"])
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        write_atomic(config, original, stat.S_IMODE(config.stat().st_mode))
        run([*WAYDROID, "upgrade", "-o"])
        raise
    print("MULTIPAK_ARM_TRANSLATION_INSTALLED", flush=True)


def main():
    if os.geteuid() != 0 or len(sys.argv) not in (2, 3):
        raise SystemExit("System authorization and one verified archive are required.")
    if platform.machine() != "x86_64" or "sse4_2" not in Path("/proc/cpuinfo").read_text():
        raise SystemExit("This processor is not supported by the ARM translation installer.")
    os.umask(0o022)
    base = Path("/var/lib/waydroid")
    if len(sys.argv) == 3:
        identifier = sys.argv[2]
        if not re.fullmatch(r"[a-z][a-z0-9]{0,10}", identifier):
            raise ValueError("Invalid Android instance ID")
        base = Path("/var/lib/multipak-waydroid/instances") / identifier
        metadata = base / "instance.json"
        uid = os.environ.get("PKEXEC_UID", "")
        if (
            not uid.isdigit()
            or metadata.is_symlink()
            or metadata.resolve() != metadata.absolute()
            or metadata.stat().st_uid != 0
            or metadata.stat().st_mode & 0o022
            or json.loads(metadata.read_text()).get("owner") != int(uid)
        ):
            raise PermissionError("Android instance belongs to another user")
        global WAYDROID
        WAYDROID = [
            "/usr/bin/python3",
            "-E",
            "-s",
            "-u",
            "/usr/local/bin/multipak-waydroid",
            "--instance",
            identifier,
        ]
    if __package__:
        from .privileged_files import open_user_file
    else:
        from privileged_files import open_user_file
    owner = pwd.getpwuid(int(os.environ["PKEXEC_UID"]))
    descriptor = open_user_file(owner, sys.argv[1])
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("The ARM translation download failed its integrity check.")
    apply(data, base=base)


if __name__ == "__main__":
    main()

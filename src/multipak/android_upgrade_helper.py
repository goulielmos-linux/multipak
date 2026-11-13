"""Run the installed Waydroid image updater for one authenticated instance."""

import configparser
import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from . import android_instances_helper as instances
    from .privileged_files import CLEAN_ENV, open_directory
else:
    import android_instances_helper as instances
    from privileged_files import CLEAN_ENV, open_directory


def configuration(directory):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string((directory / "waydroid.cfg").read_text())
    if Path(parser["waydroid"]["images_path"]) != directory / "images":
        raise ValueError("The image directory is outside this Android container")
    return parser


def versions(config):
    return tuple(config["waydroid"].get(key, "0") for key in ("system_datetime", "vendor_datetime"))


def protected_file(path):
    descriptor = open_directory(path.parent)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError("Android update files must be protected regular files")
    finally:
        os.close(descriptor)


def protected_directory(path):
    descriptor = open_directory(path)
    try:
        permissions = os.fstat(descriptor)
        if permissions.st_uid != 0 or permissions.st_mode & 0o022:
            raise PermissionError("The Android update directory is not protected")
    finally:
        os.close(descriptor)


def restore(directory, backup, names, identifier):
    instances.run(["/usr/bin/systemctl", "stop", f"multipak-waydroid@{identifier}.service"])
    if instances.mounted_below(directory, Path("/proc/self/mountinfo").read_text()):
        raise RuntimeError(
            "Android is still mounted; the recovery copy was retained at " + str(backup)
        )
    for name in names:
        source = backup / name
        if source.exists():
            os.replace(source, directory / name)
        else:
            (directory / name).unlink(missing_ok=True)


def upgrade(owner, identifier):
    info = instances.metadata(identifier, owner)
    if info.get("data_layout") != 2:
        raise ValueError("Migrate this Android container before updating it")
    directory = instances.BASE / "instances" / identifier
    for path in (
        directory,
        directory / "images",
        directory / "lxc",
        directory / "lxc" / identifier,
    ):
        protected_directory(path)
    before = configuration(directory)
    names = [
        "images/system.img",
        "images/vendor.img",
        "waydroid.cfg",
        "waydroid_base.prop",
        "waydroid.prop",
        *[f"lxc/{identifier}/{n}" for n in ("config", "config_nodes", "config_session")],
    ]
    for name in names:
        if (directory / name).exists() or (directory / name).is_symlink():
            protected_file(directory / name)
        elif name in names[:4]:
            raise ValueError("This Android container is not prepared")
    lock = directory / ".upgrade.lock"
    if lock.is_symlink():
        raise ValueError("Unexpected Android update lock")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = Path(tempfile.mkdtemp(prefix=".upgrade-backup-", dir=directory))
        preserve = False
        try:
            # Independent copies: upstream extracts images in place. Reflinks
            # avoid a second physical copy where the filesystem supports them.
            for name in names:
                source = directory / name
                if source.exists():
                    target = backup / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    instances.run(
                        [
                            "/usr/bin/cp",
                            "--reflink=auto",
                            "--preserve=all",
                            "--",
                            str(source),
                            str(target),
                        ]
                    )
            try:
                with subprocess.Popen(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-u",
                        str(instances.CLI),
                        "--instance",
                        identifier,
                        "--details-to-stdout",
                        "upgrade",
                    ],
                    stdin=subprocess.DEVNULL,
                    env=CLEAN_ENV,
                    cwd="/",
                    start_new_session=True,
                ) as process:
                    try:
                        code = process.wait(timeout=2400)
                    except BaseException:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise
                    if code:
                        raise RuntimeError("The Waydroid image updater failed")
                after = configuration(directory)
                before_properties = dict(before["properties"]) if "properties" in before else {}
                after_properties = dict(after["properties"]) if "properties" in after else {}
                if before_properties != after_properties:
                    raise RuntimeError(
                        "The Android update changed the container's custom properties"
                    )
                if any(
                    before["waydroid"][key] != after["waydroid"][key]
                    for key in ("system_ota", "vendor_ota")
                ):
                    raise RuntimeError("The Android update changed the selected image channel")
                for name in ("images/system.img", "images/vendor.img"):
                    protected_file(directory / name)
                    if not (directory / name).stat().st_size:
                        raise RuntimeError("The updated Android image is empty")
            except BaseException:
                try:
                    restore(directory, backup, names, identifier)
                    print("Previous Android images and configuration restored.", flush=True)
                except BaseException:
                    preserve = True
                    print("Android recovery copy retained at: " + str(backup), flush=True)
                    raise
                raise
            print(
                "MULTIPAK_ANDROID_UPGRADE_RESULT "
                + json.dumps(
                    {
                        "updated": versions(before) != versions(after),
                        "bridge": before.get(
                            "properties", "ro.dalvik.vm.native.bridge", fallback=""
                        ),
                    }
                ),
                flush=True,
            )
        finally:
            if not preserve:
                shutil.rmtree(backup)


def main():
    if len(sys.argv) != 2:
        raise ValueError("Select one Android container to update")
    upgrade(instances.caller(), sys.argv[1])


if __name__ == "__main__":
    main()

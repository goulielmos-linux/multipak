"""Install exactly the reviewed RPM bytes from a root-private snapshot."""

import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .privileged_files import CLEAN_ENV, copy_verified_file
else:
    from privileged_files import CLEAN_ENV, copy_verified_file


def install(owner, source, digest, *, execute=subprocess.run):
    with tempfile.TemporaryDirectory(prefix="multipak-rpm-", dir="/var/tmp") as directory:
        os.chmod(directory, 0o700)
        target = Path(directory) / "package.rpm"
        copy_verified_file(owner, source, target, digest)
        target.chmod(0o400)
        # No user-controlled path is passed on to the privileged daemon.
        execute(
            ["/usr/bin/rpm-ostree", "install", str(target)],
            check=True,
            stdin=subprocess.DEVNULL,
            env=CLEAN_ENV,
            cwd="/",
            timeout=7200,
        )
    print("MULTIPAK_SYSTEM_RPM_INSTALLED", flush=True)


def main():
    uid = os.environ.get("PKEXEC_UID", "")
    if os.geteuid() != 0 or not uid.isdigit() or int(uid) == 0 or len(sys.argv) != 3:
        raise PermissionError("Authenticated installation is required")
    install(pwd.getpwuid(int(uid)), *sys.argv[1:])


if __name__ == "__main__":
    main()

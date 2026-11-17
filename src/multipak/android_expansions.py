"""Install verified OBB files inside the selected Android environment."""

import re

from .android_bundle import extract_expansions
from .localization import tr

COPY_OBB = r"""
set -eu
package=$1
shift
root=/sdcard/Android
for directory in "$root" "$root/obb" "$root/obb/$package"; do
    test ! -L "$directory" || exit 31
done
mkdir -p "$root/obb/$package"
while test "$#" -gt 0; do
    source=$1 name=$2 digest=$3
    shift 3
    destination="$root/obb/$package/$name"
    test ! -L "$destination" || exit 31
    if test -e "$destination"; then
        test -f "$destination" || exit 32
        actual=$(sha256sum "$destination")
        test "${actual%% *}" = "$digest" || exit 33
        continue
    fi
    temporary="$destination.multipak-tmp"
    test ! -e "$temporary" && test ! -L "$temporary" || exit 34
    trap 'rm -f "$temporary"' EXIT
    cp "$source" "$temporary"
    actual=$(sha256sum "$temporary")
    test "${actual%% *}" = "$digest" || exit 35
    mv -n "$temporary" "$destination"
    actual=$(sha256sum "$destination")
    test "${actual%% *}" = "$digest" || exit 36
    rm -f "$temporary"
    trap - EXIT
done
printf '\nMULTIPAK_OBB_INSTALLED\n'
"""


def install_expansions(waydroid, package, staged):
    if not package.android_expansions:
        return
    from .android import WAYDROID, valid_package_id

    if not valid_package_id(package.desktop_entry):
        raise ValueError("Invalid OBB package")
    from . import android_staging

    with android_staging.directory(waydroid, "multipak-obb-") as directory:
        parts = package.android_expansions
        files = extract_expansions(staged, parts, directory)
        android_staging.publish(waydroid, directory)
        args = [package.desktop_entry]
        for path, part in zip(files, parts, strict=True):
            if not re.fullmatch(
                r"(?:main|patch)\.[1-9][0-9]*\." + re.escape(package.desktop_entry) + r"\.obb",
                part.split_id,
            ):
                raise ValueError("Invalid OBB destination")
            args += [
                f"/data/waydroid_tmp/{path.parent.name}/{path.name}",
                part.split_id,
                part.sha256,
            ]
        output = waydroid.privileged(
            WAYDROID, "shell", "--", "sh", "-c", COPY_OBB, "multipak-obb", *args, timeout=1200
        )
        if "MULTIPAK_OBB_INSTALLED" not in output.splitlines():
            raise RuntimeError(
                tr(
                    "Android could not copy the additional OBB data. "
                    "Existing files were preserved. {0}"
                ).format(output[-2000:])
            )

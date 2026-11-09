# Package installation

## Container selection

MultiPak checks the distribution and version in `os-release` before reusing a
container. A package built for one Fedora release is not assumed to work with
every Fedora version or another RPM-based distribution. Distrobox uses local
rootless Podman. Kontainer can see the same environments when it uses the same
user and backend.

The base catalog is in `src/multipak/models.py`; `catalog.py` resolves image
versions and local availability. Review supported images before each release.
Floating aliases use official images. Ubuntu `latest` follows the official
image's LTS alias, which may differ from the newest interim release. Local images
can be reused, and each installation records its image ID. Private container
homes are not offered for sharing.

Arch packages use `docker.io/library/archlinux:latest` and `MultiPak-Arch-rolling`.
MultiPak runs `pacman -Syu --noconfirm` before `pacman -U --noconfirm FILE` to avoid
partial upgrades. Pacman resolves repository dependencies. PKGBUILDs, source
packages and automatic AUR dependency builds are unsupported.

## Store search

Bazaar searches use a normalized package name, since RPM and DEB names may differ
from Flatpak IDs. Older Bazaar versions open the store and copy the search term
for manual entry.

## Progress and updates

The progress bar advances between operation stages; it does not measure downloaded
bytes or estimate the time remaining. MultiPak reports success after verifying
the package and integrating its shortcuts.

Container updates run `distrobox upgrade NAME` in the background, with activity
and expandable logs. Closing is blocked until the command finishes. Updates affect
the container's packages, not the host or the distribution's major version.

## AppImages

AppImages go directly to installation review. MultiPak reads the application name,
optional version and icon from internal metadata without executing the AppImage
runtime during analysis.

The original download is preserved. The managed copy is stored at
`$XDG_DATA_HOME/multipak/appimages/<id>/application.AppImage`. The same directory
contains the icon and, when needed, an extracted `AppDir`.

Flatpak builds extract both AppImage types and launch their `AppRun` on the host.
In native development builds, type 2 installation tests mounting through the standard runtime without
starting `AppRun`. Missing FUSE and recognized compatibility errors trigger one
extraction fallback. Other mounting failures are reported for diagnosis. Type 1
uses extraction without an administrative mount. The shortcut launches the
managed AppImage or extracted `AppRun`, depending on the selected method.

DwarFS variants and archives without exactly one root `.desktop` file are
unsupported. Uninstalling removes the managed copy, extraction, icon and owned
shortcuts, preserving the original download and application-created personal data.

## Direct RPM installation

This option is available only for RPM on a booted rpm-ostree system. An additional
confirmation explains dependency conflicts and the required restart. The operation
prepares a deployment for the next boot and refuses to overlap an existing pending
deployment or transaction. Authentication goes through rpm-ostree and polkit.

The manager removes explicitly requested packages installed by MultiPak using
`rpm-ostree uninstall`. rpm-ostree recalculates dependencies, and changes apply
after restarting. Packages present only as dependencies are not offered for removal.
MultiPak does not automatically run global `reset`, `rollback` or `cancel` operations.

## Records and container removal

Installation records are stored in `$XDG_DATA_HOME/multipak/installations/<uuid>`.
Each manifest tracks resource ownership. A process lock prevents concurrent
MultiPak changes, and shortcuts have unique names for each installation.

The manager first uninstalls the application while preserving its container and
personal data. If no other installation record references the container, it offers
to delete it in a separate confirmation. This applies to standard and custom
containers created by MultiPak. Choosing Keep container or closing the prompt
leaves it in place; reused external containers are kept.

A container with no registered applications may still contain packages installed
with other tools. Confirming deletion removes those packages too. Files in internal
directories are deleted with the container. The base image is always kept.

The home folder, including data and settings stored there, is kept by default.
A separate unchecked option allows deleting it when MultiPak can verify the
managed path and mount. Home deletion refuses symlinks, changed mounts and folders
used by another container.

Before deleting, MultiPak rechecks the container ID, ownership and remaining
installation records under the operation lock. A failed deletion keeps the
ownership record and offers a retry, including when container removal succeeded
but home-folder deletion failed.

## Cancellation and recovery

Cancelling an unfinished installation can remove an exclusive container created
for that attempt, with its shortcuts and home folder. Newly acquired images are
removed only when ownership and non-use can be verified, without force or global
pruning. Existing images are preserved.

When a container is reused or becomes shared, cancellation preserves the container,
image, home and shared dependencies. It waits for active package operations and
removes only the target package, with dependency checks enabled and no autoremove.
Packages present before the attempt are refused and are not removed by cancellation.

Failed cleanup retains records for another attempt. If temporary-file cleanup
fails after installation has been verified, MultiPak reports a warning and keeps
the successful installation status.

Distrobox integrates with host mounts, including `/run/host`. A separate home
folder does not make it a security sandbox. Install packages from sources you trust.

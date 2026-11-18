# Changelog

## 1.1.0

### New package formats

- RPM packages built for Mageia, openSUSE and ALT Linux are installed in a container of
  their own distribution, with its package manager.
  [See how it works.](docs/howrpmcompatibilityworks.md)
- Solus packages (`.eopkg`). [See how it works.](docs/howsoluscompatibilityworks.md)
- Void Linux packages (`.xbps`): glibc, musl and 32-bit (`i686`).
  [See how it works.](docs/howvoidcompatibilityworks.md)
- Alpine Linux packages (`.apk`), in the Alpine release each one was built for.
  [See how it works.](docs/howalpinecompatibilityworks.md)
- Puppy Linux packages (`.pet`), converted for the distribution they were built from.
  [See how it works.](docs/howpuppycompatibilityworks.md)
- Gentoo binary packages (`.gpkg.tar`), with dependencies from Gentoo's binary packages.
  [See how it works.](docs/howgentoocompatibilityworks.md)
- SliTaz packages (`.tazpkg`), in an image MultiPak builds from the official ISO.
  [See how it works.](docs/howslitazcompatibilityworks.md)
- Pisi Linux packages (`.pisi`). [See how it works.](docs/howpisicompatibilityworks.md)
- Slackware 15.0 packages (`.txz`, `.tgz`), in a light or a full environment.
  [See how it works.](docs/howslackwarecompatibilityworks.md)

### Snaps

- Install snaps without snapd, from a `.snap` file or by name or link from the Snap
  Store, choosing the channel. [See how this miracle works.](docs/howsnapcompatibilityworks.md)
- Bases and content snaps are downloaded and shared automatically. Snaps that need
  system-level permissions are refused before anything is downloaded.
- Background services run as services of your user; the Application Manager starts and
  stops them and shows their logs.
- Administrator commands, such as Nextcloud's occ, run as root only inside the snap's
  sandbox. Applications with a window are not offered.
- Update snaps, their bases and content from the Application Manager, and go back to
  the previous revision.
- The review page says what MultiPak cannot provide for a snap.

### AppImages

- Before installing, MultiPak checks, without running the AppImage, which libraries or
  modules it needs that the system lacks.
  [See how it works.](docs/howappimagecompatibilityworks.md)
- Missing dependencies are installed in a shared Fedora container, and the application
  is tested in an isolated copy first.
- Update AppImages from the Application Manager, from the address they indicate, their
  project page or a link you enter.

### Android

- Install Android applications through Waydroid: `.apk` files and `.apkm`, `.apks`
  and `.xapk` bundles. MultiPak reads the package's Android version requirements and
  architecture and recommends an Android version.
- Create separate Android containers, each with its own system images, applications,
  data, network and shortcuts, using a bundled modified Waydroid runtime. The system
  Waydroid installation remains available as a separate option.
- Optionally set up ARM translation for applications built only for ARM (Android 13
  on x86_64).
- Add Android containers to the Application Manager: create, open, stop, update and
  remove them. Uninstall Android applications while keeping their data, and offer to
  remove a container after its last application.
- Show the modified Waydroid runtime and LXC versions in About, with an explanation of
  the Waydroid changes.
- Associate Android package files with MultiPak. The new file type starts selected
  without changing existing choices.
- Android `.apk` apps and Alpine Linux `.apk` packages share the extension: MultiPak
  tells them apart by their contents (an Android app is a ZIP, an Alpine package a gzip
  TAR), and the Android file type is a choice of its own in the file associations.

### Application Manager

- Waydroid integration in the side bar, with the Waydroid logo as its icon.
- A new layout: a side bar with Install, Uninstall, Update, Snap services, File
  associations, Language and About, with its own icons.
- Install from a file or from the Snap Store; snaps from the snapcraft.io site open
  directly in MultiPak.
- Update lists applications, not containers, and shows which ones have a newer version,
  asking each container's package manager. Updating one updates its whole container,
  after listing the other applications that share it.
- Each application in a list has its own buttons: update, uninstall, start or stop a
  service, show its log.
- About lists Lua and popt, built into the Flatpak with RPM.

### Containers

- Only containers that MultiPak created are treated as its own. When one is renamed or
  deleted, its applications are marked "container not found" in the Application Manager
  and removed only when you ask, and a container that takes its name is never updated by
  mistake.
- When the container of an AppImage was renamed or deleted, the Application Manager
  offers to repair or delete it.

### Fixes

- Closing an update notice no longer postpones it; only Remind me later does.
- When an operation fails, details that only repeat the message are no longer offered.

## 1.0.3

- Fix RPM inspection in the Flatpak by installing the bundled RPM libraries
  in the runtime library search path.

## 1.0.2

- Fix an error that prevented the Flatpak from opening signed RPM packages,
  such as Google Chrome.
- Require removal of the old 1.0.0 Alpha 1 Flatpak and migrate its file
  associations and AppImage shortcuts.
- Correct the KDE runtime name and Kirigami version shown in About.
- Minor text corrections.

## 1.0.1

**Upgrading from 1.0.0 Alpha 1**

MultiPak 1.0.1 uses a new Flatpak app ID. Uninstall the old MultiPak before
installing 1.0.1 to avoid duplicate launcher entries and file associations that
still point to the old installer. Uninstalling the old Flatpak without deleting
its data preserves your containers and installation records. AppImage shortcuts
created by the Alpha may need to be migrated to the new Flatpak.

### MultiPak updates

- Add support for signed Flatpak updates through the system's software store.
- Show update notices in the manager and installer, with an option to postpone
  regular reminders for 24 hours.
- Require a security update before allowing new installations on versions marked
  as unsafe.

### Security

- Fix a security issue in shortcut creation.
- Prevent RPMs with misleading information from being identified as another package.
- Prevent AppImage installation from running the application's code before you
  choose to open it.
- Reject AppImages with unsafe internal file names.
- Ensure that direct system RPM installations use the verified package, even if
  the file changes while waiting for authorization.
- Prevent the privileged installer script from being replaced while authorization is pending.

### Reliability

- Preserve existing home folders when cancelling a container installation.
- Skip invalid application shortcuts without failing the installation.
- Report insufficient disk space during package installations.
- Keep package files openable when file association settings fail.
- Prevent container updates from overlapping other MultiPak operations.
- Keep temporary package files in a private directory and recognize older operations during cleanup.
- Handle container-owned files when removing a managed home folder.

### Interface

- Add an Install applications option to the Application Manager.
- Combine file type and language preferences on a single settings page.
- Show each application's icon in the uninstall list, using a generic icon when
  the application's own icon is unavailable.
- Redesign the About window.
- Use the same interface icons across Linux desktops.
- Show download progress during Distrobox installation and updates.
- Search for applications in Bazaar, GNOME Software or KDE Discover.
- Include the RPM inspection tool in the Flatpak package.
- Offer to install missing Distrobox through rpm-ostree.
- Keep System language selected by default in the language settings.
- Minor text corrections.

## 1.0.0 Alpha 1

- Initial release with a native Kirigami interface.
- Install RPM, DEB and compiled Arch Linux packages in Distrobox containers.
- Integrate type 1 and type 2 AppImages with managed copies and desktop shortcuts.
- Create standard or custom containers and reuse compatible existing containers.
- Manage registered applications and update MultiPak containers in the background.
- Offer to delete a MultiPak container after its last registered application is removed,
  with an optional checkbox to delete its managed home folder. Base images are preserved.
- Configure per-user file associations and interface language, with English and
  Brazilian Portuguese support.
- Install and remove RPM packages directly through rpm-ostree, with restart status.

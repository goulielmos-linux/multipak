# Changelog

## 1.1.0

### New package formats

- Alpine Linux packages (`.apk`). [See how it works.](docs/howalpinecompatibilityworks.md)
- Android apps (`.apk`, `.apkm`, `.apks`, `.xapk`). [See how it works.](docs/howandroidcompatibilityworks.md)
- Gentoo binary packages (`.gpkg.tar`). [See how it works.](docs/howgentoocompatibilityworks.md)
- Pisi Linux packages (`.pisi`). [See how it works.](docs/howpisicompatibilityworks.md)
- Puppy Linux packages (`.pet`). [See how it works.](docs/howpuppycompatibilityworks.md)
- RPM packages for Mageia, openSUSE and ALT Linux (`.rpm`). [See how it works.](docs/howrpmcompatibilityworks.md)
- Slackware packages (`.txz`, `.tgz`). [See how it works.](docs/howslackwarecompatibilityworks.md)
- SliTaz packages (`.tazpkg`). [See how it works.](docs/howslitazcompatibilityworks.md)
- Snaps (`.snap`). [See how it works.](docs/howsnapcompatibilityworks.md)
- Solus packages (`.eopkg`). [See how it works.](docs/howsoluscompatibilityworks.md)
- Void Linux packages (`.xbps`). [See how it works.](docs/howvoidcompatibilityworks.md)

### Waydroid modification

- MultiPak now integrates Waydroid into its workflow via a bundled, modified Waydroid
  1.6.3 runtime. You can create separate Android containers, each with its own apps and
  data, and manage them directly in the Application Manager. While Waydroid must be
  installed on your system, your existing installation remains completely separate and
  untouched.
- MultiPak also gives each Android app playing audio its own volume control in your
  desktop mixer, shows what Android apps are playing in your desktop's media controls,
  and lets you choose which webcam and microphone each container uses.
- Links work between Android and Linux:
  - From Android to Linux: web (`http://`, `https://`) and email (`mailto:`) links open in
    your Linux browser and email client.
  - From Linux to Android: custom links for individual Android apps (such as `myapp://`),
    as well as `intent://` and `market://` links used by some websites, open in Android.
  - You choose: by default, MultiPak asks which app to use when a link can open in both,
    and each Android app can also keep its links inside Android.
- File transfer now works between Android and Linux: send files from Linux via Dolphin,
  GNOME Files, or the Application Manager, and from Android using its Share menu.
- No manual setup for ARM-only apps: when you install one, MultiPak lists the ARM
  translation layer as a dependency and sets it up automatically (Android 13 on `x86_64`
  only).

[See how it works.](docs/howwaydroidintegrationworks.md)

### New features for AppImages

- MultiPak now detects the libraries and Python modules an AppImage is missing in two
  steps: first by reading the file, without running it, and then by testing the app for
  about 15 seconds in a throwaway copy of the container, with no internet or access to
  your files. Whatever is missing is installed from Fedora or PyPI into a shared
  container.
- AppImages can now be updated from the Application Manager, using the update address
  inside the AppImage, its project page or a link you provide.
- When the AppImages' container is renamed or deleted, the Application Manager offers to
  repair or delete it.

[See how it works.](docs/howappimagecompatibilityworks.md)

### Application Manager

- A new layout with a side bar, close to the style of KDE System Settings.
- Three new features:
  - Waydroid integration: manage your Android containers and their settings in one place.
  - Snap services: manage the background services of installed snaps.
  - Update now lists apps grouped into categories (such as "With updates" and "Already
    up to date"). Although updates are shown for each app, updating an app installed in
    a container still updates the whole container, so MultiPak now lists the other apps
    it affects first.

### Containers

- MultiPak now only manages the containers it created.
  [See how it works.](docs/howcontainermanagementworks.md)

### Fixes

- Closing an update notice no longer postpones it; only "Remind me later" does.
- In some situations, such as when an operation fails before anything runs, the "Show
  details" button no longer appears, since the details would only repeat the error
  message.

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

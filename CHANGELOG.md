# Changelog

## Unreleased

### AppImages

- Check, without running the application, whether an AppImage needs libraries or
  Python modules that are not installed on the system. The check reads the
  AppImage's ELF files and Python sources and asks the system's own Python which
  components exist. It stays within fixed size limits and reads one file at a time.
- When something is missing, offer to install the AppImage and its dependencies in
  MultiPak's Fedora container for AppImages, without changing the system, or to
  install it anyway. "See which" lists what is missing.
- Before a container installation, show the container and the application as on the
  review page for other formats, with the full list of required dependencies. All of
  them are requested in the container, since it can lack components the system has.
- Optionally, after a container installation, start the application for a few
  seconds in a throwaway copy of the container without network or access to personal
  files, and install other dependencies it reports as missing. Report when the check
  cannot confirm that the application starts, and always remove the copy, even after
  cancelling.
- Open and update the AppImage container only while its name still leads to the
  container MultiPak created.
- Offer to remove the AppImage container after its last application is uninstalled,
  and include it in the container update list.
- When Fedora does not package a Python module an AppImage needs, install it in the
  AppImage container from PyPI. Only ready-made wheels are used, and only after the
  downloaded wheel is found to contain the module the application imports.
- Show the completion screen with a warning when a dependency could not be installed,
  and name dependencies as Python modules and libraries instead of package capabilities.
- Update AppImages from the Application Manager. MultiPak looks for new versions at the
  address saved for the application, in the update information embedded in the
  AppImage, or on the project page in its AppStream metadata once a published file
  matches the installed copy. When none of these exists, the user can enter the address.
  Versions are compared by file, not by version number, and results are kept for six
  hours unless the list is reloaded.
- Before an update, ask to close the application if it is running, and confirm new
  dependencies the new version needs. The downloaded file must be the same application;
  the previous copy is restored if the update fails.

### MultiPak updates

- Only postpone update notices when Remind me later is selected.

### Containers

- Only containers that MultiPak created are treated as its own. A container that
  someone else created with a MultiPak name is no longer reused, listed or updated as
  MultiPak's.
- When a default name such as MultiPak-Fedora-45 is taken by another container, create
  the container with the lowest free name (C2-, C3-...) instead of stopping, and keep
  using it afterwards. The same applies to the AppImage container.
- A container for RPM, DEB or Arch packages that is renamed after MultiPak created it,
  including one with a custom name, is no longer treated as MultiPak's. A new one is
  created when needed.
- Forget the applications of such a renamed or deleted container: they leave the
  uninstall list and their shortcuts, which no longer work, are removed. Nothing inside
  the container is changed.
- The AppImage container is taken back instead: when it was renamed, MultiPak gives it
  its name again before using it, rather than creating a second one.
- When the container of an AppImage was renamed or deleted, the Application Manager
  shows a notice with Repair and Delete. Repair first explains what it will do: take
  the renamed container back, create it again, or, when the system now has every
  dependency, open the AppImage directly on the system. Delete removes MultiPak's copy
  of the AppImage with its shortcut and icon, and leaves the container alone.
- Opening such an AppImage from its shortcut shows a desktop notification that points
  to the Application Manager.
- Do not update a container recorded by MultiPak if another container has taken its
  name.

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

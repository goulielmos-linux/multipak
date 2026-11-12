# Changelog

## 1.0.1

**Upgrading from 1.0.0 Alpha 1**

MultiPak 1.0.1 uses a new Flatpak app ID. Uninstall the old MultiPak before
installing 1.0.1 to avoid duplicate launcher entries and file associations that
still point to the old installer. Uninstalling the old Flatpak without deleting
its data preserves your containers and installed applications.

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

# Release validation

## Automated checks

The README lists commands for translation validation, Ruff, pytest and package
builds. During development, run the checks relevant to the change. Before a
release, run the complete set and record the results.

Tests use temporary data and configuration directories with simulated package
operations. AppImage fixtures contain test entry points. General tests default
to English; localization tests also check Portuguese text and language switching.

Coverage includes:

- Package metadata limits, invalid files, DEB compression variants, RPM queries
  and Arch PKGINFO.
- AppImage extraction, FUSE fallback and managed-path boundaries.
- Subprocess arguments, errors, timeouts and cancellation.
- Container reuse, ownership, retry snapshots, shared dependencies and cleanup failures.
- Direct rpm-ostree state handling, associations, preferences and localization.

`tests/test_container_removal.py` covers optional removal with simulated Podman,
including ownership changes, new references, failed deletion and retry. Home-folder
checks cover explicit selection, symlink boundaries, shared mounts and partial failure.

Scripts under `checks/native_ui` cover pages, dialogs, worker signals, updates,
close blocking and confirmation defaults. Language checks cover system fallback,
selection, immediate retranslation, persistence and failed preference saves.
They require matching system Qt, PySide6 and Kirigami. Offscreen rendering checks
window contents; desktop window frames still need manual verification.

## Manual desktop validation

Use a disposable Bazzite VM with snapshots for direct system operations.

1. Install the Flatpak as a regular user. Open the manager from the launcher and
   packages from Dolphin.
2. Install trusted graphical RPM, DEB and Arch packages in their supported distributions.
3. Reuse a compatible Kontainer environment and verify shortcuts and shared data.
4. Test incompatible architectures, missing dependencies and packages without shortcuts.
5. Disconnect before image acquisition and during downloads; reconnect and retry, then cancel.
6. Test cancellation, close blocking and recovery after abrupt process termination.
7. Check preservation of pre-existing containers, images and shared dependencies.
   Refuse a target package that was installed before the attempt.
8. Have another container use a newly acquired image; verify a failed removal is reported.
9. Test native and Flatpak Bazaar discovery and fallback when `--search-for` is unavailable.
10. Test direct RPM installation and removal, polkit authentication, pending deployments and reboot.
11. Test type 1 and type 2 AppImage extraction, host launch and uninstallation in
    the Flatpak. For native development builds, also check working and missing FUSE.
12. Verify all file associations, including executable AppImages in Dolphin.
13. Update a MultiPak container and check logs, completion, retry and close blocking.
14. Switch languages, restart both applications and test an unsupported system locale.
15. Test light and dark themes, Wayland and X11, keyboard navigation, long names and small screens.
16. Remove the last registered application from a standard or custom MultiPak container.
    Test keeping the container, closing the prompt, explicit deletion and retry after failure.
    Check that base images and home folders remain by default and external containers are
    never offered for deletion. With home deletion selected, verify that only the managed
    home is removed.

Report simulated checks separately from real desktop results. Record which parts
of the manual validation were completed, with the packages, versions and evidence
needed to reproduce them.

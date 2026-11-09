# Architecture

MultiPak's Flatpak groups two desktop applications backed by the same Python package:

- The Installation Wizard handles package files opened from a file manager.
- The Application Manager provides uninstallation, container updates and preferences
  through its application-menu entry.

Both share the installation records and backend modules described below.

For local development, the `multipak-installer` and `multipak-manager` entry points
call `launcher.py`,
which starts the system Python interpreter with `kirigami/bootstrap.py`.
The bootstrap loads MultiPak from its installed package and uses system PySide6
and Kirigami. The desktop IDs are `io.github.multipak.installer` and
`io.github.multipak.manager`.

Flatpak builds use Python and Qt from KDE Platform and PySide6 BaseApp. The manager
keeps the desktop ID above; the wizard is exported as
`io.github.multipak.manager.installer`. `host.py` routes host tools through
`flatpak-spawn` and supplies shared paths for container operations and desktop
integration.

## Modules

Paths below are relative to `src/multipak`.

| Module | Responsibility |
| --- | --- |
| `kirigami/main.py`, `kirigami/live.py`, `kirigami/configuration.py` | Installer state and user choices |
| `kirigami/manager.py` | Installed applications, updates and associations |
| `kirigami/*.qml` | Pages, dialogs and presentation |
| `kirigami/about.py` | About-window data and backend versions |
| `localization.py`, `kirigami/languages.py` | gettext catalogs, saved language and Qt bridge |
| `worker.py` | Threaded operations and cancellation |
| `models.py`, `catalog.py` | Data types, base catalog and image resolution |
| `metadata.py`, `appimage.py` | Package inspection and AppImage management |
| `engine.py` | Container installation, retries, verification and cleanup |
| `containers.py` | Container ownership and confirmed removal |
| `process.py` | Subprocess arguments, deadlines, output and cancellation |
| `host.py` | Flatpak host commands, host XDG directories and shared temporary files |
| `environments.py`, `environment_choices.py` | Discovery and compatible container choices |
| `guest.py` | Standalone helper executed inside the container |
| `desktop.py` | Application shortcuts and container icons |
| `store.py` | Records, validated paths and operation locks |
| `system.py` | rpm-ostree installation and removal |
| `updates.py` | Background Distrobox updates |
| `file_associations.py`, `integration.py` | File associations and desktop registration |
| `bazaar.py`, `external_apps.py` | External application integration |

The frontend submits operations to a worker. The core runs outside the UI thread
and reports results through Qt signals without accessing widgets. Core localization
uses the standard library. The copied `guest.py` helper remains standalone and
emits English technical diagnostics.

## Records and ownership

Each installation has a UUID directory under the host user's
`$XDG_DATA_HOME/multipak/installations`
containing its manifest and log. Temporary package copies are removed after success.
Container homes live outside individual transaction directories, so removing one
record cannot delete another application's shared home. Managed AppImages use
`$XDG_DATA_HOME/multipak/appimages/<id>`.

Container ownership is stored independently at
`installations/environments/<name>/container.json`, so it survives removal of the
application that created the container. Optional deletion requires confirmation
and rechecks identity, ownership and references under the operation lock. It removes
the confirmed container ID, owned shortcut and record while preserving the base image.
Home deletion requires a separate unchecked option, a verified managed path and
mount, and no other container using the folder. Otherwise the home is kept.
Existing owned shortcuts can establish ownership through their recorded container
ID; a name alone cannot.

Manifests use temporary files, synchronization and atomic replacement. A `flock`
protects MultiPak changes across processes; external tools remain independent.
Owned resources are recorded before writing. Shortcuts use UUID-based names, and
cleanup validates paths before selecting host files.

## Operation states

Container installation follows `prepared → creating → installing → installed`.
Failures record `failed`; interruption can record `interrupted`. Cleanup uses
`cleaning` and `cleanup_failed`. Retained records support recovery after failure.

Retry verifies the retained package SHA-256, container ID and ownership. Reuse also
rechecks distribution and version. An exclusive container needs the original
operation label; a reused container must still be managed by Distrobox. Retry does
not roll back package scripts.

System installations use `method=system` and separate states. Container cleanup
cannot remove a system deployment. Direct RPM removal verifies rpm-ostree's
resulting state.

## Processes and progress

Commands use argument arrays, closed stdin and a noninteractive environment.
The runner manages process groups and deadlines without shell interpolation of
package or user input.

Cancellation in shared containers waits for the current package step. The guest
helper checks PID and process start time through `/proc` to account for PID reuse.
Cleanup refuses active operations and removes only the target package with
dependency checks, retaining failures for diagnosis.

Installer progress represents weighted stages. Verified success reaches 100%.
Updates show indeterminate activity and process output. Windows block closing
while an operation must finish or complete cancellation.

## Desktop integration

The guest helper lists files belonging to the selected package. Exported `.desktop`
commands and actions receive a Distrobox prefix. Guest-only checks and working
directories are removed, and `DBusActivatable` is disabled. AppImage shortcuts launch
the managed copy or extracted entry point.

Ownership tracking helps prevent accidental removal of unrelated resources. It
does not isolate malicious code running as the same user. Distrobox's separate home
is organizational, not a security sandbox. Cleanup does not use global image pruning,
dependency autoremove or system reset.

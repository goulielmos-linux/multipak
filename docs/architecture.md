# Architecture

The Installation Wizard and Application Manager share a Python backend and
installation records. Their Kirigami interfaces send operations to a worker
thread and receive progress and results through Qt signals.

The native build uses system Python and Qt. The Flatpak provides its own runtime
and routes tools such as Distrobox and Podman through `flatpak-spawn`.

## Main components

Paths are relative to `src/multipak`.

| Area | Main modules |
| --- | --- |
| Interface and preferences | `kirigami/`, `localization.py`, `file_associations.py` |
| Package inspection and installation | `metadata.py`, `engine.py`, `appimage.py`, `system.py` |
| Container discovery and management | `catalog.py`, `environments.py`, `containers.py`, `updates.py` |
| Process execution and progress | `worker.py`, `process.py`, `host.py`, `container_progress.py` |
| Records and desktop integration | `store.py`, `desktop.py`, `guest.py` |

## Records and recovery

Each installation has its own manifest and log under
`$XDG_DATA_HOME/multipak/installations`. A process lock prevents concurrent
MultiPak changes. Retry checks the saved package hash and verifies the container
before resuming. Reuse also checks the distribution and version.

Container ownership is recorded separately, so it survives removal of the first
application. A container name alone does not prove ownership. Before deleting a
container, MultiPak checks its identity, ownership and remaining references.
It keeps the base image and home folder by default. Deleting the home requires
a separate confirmation and a verified, unshared path.

Cancellation preserves shared containers and dependencies. If a package operation
is still running in a shared container, MultiPak waits for it to finish before
removing the target package. Failed cleanup leaves a record for retry.

## Package operations

Distrobox installs packages and their dependencies inside the container. Arch
installations run a full package upgrade first to avoid a partial upgrade.
MultiPak exports application shortcuts to the desktop. AppImage shortcuts launch
the extracted entry point.

Podman and the package manager can report download progress during installation
and container updates. When no percentage is available, the interface shows
indeterminate activity. The installer reports success after verification.

Direct RPM installation is separate from container operations. The privileged
helper reads the staged file with the requesting user's permissions, rejects
symlinks and nonregular files, and verifies the hash of a private copy before
passing it to rpm-ostree.

Distrobox shares resources with the host and is not a security sandbox.

# Distribution

Flatpak is MultiPak's distribution format. The Installation Wizard and Application
Manager share the same package.

## Build the Flatpak

The manifest is `packaging/io.github.multipak.manager.json`. It uses KDE Platform
6.11 and the matching PySide6 BaseApp. Both desktop applications share the Flatpak
ID `io.github.multipak.manager` and the `stable` branch.

Run the build commands in a host terminal. Install the build tools and dependencies:

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.flatpak.Builder org.kde.Sdk//6.11 io.qt.PySide.BaseApp//6.11
```

From the project directory, build the repository and installable bundle:

```bash
python3 scripts/build_flatpak.py
```

The result is `dist/flatpak/multipak-1.0.0a1-x86_64.flatpak` on x86-64 machines.
Use `--output /path/to/build-directory` to choose another destination. Translation
catalogs are compiled during the build. Source dependencies have pinned checksums.

## Install and open

```bash
flatpak install --user dist/flatpak/multipak-1.0.0a1-x86_64.flatpak
flatpak run io.github.multipak.manager
```

The Application Manager appears in the desktop launcher. Opening a supported
package file starts the Installation Wizard. To open it explicitly:

```bash
flatpak run --file-forwarding --command=multipak-installer io.github.multipak.manager @@ /path/to/application.deb @@
```

The exported desktop entries are `io.github.multipak.manager.desktop` and
`io.github.multipak.manager.installer.desktop`. On first launch, MultiPak
registers file associations for all supported formats. Later changes made in
the manager or through the desktop's preferences are preserved.

## Host integration

Distrobox and rootless Podman run on the host. RPM inspection uses the host's
`rpm`; direct RPM installation additionally needs a booted rpm-ostree system and
its session polkit agent. Python, PySide6, Kirigami and the AppImage readers are
provided by the Flatpak and its runtime.

The home-directory permission gives access to package files, installation records,
container homes and desktop shortcuts. Access to `org.freedesktop.Flatpak` runs
host commands through `flatpak-spawn --host`. Host package-manager operations use `setsid` from util-linux for process
supervision, preserving streamed output, deadlines and cancellation. Optional Bazaar and Kontainer
installations are also discovered and opened on the host.

Installation records and managed application files use the host user's
`$XDG_DATA_HOME/multipak`, including records from local development installations.
Sandbox-only helper files are copied to temporary locations visible to host tools.
AppImages are extracted before integration in Flatpak builds and launched on the
host through the `multipak-appimage` command.

## Updates

A bundle can be built and installed without a GitHub repository. Automatic updates
require a Flatpak repository, such as Flathub or a hosted repository with a signed
update feed. The source-code repository and the Flatpak update repository serve
different purposes. Keep the application ID stable when configuring publication.

## Development tools

The README describes the current local development setup. The Python installer
creates a private runtime and registers desktop launchers for the current user.
The interface uses system-provided Qt, PySide6 and Kirigami.

The repository also contains a `Containerfile` for Bazzite integration experiments.
It starts from `ghcr.io/ublue-os/bazzite:stable`, installs a private Python runtime
under `/usr/libexec`, registers launchers under `/usr/bin` and adds desktop
integration. Podman, Distrobox and rpm-ostree come from the base image.

```bash
podman build -f Containerfile -t localhost/bazzite-multipak:test .
```

Use a disposable VM to test the resulting image.

## Release checks

The GitHub workflow at `.github/workflows/check.yml` runs style checks, translation
validation, tests and Python package builds on pushes and pull requests.

Before a release, validate the Flatpak on a real desktop using the scenarios in
[Testing](testing.md). Include file associations, language selection, host-tool
access, application shortcuts and the installation and removal flows.

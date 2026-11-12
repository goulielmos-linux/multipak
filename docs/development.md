# Local development

## Run locally

Use Linux with Python 3.11+ and matching system versions of Qt, PySide6, Kirigami
and Kirigami Addons. Container installs require Distrobox 1.8+ and rootless
Podman. Native RPM inspection requires `rpm`; AppImage extraction requires
`unsquashfs` for type 2 or `7zz`, `7z` or `bsdtar` for type 1.

From the project directory, run without sudo:

```bash
python3 scripts/install_local.py
```

Open MultiPak Application Manager from the application launcher or open a
supported package file to start the wizard.

## Development checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/translations.py --template
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
.venv/bin/python -m build
```

## Interface

QML files and Python controllers live in `src/multipak/kirigami/`.
`Welcome.qml` opens the wizard and `Manager.qml` opens the application manager.
Both share `AboutWindow.qml`.

Use the [native interface checks and previews](../checks/native_ui/README.md)
to verify light and dark themes, text wrapping and small windows.

## Translations

Write source messages in English. Use `tr()` in Python, `mark()` for constants
translated later, and `qsTr()` in QML. Keep numbered placeholders intact.

Catalogs are stored in `src/multipak/locales/<locale>/LC_MESSAGES/multipak.po`.
After changing source messages, run the command below to refresh the template,
update the catalogs, then run it again to validate and compile the translations.

```bash
python3 scripts/translations.py --template
```

## Build the Flatpak

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.flatpak.Builder org.kde.Sdk//6.11 io.qt.PySide.BaseApp//6.11
python3 scripts/build_flatpak.py
```

To install and open the test build on x86-64:

```bash
flatpak install --user dist/flatpak/multipak-1.0.2-x86_64.flatpak
flatpak run io.github.goulielmos_linux.multipak
```

## Remove the direct RPM component

If you used direct RPM installation, uninstalling MultiPak leaves its system
component in place. To remove it after uninstalling MultiPak, run:

```bash
sudo rm -rf -- /usr/local/libexec/multipak-system
sudo rm -f -- /usr/share/polkit-1/actions/io.github.multipak.system-install.policy
```

These commands remove only MultiPak's component and its authorization policy.
RPMs installed on the system remain installed.

## Release checks

Run the automated checks and test the built Flatpak on a real desktop:

- Check first launch, file associations, language selection and store search.
- Install and remove each supported package format. Test RPM inspection on a
  host without `rpm`.
- Check uncached image downloads, container updates, cancellation and cleanup.
- In a disposable VM, verify missing Distrobox and direct RPM installation on
  rpm-ostree systems.
- Verify signed updates and the transition from the previous release.

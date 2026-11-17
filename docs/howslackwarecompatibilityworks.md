# How MultiPak installs Slackware packages

*Translated from the [Portuguese version](howslackwarecompatibilityworks.pt-BR.md),
which is the reference text.*

*MultiPak 1.1.0.*

---

## 1. In plain terms

### 1.1 What is a Slackware package?

Slackware is the oldest Linux distribution still active. A package is just a compressed
TAR archive (`.txz`, `.tgz`, `.tar.xz` or `.tar.gz`) with the program's files and an
`install/` folder holding the description (`slack-desc`) and, sometimes, a script
(`doinst.sh`). The file name has a fixed form: `name-version-architecture-build.txz`.

Slackware **does not record dependencies**: a package does not say which other packages
it needs to work. Normally, whoever installs it has to find and install those packages;
in MultiPak, it does this itself (see step 4 below).

### 1.2 What does MultiPak do?

1. **Recognizes the package without running it:** the file is accepted only if its name
   has the form `name-version-arch-build` and `install/slack-desc` describes that same
   package. Double-clicking opens only `.txz` and `.tgz` files with a package name in
   MultiPak; other compressed archives keep opening with their usual program.
2. **Chooses the environment:** light, the default, or full (see 1.3).
3. **Trusts Slackware's key:** the image does not include the signing key, and without
   it slackpkg refuses everything. MultiPak imports a copy of the official key before
   the first package.
4. **Installs with `installpkg`** and looks for missing libraries: it runs `ldd` on the
   installed programs, finds in Slackware's file lists which official package provides
   each library, and installs it with `slackpkg`, repeating until nothing is missing. It
   also adds data that `ldd` does not see (keyboard data, fonts, icons), without which
   GTK does not even start.
5. **Adds menu shortcuts.** If any library was not found, MultiPak warns you and
   suggests the full environment.

### 1.3 The container

Two environments, built from the same small image (`docker.io/aclemons/slackware:15.0`):

| Environment | Container | What it includes |
| --- | --- | --- |
| light (default) | `MultiPak-Slackware-15.0` | only what the application needs |
| full | `MultiPak-Slackware-15.0-full` | also Slackware's base, libraries, network and X11 series (about 1.2 GB more to download, around 6 GB on disk) |

### 1.4 Updating and uninstalling

- **Update**: under **Manager → Update**, MultiPak updates the whole container with
  Distrobox's own command (`distrobox upgrade`), which uses slackpkg.
- **New versions**: for each application, the Manager compares the installed version
  with the newest one in Slackware's package list; a fix published after the release
  (the `patches` series) counts as the newest. It then lists each application under
  **With updates**, **Already up to date** or **No update source** (installed from a
  file that no repository offers). On opening, it uses the list the container already
  has, without downloading anything; **Check for updates** runs `slackpkg update` first.
- **Uninstall**: `removepkg`. Since Slackware does not record dependencies, only the
  application's package is removed; the libraries MultiPak added stay.

### 1.5 Known limitations

- Packages built for other Slackware versions (such as 14.2 or *-current*): MultiPak
  offers only 15.0, the current stable release. Since a package does not say which
  version it was built for, all of them go to 15.0, and those may not work.
- The container image is not official, because Slackware does not publish one. MultiPak
  uses the one by `aclemons`, which is still maintained; the one Distrobox lists (by
  `vbatts`) has not been updated since 2023.
- Programs that need a library official Slackware does not have (for example, one that
  only exists on SlackBuilds.org, a community repository): MultiPak cannot find that
  library and warns you.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `slackware.py` | file name, `slack-desc`, `slack-required`, `install_name` |
| `metadata.py` | detection after Arch, Puppy and Gentoo |
| `models.py` (`FULL_VARIANT`, `FULL_LABEL`, `slackware-15.0[-full]` environments) | environments |
| `engine.py` (`slackware_key_hook`, `_install_slackware`) | key and installation |
| `guest.py` (`slackware_dependencies`, `missing_libraries`, `slackware_providers`) | ldd + file lists |
| `assets/slackware-gpg-key.asc` | official public key (EC56 49DA 401E 22AB FA67 36EF 6A44 63C0 4010 2233) |
| `assets/multipak-slackware.xml` | MIME for `.txz`/`.tgz` with a package name |
| `kirigami/EnvironmentPage.qml`, `DependenciesDialog.qml` | light/full choice and dependencies |

### 2.2 Reading

`slackware.is_candidate` requires a known compression and a `name-version-arch-build`
name. `read_info` decompresses, with a limit, until it finds `install/slack-desc`
(`makepkg` sorts the archive, so the `install/` entries are together) and checks that
the description is for the same package. `slack-required` (from third-party
repositories) is read if present: alternatives and version constraints are discarded.
Accepted architectures: the machine's, `noarch` and `fw` (firmware). Hint
`slackware-15.0`, **Medium** confidence (the package does not state its release).

### 2.3 Creating the container

`slackware_key_hook` writes the key to the container's home and imports it into the
home keyring (used by Distrobox's setup) and root's (used later by `sudo slackpkg`).
Slackware has no `/usr/lib/os-release`; `read_os_release` falls back to
`/etc/os-release`. The full environment gets the `FULL_LABEL` label. Slackware 15.0's
Python is 3.9: `guest.py` uses `from __future__ import annotations`.

### 2.4 Installation (`engine._install_slackware`)

1. `slackpkg -batch=on -default_answer=y update`.
2. `installpkg <file>` — `installpkg` requires the original name, so the copy uses
   `Transaction.package_file` (`install_name`).
3. `guest.py --slackware-dependencies light|full <name> [slack-required…]`:
   - `GRAPHICAL_DATA` (xkeyboard-config, dejavu-fonts-ttf, hicolor/adwaita icon theme,
     gsettings-desktop-schemas, shared-mime-info, dconf) and `slack-required`;
   - in the full environment, the `a ap l n x xap` series without kernels and boot
     loaders;
   - up to 10 rounds: `ldd` on up to 400 ELF files in the package (`=> not found`), a
     search in slackpkg's `*-filelist.gz` (patches, slackware64, extra, pasture series),
     and installation of the providers. The list is computed beforehand because
     `slackpkg install` exits with 20 when there is nothing to install.
4. Libraries not found → a warning to use the full environment.

### 2.5 Inside the container (`guest.py`)

`/var/log/packages/<name>-<version>-<arch>-<build>` for state and files. New versions:
compares `/var/log/packages` with `/var/lib/slackpkg/pkglist`. Removal: `/sbin/removepkg
<name>` (preceded by the `puninstall.sh` of a converted `.pet`, if any).

### 2.6 How this differs from plain Slackware

| Topic | Plain Slackware | MultiPak |
| --- | --- | --- |
| Dependencies | up to the user | ldd + official file lists |
| GPG key | `slackpkg update gpg` | bundled copy, imported in the hook |
| System | full installation | light (only what is needed) or full |

### 2.7 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log` (packages added and
  missing libraries).
- `distrobox enter MultiPak-Slackware-15.0 -- ldd /usr/bin/<program> | grep 'not
  found'`.
- If something is missing, reinstall in the full environment.

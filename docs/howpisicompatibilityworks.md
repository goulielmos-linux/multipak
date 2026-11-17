# How MultiPak installs Pisi Linux packages

*Translated from the [Portuguese version](howpisicompatibilityworks.pt-BR.md), which is
the reference text.*

*MultiPak 1.2.0.*

---

## 1. In plain terms

### 1.1 What is a `.pisi` package?

Pisi Linux continues Pardus, a Turkish distribution. Its package manager, **pisi**, is
written in Python (still Python 2.7). A `.pisi` package is a ZIP file with the
`metadata.xml` file (name, version, dependencies, distribution), the `files.xml` list
and the program's files in `install.tar.xz`. Solus uses the same format under another
name (`.eopkg`; see [How MultiPak installs Solus
packages](howsoluscompatibilityworks.md)).

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** only the `metadata.xml` file. It accepts
   Pisi Linux packages in version 1.2 of the format; the metadata names the
   distribution, whatever the file is called.
2. **Creates (or reuses) the container** `MultiPak-Pisi-Linux`, from the official image
   `docker.io/pisilinux/chroot:base`.
3. **Prepares the container:** Distrobox does not know pisi. MultiPak adds the
   repository, updates the image and installs Python 3, sudo, keyboard data, fonts and
   the system language (if Pisi Linux does not have that language, applications open in
   English).
4. **Installs with pisi**, which downloads dependencies from the official repository.
5. **Adds menu shortcuts.**

### 1.3 The container

- A single environment, `pisilinux-rolling` ("Pisi Linux (rolling release)").
- pisi runs package scripts through a service (COMAR) that needs the **system D-Bus**.
  Distrobox does not share the computer's, so MultiPak starts a D-Bus of its own inside
  the container when needed.
- The binary repository is `https://beta.pisilinux.org/pisi-index.xml.xz` (the same one
  the image uses). It is not signed: trust comes from HTTPS.

### 1.4 Updating

`distrobox upgrade` does not know pisi. **Manager → Update** (the icon of any
application in the container, or **Update all**) runs MultiPak's script: it starts
D-Bus, then runs `pisi update-repo` and `pisi upgrade`.

**New versions**: the Manager queries `pisi list-upgrades` and `pisi info` to find which
applications have a new version and lists each one under **With updates**, **Already up
to date** or **No update source** (installed from a file that no repository offers).
On opening, it uses the list the container already has, without downloading
anything; **Check for updates** runs `pisi update-repo` first.

### 1.5 Uninstalling

When you uninstall an application, MultiPak removes its package with `pisi remove`.
pisi would also delete any other application that depends on that package, so MultiPak
checks first: if another application needs it, the uninstallation is refused and nothing
is deleted.

### 1.6 Known limitations

- `.pisi` packages from other distributions, such as the old Pardus: MultiPak accepts
  only those from Pisi Linux and Solus.
- The repository has known dependency cycles and file conflicts; the setup works around
  these problems, but a broken repository may prevent the installation.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `pisi.py` | `metadata.xml` reader (Pisi Linux and Solus; refuses DOCTYPE, delta and format ≠ 1.2) |
| `metadata.py` | ZIP (`PK\x03\x04`) → `pisi.read_info` → `pisi` kind |
| `models.py` (`PISI_ENVIRONMENT`, `ROLLING_FAMILIES`) | `pisilinux-rolling`, `MultiPak-Pisi-Linux` |
| `engine.py` (`pisi_setup_hook`, `PISI_INSTALL`, `_install_pisi`) | hook and installation |
| `assets/pisi-setup.sh` | pre-init hook and `--upgrade` |
| `guest.py` (`pisi_entry`, `pisi_files`, `pisi_dependents`, `pisi_bus`) | state, files, D-Bus, removal |
| `container_progress.py` | "Downloading/Installing N / M" lines → percentage |
| `updates.py` (`SCRIPTS["pisilinux"]`) | updating |
| `assets/multipak-pisi.xml` | MIME `application/x-pisi-package` (`*.pisi`) |

### 2.2 Detection and environment

Pisi Linux packages are ZIP files, like Android applications. A ZIP file with the `.apk`
extension goes to the Android path; the others go to `pisi.read_info`. *Distribution*
"Pisi Linux" → `pisi`; "Solus" → `eopkg`. Hint `pisilinux-rolling`, **High** confidence.
`ROLLING_FAMILIES` makes the container accept any `VERSION_ID` (os-release says
`ID=pisilinux`, `VERSION_ID=2.4.4`, which changes with every update).

### 2.3 Pre-init hook (`pisi-setup.sh`)

Runs before Distrobox's package manager check (which would exit with 127):

1. Starts `dbus-daemon --system --fork`, first deleting a stale socket/pid from an
   earlier start (it would refuse connections).
2. `pisi add-repo beta …` if missing.
3. `pisi upgrade --ignore-dependency` (the repository has the `libutil-linux` →
   `python3` cycle), then `python3` alone, then python3, sudo, util-linux, shadow, mesa,
   vulkan, xkeyboard-config, dejavu-fonts, glibc-locales-en and others. Options
   `--ignore-safety` (otherwise pisi wants the whole `system.base`) and
   `--ignore-file-conflicts` (false directory conflicts, such as
   `/usr/share/man/pt_BR/man5`).
4. The host's language (`/run/host/etc/locale.conf`): `glibc-locales-<language>` and
   `localedef` (without it, GTK falls back to C).
5. `@includedir /etc/sudoers.d` in `/etc/sudoers` (Pisi Linux's does not read that
   folder, where Distrobox grants the user sudo).
6. `/.containersetupdone`, so Distrobox skips its package step.

### 2.4 Installation (`engine._install_pisi`)

1. `guest.py --pisi-bus` (starts D-Bus if it is not running).
2. `pisi update-repo --no-color`.
3. `pisi install --no-color --yes-all --ignore-safety <file>` (`PISI_INSTALL`). pisi
   accepts the renamed file.

### 2.5 Inside the container (`guest.py`)

Database `/var/lib/pisi/package/<name>-<version>/` (`metadata.xml`, `files.xml`).
`pisi_dependents` reads the `RuntimeDependencies` of installed packages; with
dependents, it refuses; otherwise `pisi_bus()` and `pisi remove --no-color --yes-all`.

### 2.6 Updating

`updates.SCRIPTS["pisilinux"]` copies the script to
`/usr/libexec/multipak-pisi-setup.sh` and runs `--upgrade`: D-Bus, `pisi update-repo`,
`pisi upgrade` with the same options.

### 2.7 How this differs from plain Pisi Linux

| Topic | Plain Pisi Linux | MultiPak |
| --- | --- | --- |
| D-Bus | the system's | its own, in the container |
| Safety switch | on | `--ignore-safety` (like the image build) |
| Removing with dependents | removes them too | refuses |

### 2.8 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Pisi-Linux -- pisi list-installed`.
- COMAR/D-Bus error: `distrobox enter MultiPak-Pisi-Linux -- ls -l /run/dbus`.

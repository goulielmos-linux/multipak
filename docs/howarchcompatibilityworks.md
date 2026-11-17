# How MultiPak installs Arch Linux packages

*MultiPak 1.2.0. Translated from the [Portuguese version](howarchcompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is an Arch Linux package?

Arch Linux is a rolling-release distribution. Its package manager is **pacman**, and a
package is a TAR archive, usually compressed with zstd (`.pkg.tar.zst`), holding the
program's files and a `.PKGINFO` file (name, version, architecture, dependencies).

Distributions derived from Arch, such as Manjaro, EndeavourOS and CachyOS, use the same
format, and `.PKGINFO` does not say which of them a package came from.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** it reads only the `.PKGINFO` file. Nothing
   is extracted and no script runs. Source packages are refused: only prebuilt packages.
2. **Creates (or reuses) the container** `MultiPak-Arch-Linux`, from the official image
   `docker.io/library/archlinux`.
3. **Updates the container before installing:** on Arch, installing a package without
   updating the rest of the system can break programs. So MultiPak updates the whole
   container first.
4. **Installs with pacman**, which downloads the dependencies from the official
   repositories.
5. **Adds menu shortcuts.**

### 1.3 The container

- A single environment, `arch-rolling` ("Arch Linux (rolling release)"), with the
  `MultiPak-Arch-Linux` container.
- Distrobox knows pacman and prepares the container by itself.
- The suggestion has **Medium** confidence: `.PKGINFO` shows the package belongs to
  the Arch family, but not whether it comes from Arch or from a derivative.

### 1.4 Updating and uninstalling

- **Update**: under **Manager → Update**, MultiPak updates the whole container with
  Distrobox's own command (`distrobox upgrade`), which uses pacman.
- **New versions**: for each application, the Manager compares the installed version
  with the newest one in the repositories. To avoid leaving the container in a
  partially upgraded state, it queries a separate copy of the package lists, without
  touching the container's own. It then lists each application under **With updates**,
  **Already up to date** or **No update source** (installed from a file that no
  repository offers).
- **Uninstall**: when you uninstall an application, MultiPak removes its package with
  `pacman -R`, which refuses if another package depends on it. Dependencies the
  application brought in are not removed with it (a general MultiPak rule).

### 1.5 Known limitations

- Packages from derivatives that depend on packages only the derivative has (for
  example, from Manjaro's or CachyOS's repositories): pacman cannot find those
  dependencies on Arch.
- CachyOS packages optimized for newer processors (`x86_64_v3` and `x86_64_v4`
  architectures): MultiPak refuses them, since they are not plain `x86_64` and would
  crash on older processors.
- AUR packages in source form (`PKGBUILD`): only prebuilt packages.
- Packages built for another architecture: MultiPak accepts only packages for the
  computer's architecture (such as `x86_64`) or for any architecture (`any`).

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `metadata.py` (`_arch_fields`, `_tar_stream`) | decompression and reading `.PKGINFO` |
| `models.py` (`ROLLING_FAMILIES`) | `arch-rolling`, `MultiPak-Arch-Linux` |
| `catalog.py` | the `docker.io/library/archlinux:latest` image |
| `suggestions.py` | Medium confidence and the note about derivatives |
| `engine.py` (`_install_arch`) | `pacman -Syu` and `pacman -U` |
| `package_check.py` (`check_pacman`) | new versions with a separate database |
| `guest.py` | state (`pacman -Q`), files and removal |
| `assets/multipak-packages.xml` | MIME `application/x-archlinux-package` |

### 2.2 Reading and detection

`_tar_stream` recognizes zstd, gzip, xz and bzip2 by their magic numbers, or an
uncompressed TAR. The TAR archive is read as a *stream*, with a read limit, up to
`.PKGINFO`. `pkgname`, `pkgver` and `arch` are required and must be unique;
`xdata = pkgtype=src` (a source package) is refused. Dependencies shown: the `depend`
lines. During detection, Alpine and Void are checked **before** Arch, because they are
compressed TAR archives too; Arch is what is left. Accepted architectures: the machine's
and `any`. Hint `arch-rolling`, **Medium** confidence.

### 2.3 Creating the container

`distrobox create` with the Arch image and `--additional-packages python` (on Arch, the
Python 3 package is called `python`). `ROLLING_FAMILIES` makes the container accept
Arch's os-release, which has no `VERSION_ID`. Old containers named
`MultiPak-Arch-rolling` (1.0.0 Alpha 1) are still recognized.

### 2.4 Installation (`engine._install_arch`)

1. `pacman -Syu --noconfirm`: refreshing the indexes without upgrading the packages
   would create a partial upgrade, which Arch does not support.
2. `pacman -U --noconfirm <file>`.

### 2.5 Inside the container (`guest.py`)

State: `pacman -Q`. Files: `pacman -Qlq`; the `.desktop` files found become shortcuts.
Removal: `pacman -R --noconfirm name`, without `-s` (dependencies stay) and without
forcing.

### 2.6 New versions (`package_check.check_pacman`)

Like `checkupdates`, it uses a temporary database: it links the local database
(`/var/lib/pacman/local`), runs `pacman -Sy --dbpath <temporary>` and then `pacman -Qu`
and `pacman -Si` against it. The container's real database is never synced, so the next
installation does not inherit a partial upgrade. The temporary folder is deleted at the
end.

### 2.7 How this differs from plain Arch

| Topic | Plain Arch | MultiPak |
| --- | --- | --- |
| Installing a local file | `pacman -U` (updating first is up to you) | `pacman -Syu` and then `pacman -U`, without a terminal |
| Checking for updates | `checkupdates` | a temporary database, like `checkupdates` |
| Uninstalling | `pacman -Rs` takes orphaned dependencies with it | `pacman -R`, the package only |

### 2.8 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Arch-Linux -- pacman -Qi <name>`.
- Dependency not found: check whether the package comes from an Arch derivative.

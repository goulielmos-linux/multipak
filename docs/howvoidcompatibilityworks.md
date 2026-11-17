# How MultiPak installs Void Linux packages

*MultiPak 1.1.0. Translated from the [Portuguese version](howvoidcompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is an `.xbps` package?

Void Linux is an independent, rolling-release distribution. Its package manager is
called **XBPS**, and a package is an `.xbps` file: a compressed TAR archive (usually
zstd) with the program's files and two metadata files, `props.plist` (name, version,
architecture, dependencies) and `files.plist` (file list).

Void comes in two variants: with **glibc** (the C library most distributions use) and
with **musl** (another, leaner C library). Packages built for one do not work on the
other. The glibc variant also has 32-bit (`i686`) packages.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** it opens the TAR archive from the outside
   and reads only `props.plist` and `files.plist`. The package's installation scripts
   (`INSTALL`, `REMOVE`) are neither run nor extracted.
2. **Picks the variant from the architecture:** `x86_64` goes to the 64-bit glibc
   container, `x86_64-musl` to the musl one and `i686` to the 32-bit glibc one. `noarch`
   works in any of them. Other architectures are refused with a clear message.
3. **Creates (or reuses) the Void container for that variant,** built from the official
   images `ghcr.io/void-linux/void-glibc-full` (64- and 32-bit) and
   `ghcr.io/void-linux/void-musl-full`.
4. **Installs through XBPS:** XBPS does not install a standalone file the way `dnf` or
   `apt` do. MultiPak sets up a local repository holding just that one package, points
   XBPS to it and asks for the exact version. Dependencies come from Void's official
   repositories.
5. **Adds menu shortcuts:** the application's `.desktop` shortcuts are exported to the
   menu.

XBPS installs the file through a local repository, which is not signed, so **the
package signature is not verified**. The installation still goes ahead, **with a
warning** in the result. Install only packages from trusted sources.

### 1.3 The container

- One environment per variant, each with its own container:

  | Environment | Container | Packages |
  | --- | --- | --- |
  | Void Linux (rolling release) | `MultiPak-Void-Linux` | `x86_64` |
  | Void Linux musl (rolling release) | `MultiPak-Void-Linux-musl` | `x86_64-musl` |
  | Void Linux 32-bit (rolling release) | `MultiPak-Void-Linux-32-bits` | `i686` |

- A `noarch` package works in any of the three; MultiPak suggests the 64-bit glibc one.
- Distrobox knows XBPS and prepares the container by itself (locales, sudo, tools).
- Before each installation, MultiPak sets GTK 3 to follow the computer's light or dark
  theme (without this, toolbar icons could become invisible).
- Void ships translations inside each package: the application opens in the system
  language with no extra package.

### 1.4 Updating

**Manager → Update** (the icon of any application in the container, or **Update all**)
runs MultiPak's script, which first updates XBPS itself and then the rest of the system.
The usual `distrobox upgrade` does not work on Void: it asks for confirmation, and
without a terminal XBPS answers "Aborting!" and **exits successfully without having
updated anything**.

**New versions**: the Manager queries `xbps-install -un` and `xbps-query -R` to find
which applications have a new version and lists each one under **With updates**,
**Already up to date** or **No update source** (installed from a file that no repository
offers). On opening, it uses the list the container already has, without downloading
anything; **Check for updates** runs `xbps-install -S` first.

### 1.5 Uninstalling

When you uninstall an application, MultiPak removes its package with `xbps-remove`,
which already checks dependencies: if another application needs the package, XBPS
refuses and nothing is deleted. Dependencies the application brought in are not removed
with it (a general MultiPak rule).

### 1.6 Known limitations

- Signature verification of the local file.
- LZ4 compression (rare): MultiPak reports that it cannot read it.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `xbps.py` | reads `props.plist`/`files.plist` with `plistlib` and validates name, `pkgver` and architecture |
| `metadata.py` (`_xbps_info`, `read_xbps_info`) | decompression (zstd, xz, gzip, bzip2, plain tar) and detection |
| `models.py` (`VOID_ENVIRONMENTS`, `void_environment`, `void_variant`) | the `void-rolling`, `void-musl` and `void-i686` environments, and a container's variant from its image |
| `void_image.py` | the 32-bit image, pulled by digest and stored under a local name |
| `engine.py` (`_install_xbps`, `_finish_install`) | container architecture check, warning and installation |
| `assets/void-setup.sh` | `--install FILE PKGVER ARCH` and `--upgrade` |
| `guest.py` | state (`xbps-query`), file list and removal |
| `updates.py` (`SCRIPTS["void"]`) | updating through the script instead of `distrobox upgrade` |
| `file_associations.py`, `assets/` | MIME type for `*.xbps` |

### 2.2 Reading the package without running it

- **Decompression**: zstd through Python's `compression.zstd` module or the
  `python-zstandard` already bundled in the Flatpak; xz, gzip, bzip2 and uncompressed
  TAR through the standard library. LZ4 has its own message.
- The TAR archive is read as a *stream*, with a read limit; only `props.plist` and
  `files.plist` are read (in memory). `INSTALL`, `REMOVE` and `*.msg` are skipped.
- `props.plist` is read with `plistlib` (XML format). Dependencies shown: only
  `run_depends` (`xbps-src` already lists each library's package there);
  `shlib-requires` only if `run_depends` is empty.

### 2.3 Detection

In `metadata._package_metadata`, Void is checked **before Arch** (both are compressed
TAR archives). A file becomes `xbps` when its contents include the XBPS metadata files;
with the `.xbps` extension, their absence is an error (instead of falling through to
Arch). The architectures accepted for `xbps` are `x86_64`, `x86_64-musl`, `i686` and
`noarch`; `i686-musl` and all others are refused in `inspect_package`.

### 2.4 Choosing the environment

The suggestion comes from the package's architecture (`void-rolling`, `void-musl` or
`void-i686`), with **High** confidence (the metadata identifies Void). For a package
that is not `noarch`, the wizard offers only the environment for its architecture. An
existing container is reused only if its os-release says `ID=void`; since os-release is
the same in all three variants, the variant comes from the container's image
(`void_variant`). Before installing, `_finish_install` runs `xbps-uhelper arch` in the
container and refuses a container of another architecture (`noarch` passes in any of
them).

The 32-bit container uses the `linux/386` image of `void-glibc-full`: the 64-bit kernel
runs 32-bit programs without emulation. That image has the same tag (`latest`) as the
64-bit one, so `void_image.ensure_image` finds its digest with `podman manifest
inspect`, pulls it by that digest (the tag stays on the 64-bit image) and stores it as
`localhost/multipak-void:i686`. Inside it, `xbps-uhelper arch` answers `i686`.

### 2.5 Installation (`void-setup.sh --install`)

1. `xbps-install -Suy xbps` — XBPS refuses any transaction while a newer xbps is
   available.
2. `gtk_preference` — writes `/etc/xdg/gtk-3.0/settings.ini` with
   `gtk-application-prefer-dark-theme` according to the host user's `settings.ini`
   (found through `SUDO_UID`). Not fatal.
3. Temporary repository (`mktemp -d`, deleted by a `trap`): the file is copied under its
   canonical name `<pkgver>.<arch>.xbps` and indexed with `xbps-rindex -a`.
4. `xbps-install -Sy -R <repo> -c <repo>/cache -- <pkgver>` — `-R` puts the local
   repository first and keeps the official (signed) ones for dependencies; the private
   cache avoids reusing another file with the same name and version.

`engine._install_xbps` rereads the copied file (`read_xbps_info`) and checks its name
and version before running the script, and adds the signature warning to the
transaction.

### 2.6 Inside the container (`guest.py`)

- State: `xbps-query -p pkgver` and `-p state` (must be `installed`).
- Files: `xbps-query -f`; links appear as `path -> target`, and the target is cut off.
  The `.desktop` files found become shortcuts.
- Removal: `xbps-remove -y -- name`, without `-R` and without `--force-revdeps`.

### 2.7 Updating

`updates.SCRIPTS["void"]` copies the script to `/usr/libexec/multipak-void-setup.sh`
(`podman cp`) and runs `sudo sh … --upgrade`: `xbps-install -Suy xbps` and then
`xbps-install -uy`.

### 2.8 How this differs from plain Void

| Topic | Plain Void | MultiPak |
| --- | --- | --- |
| Installing a local file | `xbps-rindex` + `xbps-install -R` by hand | the same, automatic, with a private cache |
| File signature | signed repositories only | not verified (warning) |
| musl and 32-bit | separate variant | one container for each |
| Updating | `xbps-install -Su` with confirmation | `-Suy xbps` and then `-uy`, without a terminal |

### 2.9 Troubleshooting

- Installation log: `~/.local/share/multipak/installations/<id>/installation.log`.
- Inside the container: `distrobox enter MultiPak-Void-Linux -- xbps-query -l`.
- Manual update: `distrobox enter MultiPak-Void-Linux -- sudo xbps-install -Su`.
- For the musl and 32-bit containers, use `MultiPak-Void-Linux-musl` or
  `MultiPak-Void-Linux-32-bits` instead.

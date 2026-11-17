# How MultiPak installs Gentoo packages

*MultiPak 1.2.0. Translated from the [Portuguese version](howgentoocompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is a `.gpkg.tar`?

Gentoo normally compiles everything from source, but it also has **binary packages**.
The current format, **GPKG**, is an uncompressed TAR archive with the `gpkg-1` marker,
the metadata (`metadata.tar.*`: category, name, version, dependencies, USE flags) and
the program's files (`image.tar.*`), each part with an optional signature.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** only the metadata. Nothing is extracted.
2. **Creates (or reuses) the container** `MultiPak-Gentoo-Linux`, from the official
   image `docker.io/gentoo/stage3:amd64-openrc`.
3. **Prepares Gentoo:** downloads the package tree (Portage) with its signature
   verified, switches to the desktop profile (the one Gentoo publishes binaries of
   graphical programs for), enables the official binary packages and installs sudo,
   keyboard data, fonts and the system language.
4. **Installs with emerge**, which fetches dependencies as binaries from the official
   server. In practice, almost nothing is compiled.
5. **Adds menu shortcuts.**

Unsigned packages, such as those generated locally with `quickpkg`, are also accepted.

### 1.3 The container

- A single environment, `gentoo-rolling` ("Gentoo Linux (rolling release)").
- Distrobox knows emerge, but it would compile its tools and does not enable binaries.
  MultiPak prepares everything beforehand and marks the basic setup as done.
- The package tree is updated at most once a day, before an installation.
- GTK 3 follows the computer's light or dark theme.

### 1.4 Updating

Distrobox's usual command for updating containers (`distrobox upgrade`) does not work on
Gentoo: it only downloads the new package list (the Portage tree) and does not update
any program. That is why **Manager → Update** (the icon of any application in the
container, or **Update all**) runs MultiPak's script, which downloads the new list and
updates every program in the container (`emerge -uDN @world`) with binary packages.

**New versions**: for each application, the Manager asks Portage for the newest version
available in the package list and compares it with the installed one. It then lists each
application under **With updates**, **Already up to date** or **No update source**
(installed from a file that no repository offers). On opening, it uses the list the
container already has, without downloading anything; **Check for updates** runs
`emerge-webrsync` first.

### 1.5 Uninstalling

When you uninstall an application, MultiPak removes its package with `emerge --unmerge`.
emerge does not check whether another application needs that package, so MultiPak
checks first: if one does, the uninstallation is refused and nothing is deleted.

### 1.6 Known limitations

- A package built with USE flags very different from the desktop profile may pull in
  dependencies that are not available as binaries (those would be compiled, which takes
  a long time).
- The first installation is long (a few minutes, with a new container).
- Only x86-64 (the `amd64` image).

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `gentoo.py` | reader, `is_candidate`, `dependency_lines` |
| `metadata.py` | detection after Arch, before Slackware |
| `models.py` (`GENTOO_ENVIRONMENT`) | `gentoo-rolling`, `MultiPak-Gentoo-Linux` |
| `engine.py` (`gentoo_setup_hook`, `_install_gentoo`) | hook and installation |
| `assets/gentoo-setup.sh` | hook, `--sync`, `--install`, `--upgrade` |
| `guest.py` (`gentoo_entries`, `gentoo_files`, `gentoo_dependents`) | the `/var/db/pkg` database |
| `container_progress.py` | `>>> Emerging [binary] (N of M)` → percentage |
| `updates.py` (`SCRIPTS["gentoo"]`) | updating |
| `assets/multipak-gentoo.xml` | MIME `application/x-gentoo-binary-package` (`*.gpkg.tar`) |

### 2.2 Reading and detection

`gentoo.is_candidate`: an uncompressed TAR archive named `.gpkg.tar` or with a `gpkg-1`
member. `metadata.tar.*` is decompressed in memory (zstd through the Flatpak's
`python-zstandard`, xz, gzip…). The `.sig` signatures are not verified when reading.
`dependency_lines` joins `|| ( a b )` as "a or b" and ignores USE conditionals and `!`
blockers. Hint `gentoo-rolling`, **High** confidence.

### 2.3 Pre-init hook (`gentoo-setup.sh` without an option)

1. `emerge-webrsync` (HTTPS; snapshot signature verified).
2. `eselect profile set default/linux/amd64/23.0/desktop` if the profile is not a
   desktop one.
3. `FEATURES="${FEATURES} getbinpkg"` in `make.conf` and `getuto` (binhost keys). The
   stage3 already ships `binrepos.conf` with the official binhost and
   `verify-signature = true`. `binpkg-request-signature` is **not** used, as it would
   refuse the user's unsigned packages.
4. `emerge --usepkg --with-bdeps=n --noreplace` of sudo, bash-completion, lsof, bc,
   wget, pinentry, xkeyboard-config, dejavu.
5. The host's language in `/etc/locale.gen` + `locale-gen`; `gtk_preference`.
6. A copy at `/usr/libexec/multipak-gentoo-setup.sh` and `/.containersetupdone`
   (Distrobox skips its package step; Python 3 already comes with the stage3).

`--binpkg-respect-use=n` was discarded: it pulled in binaries with `abi_x86_32` and
required USE changes.

### 2.4 Installation (`engine._install_gentoo`)

`emerge` only accepts a file **inside PKGDIR, at the path it would give it itself**.

1. `_install_gentoo` rereads the category and PF from the copied file and copies the
   script into the guest folder (this works in Gentoo containers MultiPak did not
   create).
2. `--sync`: theme, and `emerge-webrsync` if the last sync is more than 24 h old.
3. `--install FILE CAT PF`: validates the names, copies the file to
   `/var/cache/multipak/binpkgs/<cat>/<PF>.gpkg.tar`, sets `PKGDIR=…
   FEATURES=-binpkg-multi-instance`, runs `emaint binhost --fix` and `emerge --usepkg
   --with-bdeps=n <file>`, and cleans the binary cache. The file's original name does
   not matter.

### 2.5 Inside the container (`guest.py`)

`/var/db/pkg/<cat>/<PF>/`: `CONTENTS` (`obj PATH MD5 MTIME`; the path may contain
spaces) for the files; `RDEPEND`/`PDEPEND`, already evaluated, for the dependents.
Without dependents: `emerge --ask=n --quiet --unmerge =cat/PF` for each installed slot.

### 2.6 Updating

`--upgrade`: theme, `emerge-webrsync`, `emerge --usepkg --update --deep --newuse
@world`, cache cleanup.

### 2.7 How this differs from plain Gentoo

| Topic | Plain Gentoo | MultiPak |
| --- | --- | --- |
| Compiling | the rule | the exception: official binaries of the desktop profile |
| Installing a file | put it in PKGDIR by hand | automatic |
| `--unmerge` with dependents | removes | refuses |
| Tree | `emerge --sync` whenever you like | `emerge-webrsync` at most once a day, before installing |

### 2.8 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Gentoo-Linux -- eselect profile show`.
- To see whether anything was compiled: `/var/log/emerge.log` inside the container.

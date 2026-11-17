# How MultiPak installs Alpine Linux packages

*Translated from the [Portuguese version](howalpinecompatibilityworks.pt-BR.md), which
is the reference text.*

*MultiPak 1.1.0.*

---

## 1. In plain terms

### 1.1 What is an Alpine `.apk` package?

Alpine Linux is a very small distribution (the official image is about 4 MB), which is
why it is widely used as a base for server containers. It uses the **musl** C library
instead of glibc, so its programs only run on Alpine. Its package manager is **apk**,
and an `.apk` package (classic format, "v2") is a sequence of two or three gzip streams
joined together: the signature (optional), the `.PKGINFO` metadata file and the
program's files.

Not to be confused with the **Android** `.apk`, which is a ZIP file: MultiPak tells them
apart by their contents and warns that Android applications are not supported.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** it reads only the `.PKGINFO` file. Nothing
   is extracted.
2. **Tells it apart from Arch:** Alpine's `.PKGINFO` has the same format as Arch
   Linux's. MultiPak looks for the `.SIGN.*` signature or the `origin` and `datahash`
   fields.
3. **Estimates the release:** the first line of `.PKGINFO` says which version of
   *abuild* (Alpine's build tool) made the package, and each Alpine release uses its
   own. In a test with about 800 real packages, this got the branch right for nearly all
   of them.
4. **Suggests the environment:** the estimated release; if it is no longer supported, it
   is offered anyway, with a warning (the package needs that release's libraries); if it
   is newer than the stable releases, the development branch (*edge*).
5. **Installs with apk:** in a `MultiPak-Alpine-<release>` container built from the
   official image (a few MB). Dependencies come from Alpine's repositories.
6. **Adds menu shortcuts.**

Packages signed by Alpine go through the normal verification. Those without that
signature, such as packages built locally or obtained from third parties, are installed
without verification, **with a warning**.

### 1.3 The container

- One environment per release supported on the day (in November 2026: 3.24, 3.23,
  3.22) plus *edge*. The list comes from the image registry and the official support
  schedule.
- Distrobox knows apk and prepares the container by itself.
- Before each installation, MultiPak adds translations (on Alpine they come in separate
  `-lang` packages, which are then installed automatically with each program) and makes
  GTK 3 follow the computer's light or dark theme.

### 1.4 Updating and uninstalling

- **Update**: `distrobox upgrade` (Distrobox knows apk), under **Manager → Update**.
- **New versions**: the Manager queries `apk version` to find which applications have a
  new version and lists each one under **With updates**, **Already up to date** or **No
  update source** (installed from a file that no repository offers). When it opens, it
  uses the list the container already has, without downloading anything; **Check for
  updates** runs `apk update` first.
- **Uninstall**: `apk del` does not remove a package that others depend on, but it still
  exits "successfully". MultiPak checks first and explains which packages depend on it.
  `apk del` also removes orphaned dependencies and the program's translations.

### 1.5 Known limitations

- The **apk v3** format (new, binary): refused with a clear message (Alpine's
  repositories still publish v2).
- Packages built for another release may not find a library (apk warns about it).
- Only x86-64 and `noarch`.
- Packages without the abuild line (e.g. built with melange/Wolfi) get the newest stable
  release and "Medium" confidence.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `alpine.py` | v2 reader, `is_candidate`, `is_v3`, abuild → release table, end of support |
| `metadata.py` | detection before Arch; `alpine-<release>` hint |
| `models.py` (`alpine_environment`, `ALPINE_ENVIRONMENTS`) | environments and offline fallback |
| `catalog.py` (`alpine_profiles`, `alpine_package_environment`) | releases of the day and choice |
| `environments.py` (`release_identity`), `alpine.release_version` | `3.24.3` → `3.24`, `_alpha` → `edge` |
| `engine.py` (`_install_apk`) | `apk verify`, preparation and `apk add` |
| `assets/alpine-setup.sh` | `--prepare`: GTK 3 and `lang` + `musl-locales` |
| `guest.py` (`alpine_records`, `alpine_dependents`) | state, files and refusal to remove |
| `suggestions.py` | High/Medium confidence and the musl note |
| `assets/multipak-alpine.xml` | MIME `application/x-alpine-package` (`*.apk`, gzip magic number) |

### 2.2 Reading the package without running it

The gzip segments are read in sequence with `tarfile` in *stream* mode (abuild's TAR
archives have no end marker). Only `.PKGINFO` is read. The `ADB.`/`ADBd`/`ADBc` (v3)
magic number triggers `alpine.v3_error()` before anything is read; a ZIP file with the
`.apk` suffix is refused as an Android application.

### 2.3 Estimating the release

`# Generated by abuild 3.N.x` → release: a measured table (3.4→3.10 … final 3.9→3.16)
and, from 3.10 on, *minor* + 7 (3.17→3.24, 3.18→3.25). `builddate` was discarded (it is
the date of the APKBUILD commit). Without the line, there is no hint.

### 2.4 Catalog and choice

- `alpine_profiles`: the `3.N` tags from `skopeo list-tags
  docker://docker.io/library/alpine` that are still supported according to
  `https://alpinelinux.org/releases.json` (read on each catalog update, with the
  `alpine.END_OF_SUPPORT` table as a fallback), plus `edge` last. Without network: the
  cache or `ALPINE_ENVIRONMENTS`.
- `alpine_package_environment`: the exact release if listed; an unsupported release is
  offered anyway (and comes before edge in the advanced list); newer than all stable
  releases → edge.
- Confidence (`suggestions.py`): **High** when the estimate matches (with a warning if
  the release is no longer supported); **Medium** for edge or without an estimate.

### 2.5 Installation (`engine._install_apk`)

1. `sudo apk update`.
2. `alpine-setup.sh --prepare`: `/etc/xdg/gtk-3.0/settings.ini` with `prefer-dark`
   according to the host; `apk add lang musl-locales` (with `lang`, the `<name>-lang`
   packages come in through `install_if`).
3. `apk verify --quiet <file>`; if it fails, `--allow-untrusted` + warning.
4. `sudo apk add [--allow-untrusted] <file>`.

The progress bar follows the `( 1/78) Installing` lines (`container_progress.py`).

### 2.6 Inside the container (`guest.py`)

`/lib/apk/db/installed` is read as records (`P:` name, `F:`/`R:` files, `D:`
dependencies, `p:` what it provides). A dependent is any package whose `D:` lists the
target's name or something in its `p:` (such as `so:libgtk-3.so.0`). With dependents,
removal is refused; otherwise `apk del --no-progress name`.

### 2.7 Existing container

`release_identity` normalizes os-release: `VERSION_ID=3.24.3` → `3.24`;
`3.25.0_alpha…` → `edge`. A container is reused only if the release matches.

### 2.8 How this differs from plain Alpine

| Topic | Plain Alpine | MultiPak |
| --- | --- | --- |
| Package without a trusted key | `--allow-untrusted` by hand | automatic, with a warning |
| Translations | install `lang` by hand | `lang` + `musl-locales` before each installation |
| `apk del` with dependents | exits 0 without removing | refuses with an explanation |
| Release | the system's | the one estimated from abuild |

### 2.9 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Alpine-3.24 -- apk info -vv`.
- Missing library: in the advanced list, choose the release the package was built for.

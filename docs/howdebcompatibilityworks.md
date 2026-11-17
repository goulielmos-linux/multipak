# How MultiPak installs DEB packages (Debian and Ubuntu)

*Translated from the [Portuguese version](howdebcompatibilityworks.pt-BR.md), which is
the reference text.*

*MultiPak 1.2.0.*

---

## 1. In plain terms

### 1.1 What is a `.deb` package?

DEB is the package format of Debian, Ubuntu and the distributions based on them. A
`.deb` file is an `ar` archive with three parts: the version marker (`debian-binary`),
the metadata (`control.tar.*`: name, version, architecture, dependencies and
installation scripts) and the program's files (`data.tar.*`).

The format is the same on Debian and Ubuntu, but the libraries of each one, and of each
release, differ: a package built for Ubuntu 24.04 may not work on Debian 12, and vice
versa.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** it opens the `.deb` from the outside and
   reads only the `control` file. Nothing is extracted and no script runs.
2. **Suggests the environment:** the package does not say which distribution it was
   built for, so MultiPak suggests the newest Ubuntu LTS, with **Low** confidence and an
   explanation. You can pick another environment in the advanced options.
3. **Creates (or reuses) the container** for the chosen distribution, built from the
   official image (`docker.io/library/ubuntu` or `docker.io/library/debian`).
4. **Installs with apt**, which downloads the dependencies from the distribution's
   repositories.
5. **Adds menu shortcuts.**

### 1.3 The environments

| Distribution | Releases offered (Nov. 2026) | Container |
| --- | --- | --- |
| Ubuntu | the catalog's default release, 24.04 LTS and 22.04 LTS | `MultiPak-Ubuntu-24.04` |
| Debian | stable, 13 and 12 | `MultiPak-Debian-13` |

The releases come from the list of images Distrobox tests, and new releases show up on
their own once they are added to it. For Debian, MultiPak checks the repository itself
to find the current stable release. The table above is the offline fallback.

### 1.4 Updating and uninstalling

- **Update**: under **Manager → Update**, MultiPak updates the whole container with
  Distrobox's own command (`distrobox upgrade`), which uses apt.
- **New versions**: for each application, the Manager asks apt (`apt-cache policy`) for
  the newest version in the repositories and compares it with the installed one. It
  then lists each application under **With updates**, **Already up to date** or **No
  update source** (installed from a file that no repository offers). On opening, it uses
  the list the container already has, without downloading anything; **Check for
  updates** runs `apt-get update` first.
- **Uninstall**: when you uninstall an application, MultiPak removes its package with
  `dpkg --purge`, which refuses if another package depends on it. Dependencies the
  application brought in are not removed with it (a general MultiPak rule).

### 1.5 Known limitations

- MultiPak cannot tell which distribution the package was built for. If a library is
  missing, choose another environment in the advanced options.
- Packages built for another architecture: MultiPak accepts only packages for the
  computer's architecture (such as `amd64`) or for any architecture (`all`).

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `metadata.py` (`_deb_members`, `_deb_fields`, `parse_control`) | reading the `ar` archive and `control` |
| `models.py` (`ubuntu-*` and `debian-*` environments) | fallback environments |
| `catalog.py` (`parse_catalog`, `debian_stable_version`) | releases of the day |
| `suggestions.py` | Low confidence and the suggestion text |
| `engine.py` (`_install_deb`) | installation with apt |
| `package_check.py` (`check_apt`, `parse_apt_policy`) | new versions |
| `guest.py` | state (`dpkg-query`), files and removal |
| `file_associations.py` | the system's MIME types for `.deb` |

### 2.2 Reading the package without running it

`_deb_members` requires the `!<arch>` magic and a first `debian-binary` member holding
`2.0`; invalid headers are refused. `_deb_fields` reads `control.tar.*` (gzip, xz, bzip2
or zstd) with a size limit and takes only `control`. `Package`, `Version` and
`Architecture` are required. Dependencies shown: `Pre-Depends` and `Depends`. Accepted
architectures: the machine's (`amd64`/`x86_64`), `all` and `noarch`.

### 2.3 Choosing the environment

A `.deb` carries no clue about its origin, so there is no hint: the suggestion is the
default Ubuntu environment, with **Low** confidence and a text explaining the choice.
The catalog reads the releases from Distrobox's image list (Ubuntu `NN.NN`, Debian by
number) and resolves `debian-stable` by reading
`https://deb.debian.org/debian/dists/stable/Release`. Floating releases (`latest`,
`stable`) are resolved to the actual number before the container is named.

### 2.4 Installation (`engine._install_deb`)

1. In a container used only by this installation, `dpkg --force-confold --configure -a`
   first finishes any interrupted configuration (failure ignored).
2. `apt-get -y -o Dpkg::Use-Pty=0 -o Dpkg::Options::=--force-confold [--fix-broken]
   install <file>`, with `DEBIAN_FRONTEND=noninteractive`. `--fix-broken` is added only
   when the container is not shared.
3. If it fails because of stale indexes (package not found, download failure), it runs
   `apt-get update` and tries once more. A failure in dpkg or in a package script is
   **never** retried automatically.

### 2.5 Inside the container (`guest.py`)

State: `dpkg-query -W -f=${Status}` (must be `install ok installed`). Files:
`dpkg-query -L`; the `.desktop` files found become shortcuts. Removal:
`dpkg --purge name`, with dpkg's dependency checks enabled.

### 2.6 New versions (`package_check.check_apt`)

`apt-cache policy` shows, for each package, the installed and the candidate versions. If
the only source of the installed version is dpkg's own record (`/var/lib/dpkg/status`),
no repository offers the package: **No update source**.

### 2.7 How this differs from plain Debian/Ubuntu

| Topic | Plain Debian/Ubuntu | MultiPak |
| --- | --- | --- |
| Installing a local file | `apt install ./file.deb` | the same, without a terminal |
| Choosing the distribution | the system itself | the newest Ubuntu LTS, or whichever you choose |
| Uninstalling | `apt remove` may take dependencies with it | `dpkg --purge`, the package only |

### 2.8 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Ubuntu-24.04 -- dpkg -s <name>`.
- Missing library: reinstall, choosing another distribution or release in the advanced
  options.

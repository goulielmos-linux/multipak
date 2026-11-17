# How MultiPak installs Solus packages

*Translated from the [Portuguese version](howsoluscompatibilityworks.pt-BR.md), which is
the reference text.*

*MultiPak 1.1.0.*

---

## 1. In plain terms

### 1.1 What is an `.eopkg` package?

Solus is an independent, rolling-release distribution. Its package manager, **eopkg**,
grew out of pisi, the package manager of Pardus and Pisi Linux, and keeps the same
format: a ZIP file with a `metadata.xml` file (name, version, dependencies,
distribution), the `files.xml` list and the program's files in `install.tar.xz`.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** it opens the ZIP file and reads only
   `metadata.xml`. Nothing is extracted and no script runs.
2. **Tells Solus apart from Pisi Linux:** by the *Distribution* field in the metadata,
   not by the extension. A `.pisi` file with Solus metadata is treated as Solus, and
   vice versa.
3. **Refuses what it cannot install:** *delta* packages (`.delta.eopkg`, which only
   update a version that is already installed) and architectures other than x86-64.
4. **Prepares the image:** Solus publishes no container image. The first time, MultiPak
   downloads an **unofficial** image (`docker.io/silkeh/solus:base`), built in public CI
   from Solus's official repository with just the base system, and completes it locally
   with a small extra layer.
5. **Creates (or reuses) the container** `MultiPak-Solus-Linux` and installs with
   `eopkg install`, which resolves dependencies from Solus's repository.
6. **Adds menu shortcuts.**

### 1.3 The container

- A single environment, `solus-rolling` ("Solus (rolling release)").
- Distrobox does not know eopkg. MultiPak prepares the container beforehand (Python 3,
  sudo, graphics libraries, the tools Distrobox uses) and marks the basic setup as done.
- Before each installation, it refreshes the package list, adds an icon theme, the SVG
  loader and the Breeze GTK theme, and follows the computer's light or dark theme, GTK 2
  programs included.
- The locales already come with the image: the application opens in the system
  language.
- usysconf's "Failed handler 'tmpfiles'" warning after installing or uninstalling is
  harmless (the rootless container cannot change the owner of `/dev/lp*`).

### 1.4 Updating

`distrobox upgrade` does not know eopkg. **Manager → Update** (the icon of any
application in the container, or **Update all**) runs MultiPak's script instead: `eopkg
update-repo` and `eopkg upgrade`.

**New versions**: the Manager queries `eopkg list-upgrades` and `eopkg info` to find
which applications have a new version and lists each one under **With updates**,
**Already up to date** or **No update source** (installed from a file that no repository
offers). On opening, it uses the list the container already has, without downloading
anything; **Check for updates** runs `eopkg update-repo` first.

### 1.5 Uninstalling

When you uninstall an application, MultiPak removes its package with `eopkg remove`.
eopkg would also delete every other application that depends on that package, so
MultiPak checks first: if another application needs it, the uninstallation is refused
and nothing is deleted.

### 1.6 Known limitations

- Delta packages and architectures other than x86-64.
- The image is unofficial (Solus itself does not publish one). Its source lives in a
  public repository (gitlab.com/silkeh/docker-solus), and the packages come from the
  official repository.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `pisi.py` | reads Pisi Linux **and** Solus (`Info.family` = `pisilinux` or `solus`) |
| `metadata.py` | ZIP → `pisi.read_info` → `eopkg` or `pisi` kind; Solus is x86_64 only |
| `models.py` (`SOLUS_BASE_IMAGE`, `SOLUS_ENVIRONMENT`) | base image and local image `localhost/multipak-solus:base` |
| `solus_image.py` | `podman pull` of the base + `podman build` of the local layer |
| `engine.py` (`solus_setup_hook`, `_install_eopkg`) | pre-init hook and installation |
| `assets/solus-setup.sh` | hook, `--prepare` and `--upgrade` |
| `guest.py` (`EOPKG_DATABASE`, `pisi_dependents`) | state, files and refusal to remove |
| `updates.py` (`SCRIPTS["solus"]`) | updating through the script |
| `assets/multipak-solus.xml` | MIME `application/x-eopkg-package` (`*.eopkg`, a ZIP subclass) |

### 2.2 Reading the package without running it

`pisi.read_info` opens the ZIP file, reads `metadata.xml` with a size limit and refuses
DOCTYPE declarations. It accepts `PackageFormat` 1.2. The family comes from
*Distribution* (`Solus` or `Pisi Linux`). Error messages use `{0}` = "Pisi Linux" or
"Solus". Delta packages are refused with an explanation.

### 2.3 Detection and environment

A `PK\x03\x04` header with the `.apk` suffix is an Android app (with its own message);
any other ZIP file goes to `pisi.read_info`. Family `solus` → `eopkg` kind,
`solus-rolling` hint with a `metadata` source, **High** confidence, and a text that
mentions the unofficial image. The only accepted architecture is `{x86_64}`.

### 2.4 Local image (`solus_image.ensure_image`)

The base image skips the packages' configuration scripts, so it lacks what `baselayout`
would create: `/etc/passwd` (without it, Podman cannot start a container as root),
`group`, `shadow`, `gshadow` and the os-release links. A `Containerfile` copies those
templates from `/usr/share/baselayout` and creates the links. It is built with
`--pull=never --network=none`, in a temporary folder under
`~/.local/share/multipak/solus-image`. If the `localhost/multipak-solus:base` image
already exists, nothing happens.

### 2.5 Pre-init hook (`solus-setup.sh` without an option)

It recreates the os-release links; if `/.containersetupdone` does not exist, it runs
`eopkg update-repo` and installs python3, bash, sudo, shadow, util-linux, procps-ng,
curl, rsync, xauth, xkeyboard-config, dejavu-fonts-ttf, mesalib, vulkan and other tools
Distrobox needs; adds `@includedir /etc/sudoers.d` if `/etc/sudoers` exists; creates
`/.containersetupdone`. `/etc/sudoers` is not created: Solus's sudo is *stateless* and
reads `/usr/share/defaults/etc/sudo/sudoers`, which already includes `/etc/sudoers.d`.

### 2.6 Installation (`engine._install_eopkg`)

1. Copies `solus-setup.sh` to the guest's temporary folder and runs `sudo sh …
   --prepare`: os-release, `eopkg update-repo`, `adwaita-icon-theme`, `librsvg`,
   `breeze-gtk-theme` (each one separately, none fatal), GTK 3 `prefer-dark`, and GTK 2
   through an `include` of Breeze's or Breeze-Dark's gtkrc in `/etc/gtk-2.0/gtkrc` (the
   host's XSETTINGS sends an empty theme name, which would cancel a theme name in GTK
   2).
2. `sudo eopkg install --yes-all --no-color <file>`.

This also works in Solus containers MultiPak did not create (the script is copied on
every installation).

### 2.7 Inside the container (`guest.py`)

The Pisi Linux functions, pointed at the `/var/lib/eopkg/package` database: state and
files from the installed `metadata.xml`/`files.xml`; `pisi_dependents` reads the
`RuntimeDependencies` of every installed package and refuses the removal if any of them
depends on the target; then `eopkg remove --no-color --yes-all`.

### 2.8 Updating

`updates.SCRIPTS["solus"]` copies the script to `/usr/lib/multipak-solus-setup.sh`
(Solus has no `/usr/libexec`; `/usr/lib` points to `lib64`) and runs `--upgrade`:
`eopkg update-repo`, `eopkg upgrade --yes-all` and the theme preference.

### 2.9 How this differs from plain Solus

| Topic | Plain Solus | MultiPak |
| --- | --- | --- |
| System | full installation | container from an unofficial image + local layer |
| Removing with dependents | removes the dependents too | refuses |
| Delta | updates the installed version | refused |
| Theme | Budgie/GNOME/Plasma | Breeze GTK, light or dark like the host |

### 2.10 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- eopkg log: `distrobox enter MultiPak-Solus-Linux -- cat /var/log/eopkg.log`.
- To rebuild the local image: `podman rmi localhost/multipak-solus:base` (it is
  recreated on the next installation).

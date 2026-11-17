# How MultiPak runs AppImages

*MultiPak 1.2.0. Translated from the [Portuguese version](howappimagecompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is an AppImage?

An AppImage is an application **in a single file**. Inside, it is a small launcher
program (the *runtime*) attached to a compressed folder holding the application, the
libraries its author chose to include, an icon and a `.desktop` file (name, icon,
command).

The "usual" way to use one is to download it, mark it as executable and double-click it.
That has three well-known problems:

- the file does not show up in the application menu, and it has no icon;
- the runtime needs **FUSE** to mount the compressed folder, and not every distribution
  ships it ready to use;
- an AppImage **promises** to bring everything it needs, but it often relies on
  libraries or Python modules it expects to find on the system. If the system lacks
  them, the application does not open, and the user has no idea what is missing.

### 1.2 What does MultiPak do?

When you open an AppImage with MultiPak:

1. **Reads it without running it:** MultiPak reads the file from the outside, like a
   package, and takes the name, version, icon and shortcut from it. It **never runs**
   the AppImage's launcher to find these things out.
2. **Checks what is missing:** also without running anything, it looks at which
   libraries and Python modules the application expects from the system and asks your
   system whether they exist.
3. **Keeps a copy:** the original file stays where it is; MultiPak keeps a copy in a
   folder of its own (`~/.local/share/multipak/appimages`).
4. **Unpacks it once:** the copy is extracted to a folder. That way the application runs
   straight from the folder, **without needing FUSE**.
5. **Adds a menu shortcut:** the application shows up in the menu with its icon,
   including the shortcut's extra actions (such as "New window").

If nothing is missing, that is all: the application runs directly on your system, with
no container.

> **"On the system" does not mean rpm-ostree.** In this document, "running on the
> system" only means the application opens outside any container, from the folder
> extracted in `~/.local/share/multipak/appimages`, using the libraries your system
> already has. Nothing is installed into the system image: MultiPak does not use
> rpm-ostree for AppImages and never needs a restart.

### 1.3 When dependencies are missing: the AppImage container

If the check in step 2 finds something **required** that the system lacks, the installer
warns you ("This AppImage does not include all the dependencies it needs.", with a link
to see which) and offers two options:

- **"Install the application and its dependencies (recommended)"**: selected by default;
- **"Install only the application"**.

With the recommended option, **a container** comes into play. But the AppImage itself
**is not installed inside it**: the copy and the extracted folder stay in MultiPak's
folder (`~/.local/share/multipak/appimages`), as described in 1.2. Only the **missing
dependencies** go into the container. When you open the application, it **runs inside
the container**: the program's files are the ones in MultiPak's folder (the container
sees your home folder), but the process runs in there and uses the container's
libraries.

- It is **a single Fedora container**, named `MultiPak-AppImages`, shared by every
  AppImage that needs it. It is created the first time and reused after that.
- MultiPak installs the missing dependencies into it with Fedora's package manager.
  Fedora can find the right package from a library name, so this is automatic.
- If a Python module is not among Fedora's packages, MultiPak tries to get it from
  **PyPI** (Python's official repository), carefully (see 2.7).
- The application uses **your usual home folder**, even when running in the container.
- Your system is not changed: the installed dependencies stay inside the container.

If you choose "Install only the application", it stays outside the container (in
MultiPak's folder, as described in 1.2; no rpm-ostree). In that case MultiPak **installs
none of what is missing**: the application probably will not open until you find and
install those dependencies on the system yourself (on Fedora-based atomic systems, that
may mean using rpm-ostree; that decision and that step are yours, MultiPak does not take
them). The list of what is missing appears on screen before the installation.

### 1.4 The quick test in an isolated copy

Whenever the AppImage starts using the container, MultiPak also **tests** the
application before finishing the installation (the confirmation page says so; there is
no option to skip the test). It opens the application for about **15 seconds** in a
**throwaway copy** of the container, **with no internet** and **no access to your
files**, on a virtual display. If the application complains about a missing library or
module, MultiPak installs it and tests again (up to 3 times). Then the copy is deleted.

The test does not prove that everything works: some applications cannot start under
those conditions. When it cannot confirm, MultiPak tells you, but the installation still
stands.

### 1.5 Updating

Under **Manager → Update**, MultiPak looks for new versions when you click **Check for
updates**. It finds out where to look from the information inside the AppImage itself,
from the project page named in the application's data, or from a link you provide
(GitHub, GitLab, Codeberg or a direct link).

It does not compare version numbers: there is an update when **the published file
differs from the installed one**. Before switching, it downloads the new version, checks
that the file arrived intact and that it is **the same application**, asks you to close
the application if it is open, and only then switches. If anything goes wrong halfway,
**the old version comes back** automatically.

### 1.6 Repairing and uninstalling

If someone renames or deletes the `MultiPak-AppImages` container outside MultiPak, its
applications stop opening. The shortcut then shows a notification asking you to open the
Manager, and the Manager offers **Repair**: take the renamed container back, recreate it
and reinstall the dependencies, or, if the system now has everything, start opening the
application outside the container (from the same extracted folder as always; nothing is
installed on the system).

Uninstalling deletes the AppImage copy, the extracted folder and the shortcut. **Your
personal data** (the settings the application saved in your home folder) stays. If it
was the last AppImage using the container, MultiPak offers to delete the container too.

### 1.7 Known limitations

- **No delta updates**: the new file is downloaded in full.
- After a **successful** update, the old file is deleted.
- AppImages that are not on GitHub, GitLab or Codeberg and carry no update information
  need you to provide a direct link.
- Dependencies that **neither Fedora nor PyPI** provides, and Python modules only
  available as source code that must be **compiled** (C, Rust, etc.), are not installed:
  MultiPak tells you which ones are still missing.
- The AppImage must match the computer's **architecture** and have exactly **one**
  `.desktop` file at its root.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `appimage.py` | format reader (ELF + SquashFS/ISO), installation, shortcut, update with rollback, launching, cleanup |
| `appimage_dependencies.py` | static dependency analysis (ELF `DT_NEEDED`, glibc version, Python imports) and the system probe |
| `appimage_container.py` | shared Fedora container, installation with DNF and PyPI, isolated test, repair |
| `appimage_updates.py` | update sources, API queries, cache, verified download, detection of copies in use |
| `appimage_launcher.py` | the `multipak-appimage` command called by the shortcuts; reports failures in a notification |
| `file_associations.py` + `assets/multipak-appimage.xml` | MIME type for opening `.AppImage` files with MultiPak |
| `kirigami/live.py` | inspection before installing (`inspect_installation`, `appimage_dependencies`, `appimage_environment`) |
| `kirigami/main.py` | the wizard's choices (`appimage_options`: container yes/no, test yes/no) |
| `kirigami/manager.py` | AppImage updates, repair and deletion in the Manager |
| `containers.py` | shared container rules: who still uses it, verified removal |

### 2.2 Reading the file without running it (`appimage.py`)

- **`elf_info`** reads the ELF header and requires the `AI\x01` or `AI\x02` signature at
  byte 8 (types 1 and 2 of the specification). The ELF architecture (`e_machine`:
  x86_64, aarch64, i686, armv7l) must match the computer's. In type 2, the SquashFS
  payload starts right after the ELF section table; the code accepts up to 64 KiB of
  zero padding before the `hsqs` magic.
- **`Archive`** picks the tool: `unsquashfs -o <offset>` for type 2; `7zz`, `7z` or
  `bsdtar` for type 1 (an ISO 9660 image). It lists entries (`names`, `listing` with
  type and size), reads one member (`read`, with a limit), extracts a few
  (`extract_paths`) or everything (`extract`). `unsquashfs` runs with `-no-xattrs`,
  `-processors 2` and `-mem 64M`; `bsdtar`, with `--no-same-owner
  --no-same-permissions`.
- **Where these tools come from:** they are looked up with `shutil.which` and run
  **inside** the Flatpak (they are not in `host.HOST_TOOLS`), so whatever is installed
  on the computer does not matter. `unsquashfs` is built into the Flatpak itself (the
  manifest's `squashfs-tools` module, in `/app/bin`) and `bsdtar` comes from the
  `org.kde.Platform` 6.11 runtime (`/usr/bin/bsdtar`; checked in the installed Flatpak
  in November 2026). 7-Zip is in neither, so type 1 is read with `bsdtar`. The user does
  not need to install anything. If the tool is missing (for example, when running
  MultiPak outside the Flatpak), reading stops with "Could not find unsquashfs…" or
  "Could not find 7-Zip or bsdtar…", which says what is missing but **does not offer to
  install it**.
- **`_member`** refuses internal names that are absolute or contain `..`, `\` or control
  characters, or that start with `-` (which the tool would take as options).
- **`inspect_appimage`** requires exactly one `.desktop` file at the root, with
  `Type=Application`. It takes `Name` in the current language (or the generic one),
  `X-AppImage-Version` ("Not provided" if missing or invalid) and the icon:
  `.svg`/`.png` candidates named after `Icon=` (preferring SVG and `hicolor`), then
  `.DirIcon`; the content must really be PNG or SVG. Limits: 1 MiB for metadata, 4 MiB
  for the icon.

### 2.3 Dependency analysis (`appimage_dependencies.analyze`)

Done before installation (`kirigami/live.appimage_dependencies`) and again on every
update. A failed analysis **never blocks** the installation: it is only logged.

1. Lists the AppImage and picks the candidates: files in `bin/`/`sbin/` or with library
   names (`*.so`, `*.so.N…`), and, **only if the AppImage uses the system's Python**,
   the `.py` files. "Uses the system's Python" = `AppRun` is a `#!` script that mentions
   `python`/`python3` and the AppImage ships no `usr/bin/python3*` of its own.
2. Respects limits before extracting (when the listing gives sizes): up to 400 ELF files
   and 500 `.py` files, 256 MiB per ELF, 2 MiB per `.py`, 768 MiB in total. Beyond that,
   the report is marked **incomplete**.
3. Extracts **only** those files into a temporary folder (`multipak-analysis-*`), reads
   them one at a time and deletes each right away. Symbolic links and paths leading out
   of the folder are not followed.
4. From each ELF it reads `DT_NEEDED` and the highest `GLIBC_x.y` version required (the
   `.gnu.version_r` section). From each `.py`, the `import` statements through `ast`:
   imports inside functions or `try/except` count as **optional**.
5. Discards what the AppImage ships itself (libraries with the same name, `.py` modules
   and packages with `__init__.py`), glibc's own components (`libc`, `libm`,
   `ld-linux`…) and the Python standard library, except the parts distributions split
   out (`tkinter`, `turtle`, `idlelib` → `_tkinter`).
6. Runs a small, reliable **probe** with the system's `python3` (fixed code in `PROBE`):
   `importlib.util.find_spec` for the modules, `ldconfig -p` and the usual library
   folders, and `os.confstr("CS_GNU_LIBC_VERSION")`. If the probe does not answer,
   nothing is reported as missing (except the interpreter, if the AppImage needs it) and
   the report is incomplete.

The result (`DependencyReport`) has:

- `missing`: what the **system** lacks (this decides whether the container is offered;
  only non-optional items count, in `required`);
- `requirements`: **everything** the AppImage expects from outside, whether or not the
  system has it. Since the container is a different system, all of it is requested there
  (`container_packages`; DNF skips whatever is already installed).

Each item becomes a name DNF understands: libraries as RPM capabilities
`libfoo.so.1()(64bit)`, Python modules as `python3dist(name)`, with a table for the
cases where the Fedora package has a different name (`yaml` → `python3-pyyaml`, `gi` →
`python3-gobject`, `PIL` → `python3-pillow`, `_tkinter` → `python3-tkinter`…). A glibc
that is too new is flagged, but there is no package to install for it.

### 2.4 Installation (`appimage.install` → `_complete`)

In the wizard (`AppImagePage.qml`), if `report.required` is not empty, two `MethodCard`s
appear: "Install the application and its dependencies (recommended)" and "Install only
the application". `kirigami/main.set_dependency_report` selects the first one
(`_appimage_container = True`) and sets `_appimage_verify = True`. There is a
`setAppImageVerify` slot, but **no page uses it** today: in practice the isolated test
always runs when the container is chosen, and the confirmation page
(`AppImageContainerPage.qml`) only says so. `appimage_options` passes `container=True`
only if required dependencies are missing **and** the first card is still selected. If
the user chooses "only the application", installation continues in `extracted` mode
without installing any package: whatever was missing is still missing on the system and
it is up to the user to install it (the `system` repair, in 2.9, only exists for
AppImages that were in the container).

Steps (with the progress shown):

1. **5 %:** creates the transaction (`installations/<id>/manifest.json`,
   `method="appimage"`), creates `appimages/<id>/` (0700) and **copies** the file to
   `application.AppImage` (checking free space; can be cancelled). The original is left
   untouched. Computes the SHA-256.
2. Reads the **copy** again and checks the name, version and architecture against what
   was shown to the user; if anything changed, it refuses ("the file changed").
3. **45–60 %:** `chmod 0755` and full extraction to `AppDir/`, which must contain an
   `AppRun` (made executable).
4. **70–80 %** (container only): `ensure_container`, then `install_packages` with the
   report's list (2.7). What was not found goes into `appimage_missing` and the
   warnings; what came from PyPI is also reported.
5. `appimage_mode` = `"container"` or `"extracted"`.
6. **90 %:** shortcut (`_desktop`, below).
7. **95 %** (container + test): `verify_and_complete` (2.7). A failure here becomes a
   warning: it **does not roll back** the installation.
8. **100 %:** `status="installed"`.

A failure records the error in the transaction (`record_install_failure`). **Try again**
(`retry`) reuses the copy only if its SHA-256 still matches the recorded one.

**The shortcut** (`_desktop`) is the AppImage's own `.desktop` file, rewritten as
`~/.local/share/applications/multipak-<id>-appimage.desktop`:

- `Exec` now calls `flatpak run --command=multipak-appimage
  io.github.goulielmos_linux.multipak -- <id> …` (outside the Flatpak:
  `python3 appimage_launcher.py <id> …`);
- an `env VAR=value` prefix in the original `Exec` becomes `--setenv VAR=value`;
- `%f %F %u %U` are kept, `%i` becomes `--icon <icon>`, `%c` becomes the name, `%k` is
  dropped, and other arguments are kept with correct quoting;
- `[Desktop Action …]` sections are preserved; other sections are removed;
- `TryExec`, `Path`, `OnlyShowIn` and `NotShowIn` are dropped; `DBusActivatable=false`,
  `Hidden=false`, `NoDisplay=false`, `X-MultiPak-Method=appimage`;
- `Icon` points to `appimages/<id>/icon.svg|png` (or `application-x-executable`).

`migrate_legacy_launchers` fixes old shortcuts that called the Alpha version's ID
(`io.github.multipak.manager`).

### 2.5 Where the files live

```
~/.local/share/multipak/
├── installations/
│   ├── <id>/manifest.json            transaction (mode, container, packages, warnings, source…)
│   ├── <id>/installation.log         operation log
│   ├── appimage-updates.json         cache of update checks
│   └── environments/MultiPak-AppImages/container.json   container record
└── appimages/<id>/
    ├── application.AppImage          managed copy (0755)
    ├── AppDir/                       the extracted application (AppRun, usr/…)
    ├── icon.svg | icon.png           icon used by the shortcut
    ├── update.AppImage               downloaded new version, waiting for the switch
    └── previous.AppImage, previous.AppDir   previous copy, only during an update
~/.local/share/applications/multipak-<id>-appimage.desktop
```

The data the application writes (`~/.config/…`, `~/.local/share/…`) stays in the user's
home folder, as with any AppImage, also in container mode (the container has no home
folder of its own).

### 2.6 Launching (`appimage.launch`, `appimage_launcher.py`)

The shortcut runs `multipak-appimage <id> [args]`, which finds the transaction (it must
be `installed`) and checks that `application.AppImage` is a regular file. Then,
depending on `appimage_mode`:

| Mode | What runs | Where |
| --- | --- | --- |
| `extracted` | `AppDir/AppRun` (which must resolve to a path inside `AppDir`) | on the system, through `flatpak-spawn --host` |
| `container` | `distrobox enter --name <container> --no-tty --no-workdir -- env … AppDir/AppRun` | in the container, also called through `flatpak-spawn --host` |
| `direct` | `application.AppImage` itself (with its own runtime and FUSE) | on the system |

In `container` mode, nothing of the application is copied into the container:
`AppDir/AppRun` is the same path in `~/.local/share/multipak/appimages/<id>/`, which the
container sees because Distrobox was created without a separate `--home` and mounts the
user's home folder. The container only provides the libraries and modules (whatever DNF
and pip installed in it).

`direct` is the field's default value in `models.py` and exists only for old
transactions: current installations always record `extracted` or `container`.

The environment receives the variables from the specification: `APPIMAGE` (the copy),
`APPDIR`, `OWD` (the current folder) and `ARGV0`, plus those from `--setenv`. Inside the
Flatpak, only these are passed on; the rest of the environment is the system's
(`host.command`).

In container mode, before opening, `podman container inspect --format {{.Id}}` checks
that the name **still leads to the same container** (the recorded 64-digit ID). If it
does not, the launcher raises `RepairNeeded` and does **not** let Distrobox create
another container, nor open the application in a container that took over the name. Any
launcher error goes to `stderr` and to a **notification** (the
`org.freedesktop.portal.Notification` portal inside the Flatpak, with
`gdbus`/`notify-send` as a fallback), because a shortcut has no window to show errors
in.

### 2.7 The AppImage container (`appimage_container.py`)

**Creating and reusing it (`ensure_container`):**

- `engine.preflight`: requires `distrobox`, `podman` (and `setsid` in the Flatpak), and
  a **rootless**, non-remote Podman.
- If there is a record (`find_record`) and the container still exists with the same ID
  and the right labels (`OWNER_LABEL` = recorded owner,
  `io.github.multipak.appimage-container=1`), it is reused. If it was **renamed**
  outside MultiPak, it is taken back (`podman rename`) and the transactions are pointed
  at it again (`_retarget`). If the ID exists but the labels changed, it is refused.
- Otherwise it creates one: named `MultiPak-AppImages`, or `C2-MultiPak-AppImages`,
  `C3-…` if another container holds the name; image
  `quay.io/fedora/fedora-toolbox:latest` (`podman pull --policy=missing`); `distrobox
  create --no-entry --additional-packages python3` with the owner labels; **no separate
  `--home`**. Then a `distrobox enter … true` to initialize it.
- The container is not offered for installing regular packages, but it appears in the
  Manager's update list, in the Components section, as "Dependencies of AppImages" (as
  long as it answers to its own name).

**Installing packages (`install_packages`):**

1. Filters the names (`[A-Za-z0-9][A-Za-z0-9._+()-]{0,199}`) and checks the container's
   identity (`verify_name`).
2. `sudo -n dnf -y install --skip-unavailable --setopt=install_weak_deps=False -- …` (a
   name no package provides does not bring the others down), then
   `rpm -q --whatprovides` to see what is still missing.
3. For each `python3dist(x)` still missing, `install_from_pypi`:
   - makes sure `pip` is available (installs `python3-pip` if needed);
   - tries a **prebuilt wheel** (`pip download --no-deps --only-binary=:all:`);
   - without a wheel, it downloads the **source code**, but only goes ahead if it is
     **pure Python** (no `.c`, `.cpp`, `.pyx`, `.rs`, `.go`, `Cargo.toml`,
     `CMakeLists.txt`, `meson.build`… and it contains the requested module). The build
     tools declared in `pyproject.toml` (by default `setuptools>=40.8.0` + `wheel`) are
     downloaded **as wheels only**. The build runs in a **throwaway copy** of the
     container (`podman commit` + `podman run --network=none --cap-drop=all
     --security-opt=no-new-privileges --read-only --userns=keep-id`, `/tmp` and `/run`
     on tmpfs, up to 600 s per step), with up to 3 rounds asking the backend (PEP 517
     `get_requires_for_build_wheel`) what else it needs; each requirement is validated
     (no links, paths or repositories) and downloaded as a wheel. The result must be a
     `-none-any` wheel;
   - whichever path it takes, the wheel must **contain the module** the application
     imports (read from the zip's file list): a project with a similar name will not do;
   - installs with `sudo -n python3 -m pip install --only-binary=:all:
     --break-system-packages` (it goes into the container's `/usr/local`).
   - The temporary folders live in `installations/pypi/<uuid>` and are deleted.

**Isolated test (`isolated_check`, `verify_and_complete`):**

- Makes sure Xvfb is in the container (`xorg-x11-server-Xvfb`); without it, it tests
  without a display.
- `podman commit` of the container to `localhost/multipak-appimage-check:<id>` and
  `podman run --rm --network=none --cap-drop=all --security-opt=no-new-privileges
  --read-only --userns=keep-id`, with `/tmp`, `/run` (and `/var/lib/xkb`) on tmpfs,
  `AppDir` mounted **read-only** at `/opt/multipak-appimage`, `HOME=/tmp`,
  `QT_QPA_PLATFORM=offscreen`. With Xvfb: `Xvfb :99 -screen 0 1280x800x24 -nolisten tcp`
  and `timeout --signal=TERM --kill-after=5 15 AppRun`; Podman has a backup `--timeout`
  (15 + 15 s).
- Result: `running` (exit code 124: still running at 15 s), `exited` (exited with 0) or
  `unknown`. From the output, `startup_failures` recognizes **only** known messages:
  `error while loading shared libraries: X.so…`, `No module named 'X'`,
  `version 'GLIBC_x.y' not found`.
- Repeats for up to 3 rounds as long as it finds something installable. What it
  installed is added to `appimage_packages` (so a repair can reinstall the same in a new
  container); what it could not install goes into `appimage_missing`. The temporary
  container and image are removed even if the installation is cancelled
  (`_cleanup_runner`).

### 2.8 Updates (`appimage_updates.py`, `kirigami/manager.py`)

**Where to look** (`check_one`), in this order, using the first one available:

1. the link saved for the application (`appimage_update_source`);
2. the **embedded update information** (the `.upd_info` ELF section, up to 4 KiB):
   `zsync|https://…` and `gh-releases-zsync|owner|repo|tag|pattern` are accepted; other
   types are ignored;
3. the project pages in the **AppStream** data (`usr/share/metainfo` or `appdata`,
   `vcs-browser`, `homepage` and similar URLs pointing to GitHub/GitLab/Codeberg), used
   **only** if a release published there has the same SHA-256 or size as the installed
   copy; the link is then saved;
4. otherwise, the state is `no-source` and the Manager asks the user for a link
   (`check_link`). That link is saved only when the published file is the installed one
   or, during an update, after the download proves it is the same application
   (otherwise: `LinkMismatch`, and the link is requested again).

**Supported sources** (`Source.kind`): `github` (REST API, up to 20 releases, no drafts
or pre-releases when the tag is `latest`), `gitlab` (gitlab.com), `gitea`
(codeberg.org), `zsync` (reads only the `.zsync` header: SHA-1, size and file URL) and
`file` (a direct link to an `.AppImage`). HTTPS only, redirects included.

**Choosing the file** (`choose`): it ends in `.AppImage` (or `.zsync`), matches the
pattern, and names the computer's architecture (or names no other one); ties are broken
by naming the architecture, containing words from the application's name, and having the
shorter name.

**"Is there an update?"** (`is_current`): compares the published SHA-256 (GitHub
provides `digest`), otherwise zsync's SHA-1, otherwise the size. **Version numbers are
never compared.** The results are stored in `installations/appimage-updates.json` and
stay valid for **6 hours**; opening the list shows only recent results without going
online, and the servers are queried only when the user asks for a check. A failed check
shows the last result, marked as outdated.

**Download** (`download`): to `appimages/<id>/update.AppImage` (max. 8 GiB, can be
cancelled), checks SHA-256/SHA-1 when known, `chmod 0700`, reads it as an AppImage and
requires it to be **the same application** (same `.desktop` name or same `Name`). Then
it reruns the dependency analysis (`_with_dependencies`):

- if it already runs in the container: new packages the new version needs that are not
  yet in `appimage_packages` are shown for confirmation;
- if it runs on the system and the new version has required dependencies missing: the
  Manager proposes **moving it to the container** (`needs_container`).

**Application open:** `running` looks in the system's `ps -eo pid=,args=` for processes
whose command mentions `appimages/<id>/AppDir/` or the copy. If there are any, the
Manager asks you to close it; "Close" sends `SIGTERM` and waits up to 10 s. This is
checked before the download and again before the switch.

**Switch** (`appimage.update`): moves the current copy to `previous.AppImage` and
`AppDir` to `previous.AppDir`, puts the new one in place and runs `_complete` again
(extraction, container packages, shortcut, test). **Any failure or cancellation**
restores the previous copy, folder, icons, transaction and shortcut; a cancelled update
never undoes the installation. On success, `previous.*` is deleted.

**Update all** goes through containers, AppImages and snaps one at a time; an AppImage
that does not close can be skipped.

### 2.9 Uninstalling, deleting and repairing

**Uninstall** (`appimage.cleanup`): deletes the shortcuts (and `.tmp` files),
`appimages/<id>/` and `installations/<id>/`. If something fails, the transaction becomes
`cleanup_failed` with the error. For AppImages in the container,
`containers.uninstall_application` then checks whether any other transaction still uses
the container (`_referenced`); if none does, the Manager offers to remove it
(`remove_container`, which checks the ID, owner and record, and keeps the base images).
AppImages outside the container never claim it.

**Detecting breakage** (`needing_repair`, run in the background by
`Manager.checkRepairs`): for each AppImage in container mode, compares the recorded name
with the names `podman ps --all` shows for the recorded **ID**. If Podman does not
answer, nothing is reported as broken.

**Repair plan** (`repair_plan`), shown to the user first:

| Type | Situation | What happens |
| --- | --- | --- |
| `relink` | the container has the right name again | just points to it again, without asking |
| `system` | the system now has all the dependencies (complete analysis, nothing required missing) | switches to `extracted`, with no container |
| `renamed` | the same container, under another name | `podman rename` back (the default name or the lowest free `Cn-`) |
| `install` | the recorded container is gone, but the current AppImage container exists | installs the `appimage_packages` there |
| `recreate` | there is no AppImage container | creates a new one and installs the packages |

Taking a container back repairs all of its AppImages at once. The repair list also has
**Delete**, which removes only the copy and the shortcut and leaves the container as it
is.

### 2.10 Opening `.AppImage` files with MultiPak (`file_associations.py`)

If the user chooses MultiPak for AppImages, the `application/x-multipak-appimage` MIME
type is installed (`~/.local/share/mime/packages/io.github.multipak.appimage.xml`): the
`*.appimage` glob and the `\177ELF` + `AI\001`/`AI\002` at byte 8 magic number, both
with priority 80. If the user unchecks it and the system already ships that type
globally, MultiPak writes a local type that declares it a subclass of
`application/x-executable`, so AppImages are treated as ordinary executables again.

### 2.11 How this differs from the "usual" way of using AppImages

| Topic | `chmod +x` and run | MultiPak |
| --- | --- | --- |
| Needs FUSE | yes (the runtime mounts the image) | no: runs from the extracted folder |
| Menu and icon | no | yes, with the `.desktop` actions |
| Where it lives | wherever you left it | a copy in `~/.local/share/multipak/appimages`; the original untouched |
| Missing dependencies | the application fails | detected beforehand, with the Fedora container (DNF/PyPI) on offer |
| Test before use | no | whenever it uses the container: 15 s in a copy with no network or files |
| Updates | manual | on request; `.upd_info`, AppStream or a link; full file, verified by hash, rolled back on failure |
| Isolation while running | none | none |

### 2.12 Troubleshooting

- Installation log: `~/.local/share/multipak/installations/<id>/installation.log`.
- State: `installations/<id>/manifest.json` — `appimage_mode`, `container`,
  `container_id`, `appimage_packages`, `appimage_missing`, `warnings`,
  `appimage_update_source`.
- To see why a shortcut does not open (the error is printed in the terminal):
  `flatpak run --command=multipak-appimage io.github.goulielmos_linux.multipak -- <id>`.
- The container: `podman ps --all --filter
  label=io.github.multipak.appimage-container=1` and `distrobox enter
  MultiPak-AppImages` (or the `Cn-` name it got) to try `AppDir/AppRun` by hand.
- Updates: `installations/appimage-updates.json` (what each check found) and the
  Manager's log ("Show details").

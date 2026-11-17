# How MultiPak installs SliTaz packages

*Translated from the [Portuguese version](howslitazcompatibilityworks.pt-BR.md), which
is the reference text.*

*MultiPak 1.2.0.*

---

## 1. In plain terms

### 1.1 What is a `.tazpkg`?

SliTaz is a tiny distribution (its Base ISO is about 30 MB). Its package manager is
**tazpkg**, and a package is a CPIO archive containing a **receipt** (a shell script
with the name, version, description and dependencies), the file list and the program's
compressed files (`fs.cpio.lzma`).

SliTaz keeps separate repositories for **64-bit** (x86_64) and **32-bit** (i486)
programs. MultiPak uses *cooking*, the continuously updated development line.

### 1.2 What does MultiPak do?

1. **Reads the package without running it:** the receipt is a script, but MultiPak only
   reads its plain assignments (`PACKAGE="…"`) and never executes it. The architecture
   comes from the package's own programs (the header of the first ELF file), not from
   the file name.
2. **Picks the environment for that architecture:** `slitaz-cooking` (64-bit, container
   `MultiPak-SliTaz-cooking`) or `slitaz-cooking-i486` (32-bit, container
   `MultiPak-SliTaz-cooking-i486`). Packages marked `any` can go to either.
3. **Builds the image the first time:** SliTaz publishes no up-to-date container image
   (the one on Docker Hub dates from 2016). MultiPak downloads the official Base ISO,
   checks its md5, extracts the file system and imports it into Podman. **Nothing from
   the ISO is executed.**
4. **Prepares the container:** Distrobox does not know tazpkg, so MultiPak installs
   Python 3, sudo, bash, keyboard data, fonts, graphics drivers and the system language
   itself.
5. **Installs with tazpkg**, plus the translation package (`<name>-lang`) when SliTaz
   has one.
6. **Adds menu shortcuts.**

### 1.3 Download security

busybox's `wget` (the one SliTaz ships) **does not verify HTTPS certificates**. That is
why the image MultiPak builds already includes GNU Wget and the certificates, downloaded
over verified HTTPS and checked against the repository's `packages.md5`. They are
installed first, before anything else is downloaded inside the container.

### 1.4 Updating

`distrobox upgrade` does not know tazpkg. **Manager → Update** (the icon of any
application in the container, or **Update all**) runs MultiPak's script instead, which
refreshes the package lists and runs `tazpkg up --auto`.

**New versions**: the Manager asks tazpkg (comparing `installed.info` with the mirror's
`packages.info`) which applications have a new version and lists each one under **With
updates**, **Already up to date** or **No update source** (installed from a file that no
repository offers). On opening, it uses the list the container already has, without
downloading anything; **Check for updates** runs `tazpkg recharge` first.

### 1.5 Uninstalling

When you uninstall an application, MultiPak removes its package with `tazpkg remove`.
Without a terminal, tazpkg would delete the package even if another application needs
it, leaving that application broken. So MultiPak checks first: if another application
depends on it, the uninstallation is refused and nothing is deleted.

### 1.6 Known limitations

- Packages built for SliTaz 5.0, the stable release: MultiPak only offers the *cooking*
  line, because 5.0 is old. Those packages go to *cooking* and may not work.
- SliTaz's GTK 3.22 ignores the computer's GTK theme: it stays on Adwaita, light or dark
  to match the computer.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `slitaz.py` | newc CPIO reader, receipt parsed without running it, architecture from the ELF |
| `slitaz_image.py` | ISO9660 → `rootfs.gz` → CPIO → TAR → `podman import` |
| `models.py` (`SLITAZ_ENVIRONMENTS`, `slitaz_environment`) | two environments, images `localhost/multipak-slitaz:cooking[-i486]` |
| `engine.py` (`slitaz_setup_hook`, `SLITAZ_SCRIPT`, `_install_slitaz`) | hook and installation |
| `assets/slitaz-setup.sh` | hook, `--recharge`, `--upgrade` |
| `guest.py` (`slitaz_entry`, `slitaz_files`, `slitaz_dependents`) | the `/var/lib/tazpkg` database |
| `kirigami/live.py` | offers only the environment for the package's architecture |
| `assets/multipak-slitaz.xml` | MIME `application/x-tazpkg` (`*.tazpkg`) |

### 2.2 Reading

`metadata.py` recognizes the `07070` magic number, the first bytes of every cpio
archive. `slitaz.read_info` walks the CPIO archive within limits (number of members,
name length, receipt size), reads the receipt with regular expressions (literal values
only, no shell expansion) and looks for the first ELF file inside
`fs.cpio.lzma`/`.gz` to get the architecture (`EM_386` → i486, `EM_X86_64` → x86_64).
On an x86_64 computer, i486 and `any` are accepted too. The environment hint follows the
architecture, with **High** confidence.

### 2.3 Local image (`slitaz_image.ensure_image`)

If the image already exists, nothing happens. Otherwise, in a folder under
`~/.local/share/multipak/slitaz-image`, it:

1. Downloads `slitaz-base-<arch>-cooking.iso` and its `.md5` from
   `https://mirror.slitaz.org` and checks the md5 (256 MB limit).
2. Reads the ISO9660 image in Python and finds `boot/rootfs.gz`; decompresses it (LZMA
   or gzip, multiple streams).
3. Converts the CPIO archive to TAR, leaving out `boot`, `lib/modules`, `lib/firmware`,
   `/init` and device nodes; adds the os-release (`ID=slitaz`, `VERSION_ID=cooking` or
   `cooking-i486`), the HTTPS mirror and the *bootstrap* packages (GNU Wget,
   certificates and their dependencies), checked against `packages.md5` (the md5 listed
   in `packages.info` is not the file's).
4. Runs `podman import`, with a label that stores the ISO's md5.

### 2.4 Pre-init hook (`slitaz-setup.sh` without an option)

It installs the *bootstrap* packages first; runs `tazpkg recharge` and downloads
`packages.{info,list,md5,equiv}` directly with GNU Wget (the mirror's `bundle.tar.lzma`
can lag behind, and tazpkg would reject newer packages with "Checksum error"); installs
sudo, bash, python3, xorg-xkeyboard-config, ttf-dejavu, mesa-dri and glibc-locale;
generates the host's locale with `localedef`; creates a shell `getent` (busybox lacks
one, and Distrobox needs it); writes the theme preference; copies itself to
`/usr/libexec/multipak-slitaz-setup.sh`; creates `/.containersetupdone`.

### 2.5 Installation (`engine._install_slitaz`)

1. `sudo sh /usr/libexec/multipak-slitaz-setup.sh --recharge` (lists + theme).
2. `sudo tazpkg install <file>`.
3. `sudo tazpkg get-install <name>-lang` (failure ignored if it does not exist).

### 2.6 Inside the container (`guest.py`)

`/var/lib/tazpkg/installed/<name>/` (receipt and `files.list`) for state and files;
`installed.info` (the eighth field lists dependencies) for `slitaz_dependents`. With no
dependents: `tazpkg remove` with standard input closed.

### 2.7 Updating

`updates.SCRIPTS["slitaz"]` copies the script and runs `--upgrade`: `--recharge` and
`tazpkg up --auto`.

### 2.8 How this differs from plain SliTaz

| Topic | Plain SliTaz | MultiPak |
| --- | --- | --- |
| System | live CD or installation from the ISO | container imported from the same ISO |
| Downloads | busybox wget, no TLS checks | GNU Wget with certificates |
| Package lists | `bundle.tar.lzma` | lists downloaded directly |
| Removing with dependents | removes it and breaks them | refuses |

### 2.9 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-SliTaz-cooking -- tazpkg list`.
- To rebuild the image: `podman rmi localhost/multipak-slitaz:cooking`.
- Do not run MultiPak with a different `XDG_DATA_HOME`: Podman would switch to another
  storage and rebuild the image.

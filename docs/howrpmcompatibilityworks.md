# How MultiPak installs RPM packages (Fedora, Mageia, openSUSE and ALT Linux)

*MultiPak 1.2.0. Translated from the [Portuguese version](howrpmcompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is an RPM?

RPM is the package format of several distributions: Fedora, Mageia, openSUSE, ALT
Linux, RHEL and its derivatives. The format is the same, but **each distribution has its
own libraries and its own package manager**: an openSUSE RPM usually does not work on
Fedora. So it is not enough to "know how to install an RPM": you need to know **where**
it came from.

### 1.2 What does MultiPak do?

1. **Reads the header:** it uses the `rpm` tool bundled with MultiPak, without
   installing anything or running scripts, to get the name, version, architecture,
   dependencies and the *Release* and *Distribution* fields.
2. **Works out where the package came from:** it looks for the tag in *Release*, such as
   `.fc44` (Fedora 44), `.mga10` (Mageia 10), `lp156`/`bp161`/`160100` (openSUSE Leap)
   and `alt1` (ALT). When *Release* gives no clue (Tumbleweed does not tag it; ALT
   rarely names the branch), it uses *Distribution*, such as "openSUSE Tumbleweed" or
   "ALT p11". If the header says nothing at all, it tries the file name.
3. **Suggests the environment:** the distribution and release it found, with a
   confidence level (High, Medium or Low) explained on screen. With no clue at all, it
   uses the newest stable Fedora.
4. **Installs with the right package manager:** dnf on Fedora and Mageia, zypper on
   openSUSE, apt-get (APT-RPM) on ALT. Dependencies come from the distribution's
   repositories.
5. **Adds menu shortcuts.**

### 1.3 The environments

| Distribution | Releases offered (Nov. 2026) | Image | Container |
| --- | --- | --- | --- |
| Fedora | latest, 44, 43 | `quay.io/fedora/fedora-toolbox` | `MultiPak-Fedora-44` |
| Mageia | 10, 9 | `docker.io/library/mageia` | `MultiPak-Mageia-10` |
| openSUSE | Leap 16.1, 16.0 and Tumbleweed | `docker.io/opensuse/leap`, `…/tumbleweed` | `MultiPak-openSUSE-16.1`, `MultiPak-openSUSE-Tumbleweed` |
| ALT Linux | p11, p10 and Sisyphus | `docker.io/library/alt` | `MultiPak-ALT-p11`, `MultiPak-ALT-Sisyphus` |

The catalog keeps this list up to date from the image registry; the table above is the
offline fallback. A package from an old release (e.g. Leap 15.6) still gets that
release; a package from a release newer than the stable ones gets the rolling line
(Tumbleweed, Sisyphus) or, on Mageia, the newest stable release. Cauldron (Mageia's
development branch) is not offered: its container cannot be told apart from a future
stable release.

### 1.4 Theme and icons

Before every RPM installation, on all four distributions, MultiPak adds an icon theme,
the SVG loader and the Breeze themes for GTK 2 and 3, and follows the computer's light
or dark theme, GTK 2 programs included. Without this, openSUSE showed broken toolbar
icons.

### 1.5 Signatures

- dnf and APT-RPM install a local file without requiring a signature.
- zypper refuses a file whose signature it cannot verify. MultiPak checks first
  (`rpm -K`); if there is no valid signature with openSUSE's keys, it installs without
  the check, **with a warning**.

### 1.6 Updating and uninstalling

- **Update**: under **Manager → Update**, MultiPak updates the whole container with
  Distrobox's own command (`distrobox upgrade`), which runs `dnf upgrade`, `zypper dup`
  or `apt-get dist-upgrade`, depending on the distribution.
- **New versions**: the Manager asks the distribution's package manager (`dnf
  check-update` and `repoquery`, `zypper list-updates` and `search`, or `apt-cache
  policy` on APT-RPM) which applications have a new version and lists each one under
  **With updates**, **Already up to date** or **No update source** (installed from a
  file that no repository offers). On opening, it uses the list the container already
  has, without downloading anything; **Check for updates** refreshes the package lists
  first.
- **Uninstall**: `rpm -e`, which refuses if another package depends on the target.

### 1.7 Installing directly on the system (Fedora Atomic/Universal Blue only)

On systems with rpm-ostree, there is also direct installation into the system image. It
is a last resort: it requires explicit confirmation, can cause dependency conflicts or
affect system updates, and only takes effect after a restart.

### 1.8 Known limitations

- Packages from RHEL, Rocky, Alma or Nobara: with no clue in the header, they go to
  Fedora with Low confidence and a text explaining the risk.
- Mageia Cauldron.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `metadata.py` (`inspect_package`, `rpm_release_hints`, `rpm_metadata_hint`) | header reading and origin hint |
| `models.py` (`rpm_environment`, `RPM_FAMILY_ENVIRONMENTS`, `container_title`) | environments and names |
| `catalog.py` (`REPOSITORIES`, `registry_tags`, `rpm_family_profiles`, `rpm_package_environment`) | releases of the day and choice |
| `environments.py` (`release_identity`) | `opensuse-leap`/`-tumbleweed` → `opensuse`; `altlinux` → `alt` + branch |
| `engine.py` (`_install_rpm`, `_install_dnf`, `_install_zypper`, `_install_apt_rpm`, `MAGEIA_SETUP_HOOK`) | installation |
| `assets/rpm-setup.sh` | `--prepare`: icons, Breeze GTK, light/dark theme |
| `suggestions.py` (`rpm_family_details`) | confidence and the text naming the package manager |
| `system.py` | direct installation through rpm-ostree |

### 2.2 Reading

`rpm -qp --queryformat '%{NAME}\x1f%{VERSION}-%{RELEASE}\x1f%{ARCH}\x1f%{DISTRIBUTION}'`
(in the Flatpak, `/app/bin/rpm -qp --nosignature`), in binary mode, with an output
limit; empty fields or fields with control characters are refused. `--requires` gives
the dependencies; `warning: `/`error: ` lines (such as the NOKEY warning) are filtered
out.

### 2.3 Origin hint

`rpm_release_hints` applies, in order: `.fcN`; `.mgaN`; `lp`/`bp` + `15x`–`19x` (`lp156`
→ Leap 15.6); `1[5-9]XX00` at the start of *Release* (`160100.2.1` → Leap 16.1, since
Leap 16 is SLE 16 underneath); `altN` and `altN.pM`. `rpm_metadata_hint` gives priority
to *Release* and uses *Distribution* for Tumbleweed, "openSUSE Leap X" and "ALT pNN";
"ALT Sisyphus" becomes plain `alt` (packages copied from Sisyphus into a branch keep
that tag). If the header gives nothing, `rpm_release_hints(file name)` with a single
answer becomes a hint with a `filename` source (Medium confidence).

### 2.4 Choosing the environment

- Fedora: Distrobox's catalog and the registry give the versions; `fedora-latest` is
  resolved to the actual number before the container is named.
- Other families (`rpm_family_profiles`): `skopeo list-tags` tags ≥ `RPM_OLDEST`
  (Mageia 9, Leap 16.0, ALT p10; Leap 42.x ignored) + the rolling line.
- `rpm_package_environment`: exact match; family only → newest stable release; newer
  than the stable releases → rolling line or newest stable release; older → that same
  release.
- `suggestions.rpm_family_details`: **High** when it matches (naming the package
  manager), **Medium** without a release or with a newer one, **Low** if the family
  could not be offered.

### 2.5 Creating the container

`distrobox create` with the release's image. On Mageia, a pre-init hook
(`MAGEIA_SETUP_HOOK`): Distrobox 1.8.2.5's `setup_dnf` reinstalls `glibc-common` (a
Fedora package) when `/usr/share/i18n/charmaps` is missing and then runs `localedef`,
which the Mageia image deletes. The hook installs `glibc-i18ndata` and, if `localedef`
is missing, runs `dnf reinstall glibc`.

### 2.6 Installation (`engine._install_rpm`)

1. `rpm-setup.sh --prepare` (copied to the guest folder, run with sudo): theme packages
   per family (openSUSE `adwaita-icon-theme gdk-pixbuf-loader-rsvg
   gtk2/3-metatheme-breeze6`; Fedora/Mageia `adwaita-icon-theme breeze-gtk`; ALT
   `icon-theme-adwaita librsvg gtk-theme-breeze`), one at a time and none fatal; GTK 3
   `prefer-dark` in `/etc/xdg/gtk-3.0/settings.ini`; GTK 2 with an `include` of
   Breeze's or Breeze-Dark's gtkrc in `/etc/gtk-2.0/gtkrc` (the host's XSETTINGS sends
   `Net/ThemeName ""`).
2. Per family (from `environment_id`):
   - **Fedora/Mageia**: `dnf -y install --downloadonly` and then `dnf -y install`.
   - **openSUSE**: `zypper refresh`; `rpm -Kv` — no "Signature…: OK" line →
     `--no-gpg-checks --allow-unsigned-rpm` + warning; `zypper install
     --auto-agree-with-licenses`.
   - **ALT**: `apt-get update` and `apt-get -y install <file>`.

### 2.7 Inside the container (`guest.py`)

State with `rpm -q`, files with `rpm -ql`, removal with `rpm -e` (with rpm's dependency
checks; dependencies are not removed along with it).

### 2.8 Existing container

`release_identity` reads os-release: Tumbleweed's `VERSION_ID` is a date → `tumbleweed`;
ALT uses `ALT_BRANCH_ID` (or `p<VERSION_ID>`, or `sisyphus`). The container is reused
only if both family and release match.

### 2.9 How this differs from using the distribution directly

| Topic | On the distribution | MultiPak |
| --- | --- | --- |
| Package manager | the distribution's | the same one, without a terminal |
| zypper and unsigned packages | refuses | installs with a warning |
| Theme | the distribution desktop's | Breeze GTK, light or dark like the host |
| Uninstalling | `dnf remove` may take dependencies with it | `rpm -e`, the package only |

### 2.10 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-openSUSE-Tumbleweed -- rpm -qi <name>`.
- Missing library: check the confidence on the environment page and choose another
  release in the advanced list.

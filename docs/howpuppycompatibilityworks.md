# How MultiPak installs Puppy Linux packages

*Translated from the [Portuguese version](howpuppycompatibilityworks.pt-BR.md), which is
the reference text.*

*MultiPak 1.2.0.*

---

## 1. In plain terms

### 1.1 What is a `.pet`?

Puppy Linux is not a distribution with its own repository in the usual sense: each
Puppy "flavor" is built **from another distribution's packages** (Slackware, Debian or
Ubuntu). A `.pet` is a compressed TAR archive (gzip or xz) with the program's files, a
`pet.specs` metadata file and, at the end of the file, the md5 checksum of the rest. The
metadata says, among other things, **which distribution the package was built for**
(e.g. `slackware64|15.0`, `ubuntu|focal`, `debian|bookworm`).

### 1.2 Why is there no Puppy container?

Because none is needed: the programs in a `.pet` were compiled for the source
distribution. MultiPak installs the `.pet` **on that distribution**, in its container
(for example, `MultiPak-Debian-12`), and that distribution's package manager then
records and removes the package like any other.

### 1.3 What does MultiPak do?

1. **Checks and reads the package without running it:** it computes the file's md5 and
   compares it with the one at the end; it reads only `pet.specs`. The architecture
   comes from the programs themselves (ELF).
2. **Suggests the environment:** Slackware 15.0, Debian 12, Ubuntu 20.04, 22.04 or
   24.04, according to `pet.specs`. If the metadata says nothing, or names something
   MultiPak does not offer, it suggests Debian 12 with **Low** confidence and a warning.
3. **Converts:** it rewrites the package with root ownership, no setuid bits, 755
   directories and without `pet.specs`. On Slackware it becomes
   `name-version-arch-1_pet.txz`; on Debian/Ubuntu, a `.deb` with a lowercase name.
4. **Adds the dependencies:** Puppy names that exist in the distribution are used; the
   libraries the programs cannot find (`ldd`) become distribution packages (on
   Debian/Ubuntu, through the repository's file index, `apt-file`).
5. **Installs** with the distribution's package manager and creates the shortcuts.

Puppy's scripts (`pinstall.sh`, `puninstall.sh`) run inside the container, with a time
limit; if they fail, only a warning appears in the log.

### 1.4 Updating and uninstalling

After the conversion, the installation is an ordinary Slackware or DEB one: updating the
container and uninstalling work as for those formats. When uninstalling,
`puninstall.sh` runs first. Since the package came from a `.pet` file, which no
repository offers, the check for new versions lists it under **No update source**; to
update it, install the new version from its file.

### 1.5 Known limitations

- 32-bit `.pet` files (refused).
- Old bases (tahr, xenial, slacko 14.x) get Low confidence.
- Libraries that Puppy itself provides in other `.pet` files (e.g. S15Pup's
  `libgoffice`) do not exist in the distribution: MultiPak warns you to install those
  `.pet` files first, in the same environment.
- Libraries in `/usr/lib64` inside a `.pet` installed on Debian are not found at run
  time.
- A `.pet` with the same name as an official package may be replaced by apt during an
  update, if the repository has a higher version.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `puppy.py` | reading, md5, `compatible_environment`, `to_slackware`, `to_deb`, names |
| `metadata.py` | detection after Arch and before Gentoo; hint from the metadata |
| `models.py` (`package_environment_kinds`, `package_profiles`) | the `.pet` sees `slackware` and `deb` environments |
| `engine.py` (`install`, `_convert_puppy`, `_puppy_deb_dependencies`) | copy, conversion and dependencies |
| `guest.py` (`puppy_deb_dependencies`, `run_puppy_script`, `slackware_dependencies`) | ldd, apt-file, scripts |
| `suggestions.py` | High when it matches the metadata; Low with the suggested environment |
| `package_icons.py` | icon read from inside the `.pet` |
| `assets/multipak-puppy.xml` | MIME `application/x-puppy-pet` (`*.pet`) |

### 2.2 Reading

`puppy.is_candidate`: gzip/xz named `.pet` or with the 32-character md5 trailer. The md5
is computed over the whole file minus the trailer (512 MB limit). `pet.specs`: fields
separated by `|`; fields 10 and 11 give the distribution and release. The version loses
Puppy's suffix (`3.0.4-x86_64_s84` → `3.0.4`). An empty architecture becomes `noarch`;
i686 is refused.

### 2.3 Choosing the environment

`compatible_environment`: `slackware|15.0`/`slackware64|15.0` → `slackware-15.0`;
`debian|bookworm`/`12` → `debian-12`; `ubuntu|focal`/`jammy`/`noble` →
20.04/22.04/24.04. Devuan and unknown values give "" → `debian-12` with a `default`
source. When the metadata names a distribution, `package_profiles` shows only that
family's environments (e.g. `slackware64|14.0` → Slackware 15.0, Low confidence).

### 2.4 Conversion (`engine._convert_puppy`)

`engine.install` copies the `.pet` to `source.pet`, rereads it to check that it is the
same package, and calls `puppy.to_slackware` or `puppy.to_deb`, which rewrite the TAR
archive member by member: special files are refused, and so is an `install/` folder on
Slackware. `pinstall.sh`/`puninstall.sh` go to `/usr/share/multipak/puppy/<name>/`:

- Slackware: `install/doinst.sh` runs pinstall; puninstall runs in `guest --remove`,
  before `removepkg`.
- Debian: `postinst` runs pinstall and `prerm` runs puninstall.

Both with `timeout 300` and `cd /`. From then on the transaction is Slackware or DEB
(`converted_from="puppy"`).

### 2.5 Dependencies

- **Slackware**: Puppy's names go into `--slackware-dependencies` (only those that
  exist in slackpkg), together with Slackware's ldd-based search (see
  [How MultiPak installs Slackware packages](howslackwarecompatibilityworks.md)).
- **Debian/Ubuntu** (`guest.py --puppy-deb-dependencies`): `apt-get update`;
  `dpkg-deb -x` and `ldd` on the ELF files with `LD_LIBRARY_PATH` set to the package's
  `.so` folders; installs `apt-file`, runs `apt-file update` and `search --regexp`,
  preferring system library folders; adds Puppy names that exist in apt and graphical
  data packages when GTK, X11, Qt… are present; rewrites `Depends` (`dpkg-deb -R` and
  `-b --root-owner-group`). Then the normal DEB installation (`apt-get install`).
- Library not found → `puppy_missing_warning` warning.

### 2.6 How this differs from plain Puppy

| Topic | Plain Puppy | MultiPak |
| --- | --- | --- |
| Record | Puppy's `/root/.packages` | the source distribution's package manager |
| Ownership and setuid | as they come | root, no setuid |
| Scripts | run as root on the system | run in the container, with a time limit |
| Dependencies | other `.pet` files | official packages of the source distribution |

### 2.7 Troubleshooting

- `~/.local/share/multipak/installations/<id>/installation.log` (packages added,
  missing libraries, pinstall failure).
- Debian/Ubuntu: `dpkg -s <name>` in the container shows the generated `Depends`.
- Slackware: `/var/log/packages/<name>-…_pet` in the container.

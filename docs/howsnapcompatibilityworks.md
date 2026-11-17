# How MultiPak installs and runs snaps

*MultiPak 1.2.0. Translated from the [Portuguese version](howsnapcompatibilityworks.pt-BR.md), which is the reference text.*

---

## 1. In plain terms

### 1.1 What is a snap?

A snap is an application **packaged with everything it needs**: the program, its
libraries and even a "mini-Ubuntu" underneath (called the **base**, such as `core22` or
`core24`). The idea is that it works the same on any Linux system.

Normally, snaps are installed and run by a system program called **snapd**. It runs as
administrator (root), is always on, and relies on features that not every distribution
has (such as AppArmor). On atomic systems, installing snapd is hard or impossible.

### 1.2 What does MultiPak do?

MultiPak does snapd's job **without snapd and without asking for an administrator
password**.

Think of a snap as a sealed box:

1. **Download:** MultiPak fetches the box from the official Snap Store and checks that
   it arrived intact (it compares the file's "fingerprint" with the one the store
   publishes).
2. **Unpack:** it opens the box into a folder of yours (`~/.local/share/multipak`). The
   base, the "mini-Ubuntu", is downloaded only once and shared by every snap that uses
   it.
3. **Run in a bubble:** when you open the application, MultiPak builds a **bubble** (a
   *sandbox*) with a tool called **bubblewrap**. Inside the bubble, the application sees
   the world as if it were on Ubuntu with snapd: its base, its own files and your home
   folder. Outside the bubble, nothing on your system changes.
4. **Add to the menu:** the application shows up in the menu like any other.

### 1.3 Classic snaps

Most snaps stay confined to the bubble described above. Some, called **classic** snaps,
are published to run **without confinement**, like a normally installed program: they
see the whole system. This is the case for code editors and development tools (VS Code,
IntelliJ, `go`, `node`), which need to use the computer's compilers and files.

A classic snap expects to find its own files in a `/snap` folder at the root of the
system. On atomic systems that folder does not exist and cannot be created, because the
root is read-only, so these snaps do not run there even with snapd. MultiPak gets around
this with bubblewrap: it shows the program the whole computer plus an extra `/snap`
folder holding the snap's files. That folder exists only in the program's view; nothing
is created or changed on the real computer.

Since they are not confined, MultiPak does not run these snaps' administrator commands.

### 1.4 What about "services"?

Not every snap is an application with a window. Some are **servers** that keep running
in the background, with no window. Nextcloud, for example, has a database, a web server
and other programs.

MultiPak runs these services through **your user's systemd** (the same mechanism your
desktop uses for its own background programs), each one in its own bubble. It:

- starts the services after installation;
- restarts a service that crashed;
- stops everything in about a second when you ask;
- starts them again when you log in after restarting the computer.

You can see, start and stop the services under **Manager → Snap services → Background
services**: each service has a button to start or stop it and one to view its log.

### 1.5 "Root" without being root

Snap installation scripts and many services assume they are running as administrator
(root). MultiPak lets them **believe** they are root, but only **inside the bubble**. As
far as the computer is concerned, they still run as your user, with no extra powers.

The same trick lets you run a snap's **administrator commands**, such as Nextcloud's
`occ`: **Manager → Snap services → Administrator commands**, after a warning. The
command is root only inside that snap's bubble. Applications with a window (those with a
menu shortcut, such as Firefox) are left out of this list and do not run as
administrator: as root, they would leave files in the snap's data that only root can
change.

### 1.6 Updating and rolling back

MultiPak checks the store for new versions. When it updates a snap with services, it
keeps the services running while it downloads and prepares everything, and stops them
only for a few seconds during the switch. If the new version fails to configure itself,
it **rolls back automatically** to the previous one. You can also choose **Revert to
previous version** under **Manager → Update**, with the snap selected.

### 1.7 Is it a "translation layer"?

Not quite. The more accurate term is **compatibility layer**: for snaps, MultiPak builds
an environment of its own similar to snapd's, enough for them to run.

- A translation layer, such as **Wine**, converts the calls of a program made for
  another system (Windows) into calls Linux understands. It "translates" the program
  while it runs.
- A snap **is already a Linux program**. Its binaries run directly on the kernel, at the
  same speed, with nothing in between. What is missing without snapd is the surrounding
  **environment**: the base mounted as the root, the `/snap/...` and `/var/snap/...`
  folders, the `SNAP_*` variables, a private `/tmp`, something to answer `snapctl`,
  something to look after the services. MultiPak **builds a similar environment** with
  ordinary tools (bubblewrap and the user's systemd).

There is only one place where something like translation happens: when the snap makes a
request to snapd (through `snapctl`, such as "store this setting" or "restart service
X"), MultiPak handles it instead and turns the request into whatever it has at hand: a
settings file, a command to the user's systemd, a window on the desktop.

### 1.8 Known limitations

- Snaps that ask for **system permissions** (for example, to control the computer's
  network): MultiPak refuses them, on purpose.
- Services that need **ports below 1024** (80, 443, 53): without real root, this is not
  possible. The port can usually be changed.
- Services start when you **log in**, not when the computer boots.
- Services started through the system D-Bus, such as those of the `bluez`,
  `network-manager` and `udisks2` snaps. These snaps are made mainly for Ubuntu Core,
  the version of Ubuntu for devices; on a desktop, the system itself already provides
  these functions.

---

## 2. Technical details

Code paths are relative to `src/multipak`.

### 2.1 Module overview

| Module | Role |
| --- | --- |
| `snap_store.py` | the Snap Store's public, anonymous API; download checked against SHA3-384 |
| `snap_yaml.py`, `snap_meta.py` | strict reading of `meta/snap.yaml` and the refusal rules |
| `snap.py` | installation, hooks, launching apps and services, uninstallation |
| `snap_sandbox.py` | builds the bubblewrap arguments (the "view" snapd would give) |
| `snap_ctl.py` | `snapctl` without snapd: a shell client inside the bubble + a server in MultiPak |
| `snap_daemons.py` | services as transient `systemd --user` units |
| `snap_timer.py` | converts snapd timers into systemd calendar events |
| `snap_refresh.py`, `snap_updates.py`, `snap_runtime_updates.py` | updating and rollback |
| `snap_components.py` | optional components (snapd 2.67+) |
| `snap_dialog.py`, `snap_open.py` | `zenity` and `xdg-open` handled on the user's desktop |
| `snap_launcher.py` | the `multipak-snap` command (apps, services, `--set`, `--admin`) |

### 2.2 Installation

1. **Download** (`snap_store`): MultiPak uses the Snap Store's public API, with no
   account or login. The downloaded revision is checked against the SHA3-384 the store
   publishes for it before it is used.
2. **Reading and refusal** (`snap_meta.refusal`): the snap is refused if it is a system
   snap (`kernel`, `gadget`…), built for another architecture, if it asks for **system
   interfaces** (`network-control`, `snapd-control`…), unknown system users, system
   files that snapd itself would not accept, or if it only has services MultiPak cannot
   run.
3. **Bases and content** (`ensure_runtime`): the base and the content snaps (`content`
   plugs, such as `gnome-42-2204`) are extracted once into
   `~/.local/share/multipak/snap-runtime/<name>/<revision>` and shared. An incomplete
   folder in that place (without `meta/snap.yaml`) is moved out of the way first.
   `collect_runtime` deletes whatever no snap uses anymore.
4. **Extraction** of the snap itself into `snaps/<id>/root`.
5. **Hooks** `install` and `configure` (`run_hook`), as root in the bubble (2.4).
6. **Registration** in `installations/<id>/manifest.json`, `.desktop` shortcuts, session
   D-Bus services, service autostart and startup of the enabled services.

Folders of an installed snap:

```
~/.local/share/multipak/
├── installations/<id>/manifest.json      record (revision, channel, warnings…)
├── snap-runtime/<base>/<rev>/            shared bases and content
└── snaps/<id>/
    ├── root/            /snap/<name>/<rev>   (read-only)
    ├── data/<rev>/      $SNAP_DATA           /var/snap/<name>/<rev>
    ├── data/common/     $SNAP_COMMON         /var/snap/<name>/common
    ├── root-home/snap/  /root/snap           (HOME for hooks and services)
    ├── state/config.json                      what "snapctl set" stored
    ├── accounts/        passwd/group with the snap's system users
    ├── tools/           snapctl client
    └── channels/        snapctl request channels in use
~/snap/<name>/<rev>      $SNAP_USER_DATA (apps running as the user)
/tmp/multipak-private-tmp-<uid>/<id>/tmp   the snap's /tmp
```

### 2.3 The bubble (`snap_sandbox.build`)

For **strict** and **devmode** snaps, bubblewrap starts with `--tmpfs /` and mounts:

- the **base** as the root (`/usr`, `/bin`, `/lib`…), read-only;
- from the computer, the list `snap-confine` uses in normal mode: `/home`, `/run`,
  `/var/tmp`, `/mnt`, `/media`, `/dev`, `/sys`, a private `/proc`;
- a **composed `/etc`**: the base's, with the computer's entries that describe the
  machine (`passwd`, `group`, `hosts`, `resolv.conf`, `localtime`, fonts, GPU…), as
  Flatpak does. The snap's system users go into a `passwd`/`group` of their own;
- `/snap/<name>/<rev>` (and `current`) for the snap, the base and the content providers;
  `/var/snap/<name>/{<rev>,common}` for the data;
- the `snap.yaml` **layouts** (`bind`, `bind-file`, `symlink`, `tmpfs`). Where a layout
  needs a path that does not exist in a read-only directory, that directory becomes a
  writable copy whose entries are bound back to the original (snapd's *writable mimic*);
- the **snap's /tmp**: a folder in the computer's `/tmp`, shared by all apps, services
  and hooks of the same snap (this is how Nextcloud's PHP finds `mysql.sock`) and
  invisible to other snaps, which never see the computer's `/tmp`. The computer empties
  it on restart, and uninstallation deletes it;
- folders that exist only to hold mounts (`/snap/<name>`) are created with `--dir`
  (0755). If bubblewrap created them implicitly, they would be 0700, closed to the
  snap's system users.

**Classic** snaps see the computer's root (as with snapd) and the computer's `/tmp`.

The environment follows snapd's (`SNAP`, `SNAP_DATA`, `SNAP_USER_DATA`, `SNAP_NAME`,
`PATH` with `$SNAP/usr/bin`…), followed by the snap's and the app's `environment`,
expanded in order.

### 2.4 Identities: the user, "root" and system users

| Who runs | Identity inside the bubble | On the computer |
| --- | --- | --- |
| regular app | the user (uid 1000) | the user |
| user service (`daemon-scope: user`) | the user | the user |
| hook, system service, app with `--admin` | root (uid 0) | the user |
| process that switches to `snap_daemon` / `_daemon_` | 584788 / 584792 | one of the user's subordinate IDs |

- **Root without privileges:** `bwrap --unshare-user --uid 0 --gid 0` creates a *user
  namespace* in which the user appears as root. bubblewrap drops all of that root's
  capabilities (`CapEff=0`); it only "owns" the user's files.
- **System users** (`system-usernames`): the snap can switch to `snap_daemon` (for
  example with `setpriv` or its own `drop`). For that, `user_mapping` sets up the
  namespace with `unshare --map-users/--map-groups` (through `newuidmap`/`newgidmap`, as
  rootless Podman does): root = the user, `snap_daemon` = the last subordinate ID in
  `/etc/subuid`, `_daemon_` = the one before it. bubblewrap joins that namespace instead
  of creating its own (`bubble_command`). Files written by `snap_daemon` show up on the
  computer with a high numeric owner (for example, 589823) and are handed back to the
  user (`chown` inside the namespace) before they are deleted.
- **Administrator mode** (`plan_launch(admin=True)`, `multipak-snap --admin`,
  "Administrator commands"): runs the **app**'s command with the hooks' identity (HOME
  `/root/snap/<name>/<rev>`, `USER=root`). It refuses classic snaps (without confinement
  there is no "root only in there") and services.

### 2.5 `snapctl` without snapd (`snap_ctl.py`)

Inside the bubble, `snapctl` is a POSIX script (the base may have nothing more than a
shell and coreutils). It writes the request to a shared folder
(`/snap/.multipak/channel`) and waits for the answer on a *named pipe*. MultiPak answers
as snapd would wherever there is an equivalent: `get`/`set`/`unset` (stored in
`state/config.json`), `services`, `start`/`stop`/`restart` (inside a hook, deferred
until it finishes, as in snapd), `is-connected`, `user-open` (newer bases' `xdg-open`
opens on the desktop), and so on. When there is no equivalent, the answer says so
clearly.

The answers come from a `snap_ctl.Server` (a *thread*) inside the MultiPak process that
opened that bubble, for as long as the bubble exists, and each bubble has its own
channel (`snaps/<id>/channels/<channel>`):

| In the bubble | Process that answers `snapctl` |
| --- | --- |
| app (including with `--admin`) | `multipak-snap <id> <app>` |
| service | `multipak-snap --service <id> <service>` (started by systemd) |
| `--set` hook | `multipak-snap --set` |
| install, refresh or remove hook | the wizard or the Manager performing the operation |

### 2.6 Services (`snap_daemons.py`, `snap.run_service`)

- Each service becomes a **transient unit** of `systemd --user`
  (`multipak-snap-<id>-<service>.service`, created with `systemd-run`), which runs
  `multipak-snap --service <id> <service>`. Its properties come from `snap.yaml`:
  `Restart` (`restart-condition`), `RestartSec`, `TimeoutStopSec`, `After`/`Before`,
  `Type=oneshot`; at most 5 starts in 120 s.
- **Timers** become `.timer` units with `OnCalendar` (and `RandomizedDelaySec` for
  windows such as `9:00~11:00`).
- The launcher holds the service lock (`services.lock`), keeps a `snapctl` server
  running and runs the bubble with `--unshare-pid`: when the service ends, nothing it
  started is left behind. The `SERVICE` script, inside the bubble:
  - randomizes the starting PID number (`ns_last_pid`, or by using up a few), so that a
    stale PID file does not point to the new process after a `kill -9`;
  - starts the command and waits; with `forking`, it also waits for whatever the command
    left running;
  - handles `stop`/`reload` through a *named pipe* (`stop-command`, `stop-mode`,
    `post-stop-command`).
- **Stopping:** systemd cannot reach processes inside the Flatpak sandbox; `ExecStop`
  calls `multipak-snap --services halt`, which asks the launcher to stop the bubble.
  `Services.stop` stops all units in a single `systemctl stop` (loaded ones only), so
  they stop in parallel.
- **Lifeline pipe:** if the Python launcher dies (even with `kill -9`), a watcher on the
  computer reads EOF on a pipe and terminates the whole group.
- **`notify`, watchdog and sockets:** `NOTIFY_SOCKET` points to MultiPak's channel,
  which passes readiness on; sockets (`sockets:`) are opened by the launcher and handed
  over as fd 3, 4… with `LISTEN_FDS`, activating the service on the first connection.
- **After a restart:** transient units disappear on reboot. The
  `~/.config/autostart/multipak-snap-<id>-services.desktop` file runs
  `multipak-snap --services login <id>` when the session starts, starting every enabled
  service (those already running are skipped).
- Command line: `multipak-snap --services list|start|stop|restart <id> [service…]`.

### 2.7 Updating and rollback (`snap_refresh.py`)

1. Downloads and extracts the new revision into a staging directory (`.refresh`), along
   with new bases and content, **while the services keep running**.
2. Runs the old revision's `pre-refresh`. If it refuses, nothing changes and the
   services keep running.
3. Stops the services, waits for the processes to end, copies `$SNAP_DATA` to the new
   revision and switches revisions.
4. Runs the new revision's `post-refresh` with the services stopped (as snapd does). If
   it fails, it **undoes the switch** and goes back to the previous revision.
5. Starts the services. The previous revision is kept for **Revert to previous
   version**.

In practice, the service is down for only a few seconds.

### 2.8 Uninstallation

It stops the services (if any of them does not stop, nothing is deleted), runs the
`remove` hook, hands the system users' files back to the user, deletes the snap's
folders, its private `/tmp`, shortcuts, autostart and D-Bus services, and
garbage-collects bases left unused.

### 2.9 How this differs from snapd

| Topic | snapd | MultiPak |
| --- | --- | --- |
| Privileges | root daemon | none; everything runs as the user |
| Confinement | AppArmor + seccomp + namespaces | bubblewrap namespaces (no per-interface AppArmor/seccomp) |
| Interfaces | connected and mediated one by one | the common desktop ones; system ones are refused |
| System services | root, at boot | root only in the bubble, at login |
| Ports < 1024 | yes | no |
| Services activated through the system D-Bus | yes | no |
| `/etc` | the computer's, with a few entries from the base | the base's, with entries from the computer |
| `sudo <snap>.<app>` | yes | `multipak-snap --admin` / "Administrator commands" |
| Automatic updates | yes | the user checks and updates in the Manager |

### 2.10 Troubleshooting

- Installation log: `~/.local/share/multipak/installations/<id>/installation.log`.
- Service output: `journalctl --user -u 'multipak-snap-<id>-*'` (or the service's log
  button, under **Manager → Snap services**).
- State: `multipak-snap --services list <id>`.
- To run a service in the foreground and see the error: `flatpak run
  --command=multipak-snap io.github.goulielmos_linux.multipak -- --service <id>
  <service>`.

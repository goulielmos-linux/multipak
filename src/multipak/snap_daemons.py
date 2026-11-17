"""A snap's background services, run as transient services of the user's systemd.

Each service runs in the same bubble as the snap's apps, as root only inside it, and
nothing is written to the system. Services that need the system bus or ports below
1024 cannot run; the review page says so."""

import re
import time
from graphlib import CycleError, TopologicalSorter

from .localization import tr
from .process import CommandError

UNIT = re.compile(r"[a-f0-9]{32}")
STOP_SECONDS = 30
# How long a notify service may take to say it is ready before the services that
# come after it are started anyway, as systemd's own default start timeout.
READY_SECONDS = 90


# snapd's restart-condition as systemd's Restart=. To systemd every service ends as
# MultiPak's launcher ends, with an exit status: those that tell how the service
# ended become on-failure, which covers them.
RESTART = {
    "never": "no",
    "on-success": "on-success",
    "on-failure": "on-failure",
    "on-abnormal": "on-failure",
    "on-abort": "on-failure",
    "on-watchdog": "on-failure",
    "always": "always",
}


def _systemd_quote(word):
    """One word of a systemd command line."""
    return '"' + str(word).replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


def ready_path(store, tx, name):
    """Present while a notify service has said it is ready (READY=1)."""
    return store.snap_directory(tx.id) / "ready" / name


def ordered(info, names=None):
    """The runnable services, or those of names, in the order snapd would start them."""
    from .snap_meta import runnable_services

    runnable = runnable_services(info)
    chosen = [name for name in runnable if names is None or name in names]
    graph = {name: set() for name in chosen}
    for name in chosen:
        app = info.app(name)
        graph[name].update(item for item in app.after if item in chosen)
        for later in app.before:
            if later in chosen:
                graph[later].add(name)
    try:
        return tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError(tr("The snap's services depend on each other in a loop.")) from error


def unit(tx, name):
    if not UNIT.fullmatch(tx.id):
        raise ValueError(tr("This snap installation is unavailable."))
    return f"multipak-snap-{tx.id}-{name}.service"


def host_call(argv):
    """Run a systemd command on the computer: (exit code, output)."""
    from . import host

    result = host.run(argv, capture_output=True, text=True, timeout=120, check=False)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def runner_call(runner):
    def call(argv):
        try:
            return 0, runner.run(argv, timeout=120)
        except CommandError as error:
            return error.code if error.code is not None else 1, error.output or str(error)

    return call


class Services:
    """Starts, stops and lists one installation's services.

    call(argv) -> (code, output) runs systemctl and systemd-run on the computer.
    deferred: inside the snap's scripts, as snapd does while it installs or
    configures, requests to start or stop are accepted and left to what follows."""

    def __init__(
        self, tx, info, call=host_call, *, deferred=False, store=None, clock=time.monotonic
    ):
        self.tx, self.info, self.call, self.deferred = tx, info, call, deferred
        self._store, self.clock = store, clock

    @property
    def store(self):
        if self._store is None:
            from .store import Store

            self._store = Store()
        return self._store

    def ready(self, name):
        return ready_path(self.store, self.tx, name).exists()

    def _wait_ready(self, name):
        """Wait for a notify service to say it is ready, as systemd waits before
        starting what comes after it. Running and not ready by the deadline: the
        rest starts anyway, as with a start timeout that does not stop it."""
        deadline = self.clock() + READY_SECONDS
        while self.clock() < deadline:
            if self.ready(name) or self.state(name, raw=True) not in ("active", "activating"):
                return
            time.sleep(0.2)

    def names(self, requested):
        """Service names from snapctl's arguments: snap.app, the snap's name for all
        of them, or nothing for all of them."""
        options = [item for item in requested if item.startswith("-")]
        wanted = [item for item in requested if not item.startswith("-")]
        unknown = [o for o in options if o not in ("--enable", "--disable", "--reload")]
        if unknown:
            raise ValueError(tr("unknown option {0}").format(unknown[0]))
        if not wanted:
            return list(self.info.services)
        names = []
        for item in wanted:
            if item == self.info.name:
                names.extend(self.info.services)
                continue
            name = item.removeprefix(self.info.name + ".")
            if name not in self.info.services:
                raise ValueError(tr("unknown service: {0}").format(item))
            names.append(name)
        return list(dict.fromkeys(names))

    def _unit(self, name):
        """The unit that shows whether the service is on: its timer, if it has one."""
        base = unit(self.tx, name)
        return base.removesuffix(".service") + ".timer" if self.info.app(name).timer else base

    def state(self, name, *, raw=False):
        if name not in ordered(self.info):
            return "inactive"
        _code, output = self.call(["systemctl", "--user", "is-active", self._unit(name)])
        word = output.strip().splitlines()[0] if output.strip() else ""
        word = word if word in ("active", "activating", "deactivating", "failed") else "inactive"
        # To systemd a notify service is running once started; it is "activating"
        # until it says it is ready, as systemd itself would show it.
        if not raw and word == "active" and self.info.app(name).daemon == "notify":
            return "active" if self.ready(name) else "activating"
        return word

    def table(self, requested=()):
        """snapctl services, in snapd's columns."""
        names = self.names(list(requested))
        if not names:
            return ""
        labels = [f"{self.info.name}.{name}" for name in names]
        width = max(len("Service"), *(len(label) for label in labels))
        lines = [f"{'Service':<{width}}  Startup   Current   Notes"]
        for name, label in zip(names, labels, strict=True):
            app = self.info.app(name)
            startup = "enabled" if app.install_mode == "enable" else "disabled"
            current = self.state(name)
            notes = "-" if not app.unsupported else ",".join(app.unsupported)
            lines.append(f"{label:<{width}}  {startup:<8}  {current:<8}  {notes}")
        return "\n".join(lines) + "\n"

    def _command(self, name):
        from .snap import launcher_invocation
        from .snap_timer import calendar

        app = self.info.app(name)
        argv = [
            "systemd-run",
            "--user",
            "--quiet",
            "--no-block",
            "--collect",
            # Without a suffix: with a timer, systemd-run names both units after it.
            f"--unit={unit(self.tx, name).removesuffix('.service')}",
            f"--description=MultiPak snap {self.info.name}: {name}",
            # MultiPak's launcher gives the service its own stop timeout, then ends it:
            # systemd waits a little longer for that.
            f"--property=TimeoutStopSec={int((app.stop_timeout or STOP_SECONDS) + 15)}",
        ]
        # MultiPak's launcher stays until the service ends, also for forking ones,
        # and nothing reaches systemd's notification socket from the bubble: to
        # systemd, all but oneshot services are simple ones.
        if app.daemon == "oneshot":
            argv.append("--property=Type=oneshot")
            if not app.timer:
                argv.append("--property=RemainAfterExit=yes")
            if app.start_timeout:
                argv.append(f"--property=TimeoutStartSec={int(app.start_timeout) or 1}")
        if app.timer:
            values, delay = calendar(app.timer)
            argv.append(f"--on-calendar={values[0]}")
            argv += [f"--timer-property=OnCalendar={value}" for value in values[1:]]
            if delay:
                argv.append(f"--timer-property=RandomizedDelaySec={delay}")
        else:
            argv.append(f"--property=Restart={RESTART.get(app.restart_condition, 'on-failure')}")
            argv.append(f"--property=RestartSec={(app.restart_delay or 2):g}")
        # Stopping goes through MultiPak's launcher, which stops the service in its
        # bubble: signals from systemd do not reach into a Flatpak's sandbox.
        halt = [*launcher_invocation(), "--services", "halt", self.tx.id, name]
        argv.append("--property=ExecStop=" + " ".join(_systemd_quote(word) for word in halt))
        # A service that keeps failing stops being restarted, instead of looping.
        argv += ["--property=StartLimitIntervalSec=120", "--property=StartLimitBurst=5"]
        others = set(self.info.services) - {name}
        for relation, values in (("After", app.after), ("Before", app.before)):
            for other in values:
                if other in others:
                    argv.append(f"--property={relation}={unit(self.tx, other)}")
        return [*argv, "--", *launcher_invocation(), "--service", self.tx.id, name]

    def start(self, names):
        failures = []
        chosen = ordered(self.info, names)
        for index, name in enumerate(chosen):
            if self.state(name, raw=True) in ("active", "activating"):
                continue
            code, output = self.call(self._command(name))
            if code:
                failures.append(f"{name}: {output.strip() or code}")
                continue
            later = chosen[index + 1 :]
            app = self.info.app(name)
            if app.daemon == "notify" and any(
                name in self.info.app(other).after or other in app.before for other in later
            ):
                self._wait_ready(name)
        if failures:
            raise RuntimeError(
                tr("Could not start the snap's services:\n{0}").format("\n".join(failures))
            )

    def stop(self, names):
        """Stop them all with one request, as snapd does: systemd stops them at the
        same time, in the reverse of their order where they have one (After=,
        Before=), instead of each one waiting for the one before."""
        chosen = list(reversed(ordered(self.info, names)))
        units = {}
        for name in chosen:
            if self.info.app(name).timer:
                units[self._unit(name)] = name
            units[unit(self.tx, name)] = name
        # systemctl refuses the whole request if one unit is not loaded (a service
        # that ended, collected by systemd): only those it has are asked to stop.
        loaded = self._loaded(list(units))
        if not loaded:
            return
        code, _output = self.call(["systemctl", "--user", "stop", *loaded])
        if code == 0:
            return
        # Refused or failed: one at a time, as a last resort.
        failures = []
        for item in loaded:
            code, output = self.call(["systemctl", "--user", "stop", item])
            # 5: the unit is not loaded, which is what stopped looks like here.
            if code not in (0, 5):
                failures.append(f"{units[item]}: {output.strip() or code}")
        if failures:
            raise RuntimeError(
                tr("Could not stop the snap's services:\n{0}").format("\n".join(failures))
            )

    def _loaded(self, units):
        """The units systemd has loaded, of units; all of them if it cannot tell."""
        code, output = self.call(
            ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "--full"]
            + ["--", *units]
        )
        if code:
            return units
        listed = {line.split()[0] for line in output.splitlines() if line.split()}
        return [item for item in units if item in listed]

    def running(self, names=None):
        """The services still running, or starting or stopping; of names, if given."""
        return [
            name
            for name in ordered(self.info, names)
            if self.state(name, raw=True) in ("active", "activating", "deactivating")
        ]

    def _reload(self, name):
        """reload-command, in the running service's bubble; False if not running."""
        from .snap import running_path, send_control

        try:
            channel = running_path(self.store, self.tx, name).read_text(encoding="utf-8")
        except OSError:
            return False
        return send_control(f"{channel.strip()}/control", "reload")

    def act(self, command, requested):
        """snapctl start, stop and restart."""
        names = self.names(list(requested))
        idle = [name for name in names if self.info.app(name).unsupported]
        if command in ("start", "restart") and idle and len(idle) == len(names):
            raise ValueError(
                tr("MultiPak cannot run these services yet: {0}").format(", ".join(idle))
            )
        if self.deferred:
            return ""
        if command == "restart" and "--reload" in requested:
            # Those that can reload do, if running; the rest restart.
            names = [
                name
                for name in names
                if not (self.info.app(name).reload_command and self._reload(name))
            ]
            if not names:
                return ""
        if command in ("stop", "restart"):
            self.stop(names)
        if command in ("start", "restart"):
            self.start(names)
        return ""

    def start_enabled(self, scope=None):
        """What snapd starts after installing, and user services when the session
        starts: the services not marked install-mode: disable; with scope, only
        those of that scope."""
        self.start(
            [
                name
                for name in ordered(self.info)
                if self.info.app(name).install_mode == "enable"
                and scope in (None, self.info.app(name).scope)
            ]
        )

    def stop_all(self):
        self.stop(list(self.info.services))

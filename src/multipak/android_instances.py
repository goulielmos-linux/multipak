"""Named Android environments, separate from the system Waydroid instance."""

import json
import os
import re
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from . import host
from .android import Waydroid
from .android_progress import AndroidProgress, AndroidStartupOutput
from .localization import tr
from .process import Cancelled, CommandError

CLI = "/usr/local/bin/multipak-waydroid"
BASE = Path("/var/lib/multipak-waydroid/instances")
PROFILES = (("android-13", "Android 13", "20.0", 33), ("android-11", "Android 11", "18.1", 30))


def valid_id(identifier):
    return isinstance(identifier, str) and bool(re.fullmatch(r"[a-z][a-z0-9]{0,10}", identifier))


def valid_label(label):
    return (
        isinstance(label, str)
        and bool(label.strip())
        and len(label) <= 64
        and not re.search(r"[\x00-\x1f\x7f]", label)
    )


def profile_for(release):
    return next(p for p in PROFILES if p[2] == release)


def discover(runner):
    if not host.which("multipak-waydroid"):
        return []
    entries = json.loads(runner.run([CLI, "list", "--json"], timeout=10))
    result = []
    for entry in entries:
        if (
            not valid_id(entry.get("id"))
            or entry.get("owner") != os.getuid()
            or entry.get("release") not in ("20.0", "18.1")
        ):
            continue
        profile = profile_for(entry["release"])
        result.append({**entry, "profile": profile[0], "version": profile[1], "sdk": profile[3]})
    return sorted(result, key=lambda e: (e["release"], e["label"].casefold()), reverse=True)


def keep_ready_path(instance_id):
    home = Path(os.environ.get("HOME") or Path.home())
    return home / ".config/autostart" / f"multipak-android-{instance_id}-ready.desktop"


def send_files(runner, instance_id, paths, progress=lambda done, total, name: None):
    """Send files to one environment's Download folder; progress(done, total, name) follows
    each file. Errors come as codes from the command and are translated here."""
    if not paths:
        raise ValueError(tr("Choose the files to send."))
    state = {"buffer": "", "name": "", "error": None}

    def output(text):
        state["buffer"] += text
        *lines, state["buffer"] = state["buffer"].split("\n")
        for line in lines:
            kind, _, rest = line.strip().partition(" ")
            if kind == "START":
                total, _, state["name"] = rest.partition(" ")
                progress(0, int(total), state["name"])
            elif kind == "PROGRESS":
                done, _, total = rest.partition(" ")
                progress(int(done), int(total), state["name"])
            elif kind == "ERROR" and state["error"] is None:
                state["error"] = rest.partition(" ")

    argv = InstanceWaydroid(runner, instance_id).argv("send", "--progress", "--", *paths)
    try:
        runner.run(argv, timeout=7 * 24 * 3600, on_output=output)
    except CommandError as error:
        code, _, name = state["error"] or ("failed", "", state["name"])
        if code == "off":
            raise RuntimeError(tr("Android is off. Start a session to send files.")) from error
        if code == "folder":
            message = tr("Only files can be sent: {0} is a folder.").format(name)
            raise RuntimeError(message) from error
        raise RuntimeError(tr("Could not send {0}.").format(name or tr("the files"))) from error


def microphone(runner, instance_id, *choice):
    """The Linux microphone one environment records from: the options and which one is
    chosen. With a name ("" for the Linux default), choose it first."""
    environment = InstanceWaydroid(runner, instance_id)
    arguments = ("set", *filter(None, choice)) if choice else ("list",)
    output = environment.command("microphone", *arguments, timeout=30)
    try:
        answer = json.loads(output.strip().splitlines()[-1])
        chosen, listed = answer["microphone"], answer["sources"]
        options = [{"name": "", "label": tr("Linux default")}]
        options += [{"name": item["name"], "label": item["description"]} for item in listed]
        if any(not isinstance(value, str) for row in options for value in row.values()):
            raise ValueError
        if not isinstance(chosen, str):
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise RuntimeError(tr("Android did not confirm the microphone.")) from error
    if chosen not in [row["name"] for row in options]:
        # Kept: it may only be unplugged, and Linux uses its default meanwhile.
        options.append({"name": chosen, "label": tr("{0} (disconnected)").format(chosen)})
    return {
        "options": options,
        "index": [row["name"] for row in options].index(chosen),
        "listed": answer.get("listed") is not False,
    }


def camera(runner, instance_id, *choice):
    """The Linux webcam one environment sees from its next start: the options, which one
    is chosen and a note on this Android's limits. With an id, choose it first."""
    environment = InstanceWaydroid(runner, instance_id)
    output = environment.command("camera", *(("set", *choice) if choice else ("list",)), timeout=30)
    try:
        answer = json.loads(output.strip().splitlines()[-1])
        chosen, release = answer["camera"], answer["release"]
        needed = {"20.0": "MJPEG", "18.1": "YUYV"}[release]
        options = [
            {
                "name": "auto",
                "label": tr("Automatic (the first webcam that works)"),
                "enabled": True,
            },
            {"name": "none", "label": tr("No camera"), "enabled": True},
        ]
        for item in answer["cameras"]:
            if not all(
                isinstance(value, str) for value in (item["id"], item["name"], *item["formats"])
            ):
                raise ValueError
            # Only the formats an Android reads; a test driver may offer dozens.
            formats = ", ".join(
                shown
                for code, shown in (("MJPG", "MJPEG"), ("YUYV", "YUYV"))
                if code in item["formats"]
            )
            label = (
                f"{item['name']} — {formats}"
                if item["compatible"] is True
                else tr("{0} — does not work in this Android (it needs {1})").format(
                    item["name"], needed
                )
            )
            options.append(
                {"name": item["id"], "label": label, "enabled": item["compatible"] is True}
            )
        if not isinstance(chosen, str) or any(
            not isinstance(row["name"], str) or not isinstance(row["label"], str) for row in options
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise RuntimeError(tr("Android did not confirm the webcam.")) from error
    if chosen not in [row["name"] for row in options]:
        # Kept: it may only be unplugged; Android then starts without a camera.
        shown = chosen.removeprefix("name:").rsplit("/", 1)[-1]
        options.append(
            {"name": chosen, "label": tr("{0} (disconnected)").format(shown), "enabled": True}
        )
    note = (
        tr("Android 11: some camera apps, such as the Camera app, may not work.")
        if release == "18.1"
        else ""
    )
    return {
        "options": options,
        "index": [row["name"] for row in options].index(chosen),
        "note": note,
    }


def keyboard_wanted(instance_id):
    """The container's virtual keyboard choice, saved by the Android runtime; off by default."""
    home = Path(os.environ.get("HOME") or Path.home())
    path = home / ".local/share/multipak/android-instances" / instance_id / "input-preferences.json"
    try:
        return json.loads(path.read_text()).get("keyboard") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_keyboard(runner, instance_id, enabled):
    """Show or hide the virtual keyboard in one container; applied at once when it runs."""
    output = InstanceWaydroid(runner, instance_id).command("keyboard", "on" if enabled else "off")
    try:
        if json.loads(output.strip().splitlines()[-1])["keyboard"] is not bool(enabled):
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise RuntimeError(tr("Android did not confirm the keyboard preference.")) from error


def set_keep_ready(instance_id, enabled, label=""):
    """Start the Android session in the background whenever the desktop starts.

    The root service already starts with the computer; Android freezes itself while
    idle, so opening an application only thaws it. Uses the Manager's session unit."""
    if not valid_id(instance_id):
        raise ValueError(tr("Invalid Android environment ID."))
    path = keep_ready_path(instance_id)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    from .desktop import quote_exec

    command = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit=multipak-android-session-{instance_id}",
        "--service-type=exec",
        # No value: systemd-run copies the variable from the desktop's environment.
        "--setenv=WAYLAND_DISPLAY",
        "--",
        CLI,
        "--instance",
        instance_id,
        "session",
        "start",
    ]
    name = re.sub(r"[\x00-\x1f\x7f]", "", label) or instance_id
    text = (
        "[Desktop Entry]\nType=Application\n"
        f"Name=MultiPak Android: {name}\n"
        f"Exec={' '.join(quote_exec(word) for word in command)}\n"
        "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def discover_with_status(runner):
    """Read each owned instance's state without preparing or starting Android."""
    labels = {
        "running": tr("Running"),
        "frozen": tr("Suspended"),
        "stopped": tr("Stopped"),
        "uninitialized": tr("Not prepared"),
        "unavailable": tr("Status unavailable"),
    }
    result = []
    for entry in discover(runner):
        state, explanation = "uninitialized", ""
        if entry.get("initialized"):
            state = "unavailable"
            try:
                output = runner.run([CLI, "--instance", entry["id"], "status"], timeout=5)
                container = re.search(r"^Container:\s*(\S+)", output, re.M)
                session = re.search(r"^Session:\s*(\S+)", output, re.M)
                if container and container[1] in ("RUNNING", "FROZEN", "STOPPED"):
                    state = {"RUNNING": "running", "FROZEN": "frozen", "STOPPED": "stopped"}[
                        container[1]
                    ]
                elif session and session[1] == "STOPPED":
                    state = "stopped"
                else:
                    explanation = tr(
                        "Android returned an unrecognized state. Refresh to try again."
                    )
            except (OSError, RuntimeError, TimeoutError) as error:
                explanation = str(error)
        result.append(
            {
                **entry,
                "state": state,
                "state_label": labels[state],
                "state_explanation": explanation,
                "can_stop": state in ("running", "frozen"),
                "keep_ready": keep_ready_path(entry["id"]).is_file(),
                "keyboard": keyboard_wanted(entry["id"]),
            }
        )
    return result


def discover_system(runner):
    """Inspect the personal environment without starting or initializing Android."""
    from .android import CONFIG, ROOTFS

    if not host.which("waydroid"):
        return None
    try:
        runner.run(["test", "-s", CONFIG], timeout=5)
        runner.run(["test", "-d", ROOTFS], timeout=5)
    except (OSError, RuntimeError, TimeoutError):
        return None
    sdk, release = 0, ""
    try:
        props = runner.run(["cat", ROOTFS + "/system/build.prop"], timeout=5)
        match = re.search(r"^ro.build.version.sdk=(\d+)$", props, re.M)
        sdk = int(match[1]) if match else 0
        match = re.search(r"^ro.build.version.release=(.+)$", props, re.M)
        release = match[1].strip() if match else ""
    except (OSError, RuntimeError, TimeoutError):
        pass
    return {
        "id": "system-waydroid",
        "label": tr("Waydroid (existing environment)"),
        "profile": "",
        "personal": True,
        "sdk": sdk,
        "version": "Android " + release if release else tr("Existing Android"),
    }


def identifier():
    return "m" + uuid.uuid4().hex[:10]


def ensure_runtime(runner):
    from .distrobox_setup import WaydroidMissing, outdated_waydroid

    if not host.which("waydroid"):
        raise WaydroidMissing(
            tr("Install Waydroid on your system before preparing an Android environment.")
        )
    if message := outdated_waydroid(runner):
        raise WaydroidMissing(message)
    if host.which("multipak-waydroid"):
        version = runner.run([CLI, "--runtime-version"], timeout=10).strip()
        from .android_instances_helper import RUNTIME_SHA256

        digest = runner.run([CLI, "--runtime-digest"], check=False, timeout=10).strip()
        if version == "1.1.0-waydroid-1.6.3" and digest == RUNTIME_SHA256:
            entries = json.loads(runner.run([CLI, "list", "--json"], timeout=10))
            if all(e.get("data_layout", 1) == 2 for e in entries if e.get("owner") == os.getuid()):
                return
    from .privileged import call

    output = call(runner, "android", "deploy", timeout=600)
    if "MULTIPAK_ANDROID_RUNTIME_READY" not in output.splitlines():
        raise RuntimeError(tr("The named Android runtime was not prepared."))


def create(runner, profile, label, *, instance_id=None, progress=lambda *_: None):
    if profile not in {p[0] for p in PROFILES}:
        raise ValueError(tr("Select an Android version."))
    if not valid_label(label):
        raise ValueError(tr("Enter a valid Android environment name."))
    instance_id = instance_id or identifier()
    if not valid_id(instance_id):
        raise ValueError(tr("Invalid Android environment ID."))
    ensure_runtime(runner)
    from .privileged import call

    progress(-1, tr("Preparing the Android environment…"))
    with (
        TemporaryDirectory(
            prefix="multipak-android-create-", dir=host.temporary_directory()
        ) as temp,
    ):
        watch = Path(temp) / "running"
        watch.touch(mode=0o600)
        output = call(
            runner,
            "android",
            "create",
            instance_id,
            label,
            profile,
            str(watch),
            timeout=2400,
            on_output=AndroidProgress(progress).feed,
            cancel_file=watch,
        )
    if "MULTIPAK_ANDROID_CREATED" not in output.splitlines():
        raise RuntimeError(tr("Android environment creation was not confirmed."))
    return instance_id


def remove(runner, instance_id, *, delete_data=False, only_empty=False):
    if not valid_id(instance_id):
        raise ValueError(tr("Invalid Android environment ID."))
    from .privileged import call

    output = call(
        runner,
        "android",
        "remove-empty" if only_empty else "remove",
        instance_id,
        "delete-data" if delete_data else "keep-data",
        timeout=180,
    )
    if "MULTIPAK_ANDROID_REMOVED" not in output.splitlines():
        raise RuntimeError(tr("Android environment removal was not confirmed."))
    data, _ = host.directories()
    for shortcut in (data / "applications").glob(f"multipak-waydroid.{instance_id}.*.desktop"):
        shortcut.unlink()
    menu = data / "kio/servicemenus" / f"multipak-waydroid.{instance_id}.send.desktop"
    menu.unlink(missing_ok=True)
    marker = f"{CLI} --instance {instance_id} send "
    for script in (data / "nautilus/scripts").glob("*/*"):
        try:
            if marker in script.read_text(errors="replace"):
                script.unlink()
        except OSError:
            pass
    keep_ready_path(instance_id).unlink(missing_ok=True)


def upgrade(runner, instance_id, *, progress=lambda *_: None):
    from .privileged import call

    entry = next((e for e in discover(runner) if e["id"] == instance_id), None)
    if not entry or not entry.get("initialized"):
        raise ValueError(tr("Select a prepared MultiPak Android container to update."))
    android = InstanceWaydroid(runner, instance_id)
    status = android.command("status")
    if not re.search(r"^Session:\s+(RUNNING|STOPPED)\s*$", status, re.M):
        raise RuntimeError(tr("Could not determine the Android container's state."))
    active = bool(re.search(r"^Session:\s+RUNNING\s*$", status, re.M))
    previous = runner.defer_cancellation
    runner.defer_cancellation = True
    try:
        progress(-1, tr("Updating the Android container…"))
        try:
            output = call(
                runner,
                "android-upgrade",
                instance_id,
                timeout=2500,
                on_output=AndroidProgress(progress).feed,
            )
            records = [
                line.removeprefix("MULTIPAK_ANDROID_UPGRADE_RESULT ")
                for line in output.splitlines()
                if line.startswith("MULTIPAK_ANDROID_UPGRADE_RESULT ")
            ]
            if len(records) != 1:
                raise ValueError("Missing update confirmation")
            result = json.loads(records[0])
            if type(result.get("updated")) is not bool or not isinstance(result.get("bridge"), str):
                raise ValueError("Invalid update confirmation")
        except Exception:
            if active:
                try:
                    android.ready(progress, initialize=False)
                except Exception as recovery_error:
                    runner.log(tr("Could not restart Android: {0}").format(recovery_error))
            raise
        if active:
            progress(-1, tr("Checking Android after the update…"))
            if not android.ready(progress, initialize=False):
                raise RuntimeError(tr("The images were updated, but Android could not restart."))
            if android.command("prop", "get", "ro.build.version.sdk").strip() != str(entry["sdk"]):
                raise RuntimeError(tr("Android did not confirm the selected system version."))
            bridge = result["bridge"]
            if (
                bridge not in ("", "0")
                and android.command("prop", "get", "ro.dalvik.vm.native.bridge").strip() != bridge
            ):
                raise RuntimeError(
                    tr("Android did not confirm the ARM translation layer after updating.")
                )
        progress(
            100,
            tr("Android container updated.")
            if result["updated"]
            else tr("The Android container is already up to date."),
        )
        return result["updated"]
    finally:
        runner.defer_cancellation = previous


class InstanceWaydroid(Waydroid):
    def __init__(self, runner, instance_id):
        if not valid_id(instance_id):
            raise ValueError(tr("Invalid Android environment ID."))
        super().__init__(runner)
        self.instance_id = instance_id
        self.config = str(BASE / instance_id / "waydroid.cfg")
        self.rootfs = str(BASE / instance_id / "rootfs")
        self.session_unit = "multipak-android-session-" + instance_id

    def argv(self, *args):
        return [CLI, "--instance", self.instance_id, *args]

    def command(self, *args, timeout=60):
        from .android import FAILURE

        output = self.runner.run(self.argv(*args), timeout=timeout)
        if FAILURE.search(output):
            raise RuntimeError(output[-4000:])
        return output

    def privileged(self, *args, timeout=1800):
        from .android import FAILURE, WAYDROID
        from .privileged import call

        if args and args[0] == WAYDROID:
            output = call(self.runner, "waydroid", self.instance_id, *args[1:], timeout=timeout)
            if FAILURE.search(output):
                raise RuntimeError(output[-4000:])
            return output
        if len(args) == 3 and args[0] == "/usr/bin/systemctl" and args[1] in ("start", "stop"):
            if args[2] != f"multipak-waydroid@{self.instance_id}.service":
                raise ValueError("Unexpected Android service")
            return call(self.runner, "service", self.instance_id, args[1], timeout=timeout)
        raise ValueError("Unsupported privileged Android operation")

    def ready(self, progress=lambda *_: None, *, initialize=True):
        if initialize:
            ensure_runtime(self.runner)
        if not host.which("multipak-waydroid"):
            if not initialize:
                return False
            raise RuntimeError(tr("The named Android runtime is not installed."))
        if not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError(tr("Android applications require a Wayland desktop session."))
        info = next((e for e in discover(self.runner) if e["id"] == self.instance_id), None)
        if not info:
            if not initialize:
                return False
            raise RuntimeError(tr("The Android environment no longer exists."))
        if not info.get("initialized"):
            if not initialize:
                return False
            create(
                self.runner,
                info["profile"],
                info["label"],
                instance_id=self.instance_id,
                progress=progress,
            )
        progress(-1, tr("Starting the Android environment…"))
        service = f"multipak-waydroid@{self.instance_id}.service"
        if self.runner.run(["systemctl", "is-active", service], check=False).strip() != "active":
            self.privileged("/usr/bin/systemctl", "start", service, timeout=120)
        status = self.command("status")
        if not re.search(r"Session:\s+RUNNING", status):
            state = self.runner.run(
                ["systemctl", "--user", "is-active", self.session_unit], check=False
            ).strip()
            if state in ("failed", "deactivating"):
                self.runner.run(["systemctl", "--user", "stop", self.session_unit], check=False)
                self.runner.run(
                    ["systemctl", "--user", "reset-failed", self.session_unit], check=False
                )
            if state not in ("active", "activating"):
                self.runner.run(
                    [
                        "systemd-run",
                        "--user",
                        "--collect",
                        f"--unit={self.session_unit}",
                        "--service-type=exec",
                        f"--setenv=WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}",
                        "--",
                        *self.argv("session", "start"),
                    ],
                    timeout=30,
                )
        with AndroidStartupOutput(self.runner, progress):
            deadline = time.monotonic() + 180
            error = ""
            while time.monotonic() < deadline:
                try:
                    if re.search(r"Session:\s+RUNNING", self.command("status", timeout=15)):
                        self.apps()
                        return True
                except (RuntimeError, TimeoutError) as exc:
                    error = str(exc)
                unit_state = self.runner.run(
                    ["systemctl", "--user", "is-active", self.session_unit], check=False
                ).strip()
                if unit_state not in ("active", "activating"):
                    details = self.runner.run(
                        ["journalctl", "--user", "-u", self.session_unit, "-n", "30", "--no-pager"],
                        check=False,
                    )
                    raise RuntimeError(
                        tr("Android session stopped before it became ready: {0}").format(
                            details[-4000:]
                        )
                    )
                if self.runner.cancelled.wait(1):
                    raise Cancelled
            raise RuntimeError(tr("Android did not become ready: {0}").format(error))

    def stop(self):
        self.command("session", "stop")
        self.privileged(
            "/usr/bin/systemctl",
            "stop",
            f"multipak-waydroid@{self.instance_id}.service",
            timeout=120,
        )

    def staging_directory(self):
        return BASE / self.instance_id / "exchange/apks"

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from multipak import android_instances

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"
PACTL = """Source #57
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
\tDescription: Monitor of Áudio interno Estéreo analógico
\tMonitor of Sink: alsa_output.pci-0000_00_1f.3.analog-stereo
\tProperties:
\t\tdevice.class = "monitor"
\t\tnode.name = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"

Source #58
\tState: SUSPENDED
\tName: alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo
\tDescription: Webcam C920 Estéreo analógico
\tMonitor of Sink: n/a
\tProperties:
\t\tName: not a field
\t\tdevice.description = "Other"

Source #59
\tName: bluez_input.AA:BB:CC:DD:EE:FF.0
\tMonitor of Sink: n/a

Source #60
\tName: bad name; default-server = evil
\tDescription: Refused
\tMonitor of Sink: n/a
"""


@pytest.fixture
def microphone(monkeypatch, tmp_path):
    context = ModuleType("multipak_instance")
    context.current = lambda: SimpleNamespace(
        id="androida", work=tmp_path / "instance", user_state=tmp_path / "prefs"
    )
    (tmp_path / "instance").mkdir()
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_microphone"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name",
    [
        "mic\ndefault-server = evil",
        "mic\rx",
        "a b",
        "a;b",
        "a#b",
        "a=b",
        "../mic",
        "mic\0",
        "é",
        "x" * 201,
    ],
)
def test_a_name_that_could_add_settings_is_refused(microphone, tmp_path, name):
    with pytest.raises(ValueError):
        microphone.write(name)
    assert not (tmp_path / "instance/overlay").exists()


def test_the_file_holds_only_the_chosen_microphone(microphone):
    name = "alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo"
    assert microphone.check(name) == name and microphone.check("") == ""
    assert microphone.check("bluez_input.AA:BB:CC:DD:EE:FF.0")
    lines = [line for line in microphone.content(name).decode().splitlines() if line[:1] != "#"]
    assert lines == ["default-source = " + name]
    assert all(line.startswith("#") for line in microphone.content("").decode().splitlines())


def test_the_file_is_rewritten_in_place(microphone, tmp_path):
    conf = tmp_path / "instance/overlay/vendor/etc/pulse/client.conf"
    microphone.write("a_long_microphone_name")
    first = conf.stat()
    assert stat.S_IMODE(first.st_mode) == 0o644
    assert all(stat.S_IMODE(parent.stat().st_mode) == 0o755 for parent in list(conf.parents)[:4])
    microphone.write("mic_b")
    assert conf.read_bytes() == microphone.content("mic_b")
    microphone.write("")
    assert (
        conf.read_bytes() == microphone.content("") and b"default-source" not in conf.read_bytes()
    )
    # Android only sees the file it was started with, so it must stay the same file.
    assert conf.stat().st_ino == first.st_ino


def test_links_in_the_overlay_are_never_followed(microphone, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    pulse = tmp_path / "instance/overlay/vendor/etc/pulse"
    pulse.mkdir(parents=True)
    (pulse / "client.conf").symlink_to(victim)
    with pytest.raises(OSError):
        microphone.write("mic")
    (pulse / "client.conf").unlink()
    os.link(victim, pulse / "client.conf")
    with pytest.raises(PermissionError):
        microphone.write("mic")
    (pulse / "client.conf").unlink()
    pulse.rmdir()
    (tmp_path / "elsewhere").mkdir()
    pulse.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        microphone.write("mic")
    assert victim.read_text() == "keep" and not list((tmp_path / "elsewhere").iterdir())


def test_microphones_are_listed_without_monitors(microphone, monkeypatch):
    assert microphone.parse_sources(PACTL) == [
        {
            "name": "alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo",
            "description": "Webcam C920 Estéreo analógico",
        },
        {
            "name": "bluez_input.AA:BB:CC:DD:EE:FF.0",
            "description": "bluez_input.AA:BB:CC:DD:EE:FF.0",
        },
    ]
    ran = []

    def run(command, **options):
        ran.append((command, options["env"]["LC_ALL"]))
        return SimpleNamespace(stdout=PACTL.encode())

    monkeypatch.setattr(microphone.subprocess, "run", run)
    assert len(microphone.sources()) == 2
    assert ran == [(("pactl", "list", "sources"), "C.UTF-8")]

    def missing(command, **options):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(microphone.subprocess, "run", missing)
    assert microphone.sources() is None


def test_the_choice_is_saved_and_given_to_the_root_service(
    microphone, monkeypatch, tmp_path, capsys
):
    asked = []
    service = SimpleNamespace(SetMicrophone=asked.append)
    ipc = ModuleType("tools.helpers.ipc")
    ipc.DBusContainerService = lambda: service
    helpers = ModuleType("tools.helpers")
    helpers.ipc = ipc
    tools = ModuleType("tools")
    tools.helpers = helpers
    for name, module in (("tools", tools), ("tools.helpers", helpers), ("tools.helpers.ipc", ipc)):
        monkeypatch.setitem(sys.modules, name, module)
    listed = [{"name": "mic_a", "description": "Mic A"}]
    monkeypatch.setattr(microphone, "sources", lambda: listed)
    preferences = tmp_path / "prefs/input-preferences.json"
    preferences.parent.mkdir()
    preferences.write_text(json.dumps({"keyboard": False}))

    microphone.run(SimpleNamespace(mode="list", NAME=None))
    assert json.loads(capsys.readouterr().out) == {
        "microphone": "",
        "sources": listed,
        "listed": True,
    }
    assert asked == []

    microphone.run(SimpleNamespace(mode="set", NAME="mic_b"))
    assert json.loads(capsys.readouterr().out)["microphone"] == "mic_b"
    assert asked == ["mic_b"] and microphone.chosen() == "mic_b"
    assert json.loads(preferences.read_text()) == {"keyboard": False, "microphone": "mic_b"}

    with pytest.raises(ValueError):
        microphone.run(SimpleNamespace(mode="set", NAME="mic\nx"))
    assert microphone.chosen() == "mic_b" and asked == ["mic_b"]

    # A service that is not running: the session gives it the choice when Android starts.
    def stopped(name):
        raise RuntimeError("not running")

    service.SetMicrophone = stopped
    monkeypatch.setattr(microphone, "sources", lambda: None)
    microphone.run(SimpleNamespace(mode="set", NAME=None))
    assert json.loads(capsys.readouterr().out) == {"microphone": "", "sources": [], "listed": False}
    assert microphone.chosen() == ""

    preferences.write_text(json.dumps({"microphone": "bad name"}))
    assert microphone.chosen() == ""


def test_the_root_service_writes_the_file_before_mounting_and_on_request():
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    start = source[source.index("def do_start(") :].split("\ndef ", 1)[0]
    assert start.index('multipak_microphone.write(session.get("microphone", ""))') < start.index(
        "helpers.images.mount_rootfs("
    )
    method = source[source.index("def SetMicrophone(") :].split("\n    @", 1)[0]
    assert method.index("self._authorize(sender, conn)") < method.index(
        "multipak_microphone.write(str(name))"
    )
    assert (
        "in_signature='s', out_signature=''"
        in source[: source.index("def SetMicrophone(")].rsplit("@dbus", 1)[1]
    )
    session = (VENDOR / "tools/actions/session_manager.py").read_text()
    assert session.index('session["microphone"] = multipak_microphone.chosen()') < session.index(
        "DBusContainerService().Start(session)"
    )


class Runner:
    def __init__(self, answer):
        self.answer, self.ran = answer, []

    def run(self, argv, timeout):
        self.ran.append(argv)
        return "a warning line\n" + (
            self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        )


def test_the_manager_offers_the_default_the_microphones_and_an_unplugged_choice():
    sources = [{"name": "mic_a", "description": "Mic A"}, {"name": "mic_b", "description": "Mic B"}]
    runner = Runner({"microphone": "mic_b", "sources": sources, "listed": True})
    result = android_instances.microphone(runner, "androida")
    assert runner.ran[-1][-2:] == ["microphone", "list"]
    assert result == {
        "options": [
            {"name": "", "label": "Linux default"},
            {"name": "mic_a", "label": "Mic A"},
            {"name": "mic_b", "label": "Mic B"},
        ],
        "index": 2,
        "listed": True,
    }
    runner.answer = {"microphone": "mic_gone", "sources": sources, "listed": True}
    result = android_instances.microphone(runner, "androida", "mic_gone")
    assert runner.ran[-1][-3:] == ["microphone", "set", "mic_gone"]
    assert result["options"][-1] == {"name": "mic_gone", "label": "mic_gone (disconnected)"}
    assert result["index"] == 3
    runner.answer = {"microphone": "", "sources": [], "listed": False}
    result = android_instances.microphone(runner, "androida", "")
    assert runner.ran[-1][-2:] == ["microphone", "set"]
    assert result == {
        "options": [{"name": "", "label": "Linux default"}],
        "index": 0,
        "listed": False,
    }
    for answer in (
        "not json",
        {"sources": []},
        {"microphone": 1, "sources": []},
        {"microphone": "", "sources": [{"name": "x"}]},
    ):
        runner.answer = answer
        with pytest.raises(RuntimeError, match="microphone"):
            android_instances.microphone(runner, "androida")
    with pytest.raises(ValueError):
        android_instances.microphone(runner, "../x")


def test_the_manager_window_shows_the_choice_and_its_errors(monkeypatch):
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QObject, Signal

    from multipak.kirigami import android_environments as module

    class Worker(QObject):
        succeeded = Signal(object)
        failed = Signal(str, object)
        finished = Signal()

        def __init__(self, _store, operation, parent):
            super().__init__(parent)
            self.operation = operation

        def start(self):
            try:
                self.succeeded.emit(self.operation(SimpleNamespace(runner="runner")))
            except Exception as error:
                self.failed.emit(str(error), None)
            self.finished.emit()

    calls = []

    def microphone(runner, identifier, *choice):
        calls.append((runner, identifier, choice))
        if choice == ("broken",):
            raise RuntimeError("Android did not confirm the microphone.")
        options = [{"name": "", "label": "Linux default"}, {"name": "mic_a", "label": "Mic A"}]
        return {
            "options": options,
            "index": 1 if choice == ("mic_a",) else 0,
            "listed": choice != ("",),
        }

    monkeypatch.setattr(module.environments, "microphone", microphone)
    controller = module.AndroidEnvironments(None, worker_factory=Worker)
    assert controller.microphones == [] and controller.microphoneIndex == -1
    controller.loadMicrophone("androida")
    assert calls[-1] == ("runner", "androida", ())
    assert len(controller.microphones) == 2 and controller.microphoneIndex == 0
    controller.setMicrophone("androida", "mic_a")
    assert calls[-1] == ("runner", "androida", ("mic_a",)) and controller.microphoneIndex == 1
    controller.setMicrophone("androida", "broken")
    assert "confirm" in controller.microphoneError and controller.microphoneIndex == 1
    controller.setMicrophone("androida", "")
    assert calls[-1] == ("runner", "androida", ("",)) and controller.microphoneIndex == 0
    assert "could not be listed" in controller.microphoneError and not controller.microphoneBusy

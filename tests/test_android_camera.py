import importlib.util
import json
import stat
import struct
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from multipak import android_instances

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


@pytest.fixture
def camera(monkeypatch, tmp_path):
    context = ModuleType("multipak_instance")
    context.current = lambda: SimpleNamespace(
        id="androida", release="20.0", work=tmp_path / "instance", user_state=tmp_path / "prefs"
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    for name in ("multipak_input", "multipak_camera"):
        spec = importlib.util.spec_from_file_location(name, VENDOR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return module


def fake_ioctl(caps, formats, card=b"HD Pro Webcam C920"):
    def ioctl(_descriptor, request, buffer):
        if request == 0x80685600:
            struct.pack_into("16s32s32sIII", buffer, 0, b"uvcvideo", card, b"usb-1", 1, *caps)
        elif request == 0xC0405602:
            index, kind = struct.unpack_from("II", buffer)
            if kind != 1 or index >= len(formats):
                raise OSError(22, "Invalid argument")
            code = formats[index]
            buffer[44:48] = code if isinstance(code, bytes) else code.encode()
        else:
            raise OSError(25, "Not a typewriter")

    return ioctl


def test_only_video_capture_nodes_are_webcams(camera, monkeypatch, tmp_path):
    # /dev/null is a character device, but not Video4Linux.
    assert camera.probe("/dev/null") is None
    monkeypatch.setattr(camera, "_video", lambda _descriptor: None)
    capture = (0x84A00001, 0x04200001)
    monkeypatch.setattr(camera.fcntl, "ioctl", fake_ioctl(capture, ["MJPG", "YUYV"]))
    assert camera.probe("/dev/null") == ("HD Pro Webcam C920", ["MJPG", "YUYV"])
    # The metadata node of a USB webcam: the device captures video, this node does not.
    monkeypatch.setattr(camera.fcntl, "ioctl", fake_ioctl((0x84A00001, 0x04A00000), []))
    assert camera.probe("/dev/null") is None
    # A virtual camera with exclusive_caps and nothing to show only offers output.
    monkeypatch.setattr(camera.fcntl, "ioctl", fake_ioctl((0x85208002, 0x05208002), []))
    assert camera.probe("/dev/null") is None
    # A driver without device_caps describes the node in capabilities.
    big_endian = struct.pack("<I", int.from_bytes(b"Y16 ", "little") | 0x80000000)
    monkeypatch.setattr(
        camera.fcntl, "ioctl", fake_ioctl((0x05000001, 0), ["YUYV", big_endian], b"")
    )
    assert camera.probe("/dev/null") == ("/dev/null", ["YUYV", "Y16"])
    (tmp_path / "video0").symlink_to("/dev/null")
    assert camera.probe(str(tmp_path / "video0")) is None
    assert camera.probe(str(tmp_path / "missing")) is None


@pytest.fixture
def webcams(camera, monkeypatch):
    probed = {
        "/dev/video0": ("OBS Virtual Camera", ["YUYV"]),
        "/dev/video2": ("HD Pro Webcam C920", ["MJPG", "YUYV"]),
        "/dev/video10": ("Dummy video device (0x0000)", ["MJPG"]),
    }
    nodes = ["/dev/video10", "/dev/video1", "/dev/video2", "/dev/video0", "/dev/videoX"]
    monkeypatch.setattr(
        camera.glob, "glob", lambda pattern: nodes if pattern == "/dev/video*" else []
    )
    monkeypatch.setattr(camera, "probe", lambda node: probed.get(node))
    monkeypatch.setattr(
        camera,
        "_links",
        lambda: {"/dev/video2": "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920-video-index0"},
    )
    return camera


def test_webcams_are_listed_by_stable_name_and_marked_for_each_android(webcams):
    found = webcams.cameras("20.0")
    assert [item["node"] for item in found] == ["/dev/video0", "/dev/video2", "/dev/video10"]
    assert [item["id"] for item in found] == [
        "name:OBS Virtual Camera",
        "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920-video-index0",
        "name:Dummy video device (0x0000)",
    ]
    assert [item["compatible"] for item in found] == [False, True, True]
    assert [item["compatible"] for item in webcams.cameras("18.1")] == [True, True, False]
    assert found == webcams.cameras()  # This environment's release.


def test_the_choice_finds_its_node_and_only_compares_names(webcams):
    found = webcams.cameras("20.0")
    assert webcams.resolve("auto", found) == "/dev/video2"
    assert webcams.resolve("", found) == "/dev/video2"
    assert webcams.resolve("auto", webcams.cameras("18.1")) == "/dev/video0"
    # A chosen webcam is used even when it is marked as not working there.
    assert webcams.resolve("name:OBS Virtual Camera", found) == "/dev/video0"
    for choice in ("none", "/dev/video2", "/dev/v4l/by-id/other", "name:Gone", "/etc/shadow"):
        assert webcams.resolve(choice, found) is None
    assert webcams.resolve("auto", []) is None


@pytest.mark.parametrize("choice", ["a\nb", "a\rb", "a\0b", "a\x1bb", "a\x7fb", "x" * 201])
def test_a_name_that_could_add_lines_is_refused(camera, choice):
    with pytest.raises(ValueError):
        camera.check(choice)


def test_the_root_service_opens_up_only_the_chosen_node_and_drops_old_entries(
    webcams, monkeypatch, tmp_path
):
    node = tmp_path / "video2"
    node.write_text("")
    node.chmod(0o600)
    monkeypatch.setattr(webcams, "_video", lambda _descriptor: None)
    original = webcams.cameras
    monkeypatch.setattr(
        webcams,
        "cameras",
        lambda: [
            {**item, "node": str(node) if item["node"] == "/dev/video2" else item["node"]}
            for item in original("20.0")
        ],
    )
    nodes = tmp_path / "config_nodes"
    kept = [
        "lxc.mount.entry = /dev/null dev/null none bind,create=file,optional 0 0",
        "lxc.mount.entry = /dev/dri/renderD128 dev/dri/renderD128 none bind,optional 0 0",
    ]
    old = [
        "lxc.mount.entry = /dev/video0 dev/video0 none bind,create=file,optional 0 0",
        "lxc.mount.entry = /dev/video11 dev/video11 none bind,create=file,optional 0 0",
    ]
    nodes.write_text("\n".join([kept[0], *old, kept[1]]) + "\n")
    assert webcams.prepare("auto", str(nodes)) == str(node)
    assert nodes.read_text().splitlines() == kept
    assert stat.S_IMODE(node.stat().st_mode) == 0o666
    node.chmod(0o600)
    assert webcams.prepare("none", str(nodes)) is None
    assert webcams.prepare("name:Gone", str(nodes)) is None
    assert stat.S_IMODE(node.stat().st_mode) == 0o600
    assert webcams.prepare("auto", str(tmp_path / "missing")) == str(node)
    with pytest.raises(ValueError):
        webcams.prepare("a\nb", str(nodes))


def test_the_choice_is_saved_and_listed_for_the_manager(webcams, tmp_path, capsys):
    preferences = tmp_path / "prefs/input-preferences.json"
    preferences.parent.mkdir()
    preferences.write_text(json.dumps({"microphone": "mic_a"}))
    webcams.run(SimpleNamespace(mode="list", ID=None))
    answer = json.loads(capsys.readouterr().out)
    assert answer["camera"] == "auto" and answer["release"] == "20.0"
    assert answer["cameras"][1] == {
        "id": "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920-video-index0",
        "name": "HD Pro Webcam C920",
        "formats": ["MJPG", "YUYV"],
        "compatible": True,
    }
    webcams.run(SimpleNamespace(mode="set", ID="name:OBS Virtual Camera"))
    assert json.loads(capsys.readouterr().out)["camera"] == "name:OBS Virtual Camera"
    assert json.loads(preferences.read_text()) == {
        "microphone": "mic_a",
        "camera": "name:OBS Virtual Camera",
    }
    webcams.run(SimpleNamespace(mode="set", ID="none"))
    assert webcams.chosen() == "none"
    with pytest.raises(ValueError):
        webcams.run(SimpleNamespace(mode="set", ID="a\nb"))
    assert webcams.chosen() == "none"
    webcams.run(SimpleNamespace(mode="set", ID=None))
    assert webcams.chosen() == "auto"
    preferences.write_text(json.dumps({"camera": ["bad"]}))
    assert webcams.chosen() == "auto"


def test_the_choice_reaches_the_container_only_through_the_root_service():
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    start = source[source.index("def do_start(") :].split("\ndef ", 1)[0]
    assert start.index("multipak_camera.prepare(") < start.index(
        "generate_session_lxc_config(args, session, camera)"
    )
    permissions = source[source.index("def set_permissions(") :].split("\ndef ", 1)[0]
    lxc = (VENDOR / "tools/helpers/lxc.py").read_text()
    nodes = lxc[lxc.index("def generate_nodes_lxc_config(") :].split("\ndef ", 1)[0]
    assert "/dev/video" not in permissions and 'glob("/dev/video' not in nodes
    session = (VENDOR / "tools/actions/session_manager.py").read_text()
    assert session.index('session["camera"] = multipak_camera.chosen()') < session.index(
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


CAMERAS = [
    {
        "id": "/dev/v4l/by-id/usb-C920-video-index0",
        "name": "C920",
        "formats": ["MJPG", "YUYV"],
        "compatible": True,
    },
    {
        "id": "name:OBS Virtual Camera",
        "name": "OBS Virtual Camera",
        "formats": ["YUYV"],
        "compatible": False,
    },
]


def test_the_manager_offers_automatic_none_the_webcams_and_an_unplugged_choice():
    runner = Runner({"camera": "auto", "release": "20.0", "cameras": CAMERAS})
    result = android_instances.camera(runner, "androida")
    assert runner.ran[-1][-2:] == ["camera", "list"]
    assert result == {
        "options": [
            {"name": "auto", "label": "Automatic (the first webcam that works)", "enabled": True},
            {"name": "none", "label": "No camera", "enabled": True},
            {"name": CAMERAS[0]["id"], "label": "C920 — MJPEG, YUYV", "enabled": True},
            {
                "name": "name:OBS Virtual Camera",
                "label": "OBS Virtual Camera — does not work in this Android (it needs MJPEG)",
                "enabled": False,
            },
        ],
        "index": 0,
        "note": "",
    }
    runner.answer = {
        "camera": "/dev/v4l/by-id/usb-Gone-video-index0",
        "release": "18.1",
        "cameras": CAMERAS,
    }
    result = android_instances.camera(runner, "androida", "/dev/v4l/by-id/usb-Gone-video-index0")
    assert runner.ran[-1][-3:] == ["camera", "set", "/dev/v4l/by-id/usb-Gone-video-index0"]
    assert result["options"][-1] == {
        "name": "/dev/v4l/by-id/usb-Gone-video-index0",
        "label": "usb-Gone-video-index0 (disconnected)",
        "enabled": True,
    }
    assert result["index"] == 4 and "Android 11" in result["note"]
    assert result["options"][3]["label"].endswith("(it needs YUYV)")
    for answer in (
        "not json",
        {"camera": "auto", "cameras": []},
        {"camera": "auto", "release": "9", "cameras": []},
        {"camera": 1, "release": "20.0", "cameras": []},
        {"camera": "auto", "release": "20.0", "cameras": [{"id": "x"}]},
        {"camera": "auto", "release": "20.0", "cameras": [{**CAMERAS[0], "name": 3}]},
    ):
        runner.answer = answer
        with pytest.raises(RuntimeError, match="webcam"):
            android_instances.camera(runner, "androida")
    with pytest.raises(ValueError):
        android_instances.camera(runner, "../x")


def test_the_manager_window_shows_the_camera_and_restarts_android(monkeypatch):
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QObject, Signal

    from multipak.kirigami import android_environments as module

    class Worker(QObject):
        succeeded = Signal(object)
        failed = Signal(str, object)
        abandoned = Signal()
        progress = Signal(int, str)
        log = Signal(str)
        finished = Signal()

        def __init__(self, _store, operation, parent):
            super().__init__(parent)
            self.operation = operation
            self.cancel_requested = SimpleNamespace(set=lambda: None)

        def start(self):
            engine = SimpleNamespace(runner="runner", progress=lambda *_: None)
            try:
                self.succeeded.emit(self.operation(engine))
            except Exception as error:
                self.failed.emit(str(error), None)
            self.finished.emit()

    calls = []

    def camera(runner, identifier, *choice):
        calls.append((runner, identifier, choice))
        if choice == ("broken",):
            raise RuntimeError("Android did not confirm the webcam.")
        options = [
            {"name": "auto", "label": "Automatic", "enabled": True},
            {"name": "none", "label": "No camera", "enabled": True},
        ]
        return {"options": options, "index": 1 if choice == ("none",) else 0, "note": "Note"}

    monkeypatch.setattr(module.environments, "camera", camera)
    controller = module.AndroidEnvironments(None, worker_factory=Worker)
    assert controller.cameras == [] and controller.cameraIndex == -1
    controller.loadCamera("androida")
    assert calls[-1] == ("runner", "androida", ()) and controller.cameraNote == "Note"
    controller.setCamera("androida", "none")
    assert calls[-1] == ("runner", "androida", ("none",)) and controller.cameraIndex == 1
    controller.setCamera("androida", "broken")
    assert "confirm" in controller.cameraError and controller.cameraIndex == 1
    assert not controller.cameraBusy

    done = []

    class Android:
        def __init__(self, runner, identifier):
            self.identifier = identifier

        def stop(self):
            done.append(("stop", self.identifier))

        def ready(self, _progress):
            done.append(("ready", self.identifier))

    monkeypatch.setattr(module.environments, "InstanceWaydroid", Android)
    monkeypatch.setattr(
        module.environments,
        "discover_with_status",
        lambda _runner: [{"id": "androida", "label": "A"}],
    )
    controller._rows = [{"id": "androida", "label": "A"}]
    controller.actOn("androida", "restart")
    assert done == [("stop", "androida"), ("ready", "androida")]
    assert not controller.hasError

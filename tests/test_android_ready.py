import configparser
import shlex
from pathlib import Path

import pytest

from multipak import android_instances as instances

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


def entry(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser["Desktop Entry"]


def test_keep_ready_starts_the_managers_session_unit_at_login(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    instances.set_keep_ready("androida", True, "Meu Android")
    path = tmp_path / ".config/autostart/multipak-android-androida-ready.desktop"
    desktop = entry(path)
    assert desktop["NoDisplay"] == "true"
    assert desktop["Name"] == "MultiPak Android: Meu Android"
    # Exec uses desktop-entry quoting: undo the string escape, then split like a shell.
    words = shlex.split(desktop["Exec"].replace("\\\\", "\\"))
    assert words[:4] == ["systemd-run", "--user", "--collect", "--unit=multipak-android-session-androida"]
    assert "--setenv=WAYLAND_DISPLAY" in words
    assert words[words.index("--") + 1 :] == [
        instances.CLI, "--instance", "androida", "session", "start"
    ]
    assert instances.keep_ready_path("androida") == path
    instances.set_keep_ready("androida", False)
    assert not path.exists()
    instances.set_keep_ready("androida", False)  # Already off: nothing to remove.


def test_keep_ready_refuses_invalid_environments(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError):
        instances.set_keep_ready("../x", True)
    assert not (tmp_path / ".config").exists()


def test_status_reports_keep_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        instances, "discover", lambda _: [{"id": "androida", "label": "A", "initialized": False}]
    )
    assert instances.discover_with_status(None)[0]["keep_ready"] is False
    instances.set_keep_ready("androida", True)
    assert instances.discover_with_status(None)[0]["keep_ready"] is True


def test_lighter_android_properties_and_no_tracing_daemons():
    source = (VENDOR / "tools/helpers/lxc.py").read_text()
    for prop in (
        "debug.sf.nobootanimation=1",
        "config.disable_networktime=true",
        "config.disable_otadexopt=true",
    ):
        assert f'props.append("{prop}")' in source
    # Camera and text selection stay available to apps.
    assert "disable_cameraservice" not in source
    assert "disable_systemtextclassifier" not in source
    assert (VENDOR / "data/configs/empty.rc").read_bytes() == b""
    assert '"system/etc/init/perfetto.rc", options="bind,optional,ro 0 0"' in source

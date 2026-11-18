import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


def load(monkeypatch, release="20.0"):
    context = SimpleNamespace(current=lambda: SimpleNamespace(id="androida", release=release))
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    spec = importlib.util.spec_from_file_location("multipak_desktop_test", VENDOR / "multipak_desktop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Platform:
    def __init__(self, **props):
        self.props = props
        self.writes = []

    def getprop(self, key, default):
        return self.props.get(key, default)

    def setprop(self, key, value):
        self.writes.append(key)
        self.props[key] = value


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "pt_BR.UTF-8"}, "pt-BR"),
        ({"LANG": "en_US.UTF-8", "LC_ALL": "de_DE.UTF-8"}, "de-DE"),
        ({"LANG": "sr_RS.UTF-8@latin"}, "sr-RS"),
        ({"LANG": "eo"}, "eo"),
        ({"LANG": "C.UTF-8"}, None),
        ({"LANG": "pt_BR;evil"}, None),
        ({}, None),
    ],
)
def test_desktop_locale_becomes_an_android_language_tag(monkeypatch, environ, expected):
    assert load(monkeypatch).desktop_locale(environ) == expected


def test_desktop_timezone_comes_from_tz_or_localtime(monkeypatch, tmp_path):
    desktop = load(monkeypatch)
    zone = tmp_path / "usr/share/zoneinfo/America/Sao_Paulo"
    zone.parent.mkdir(parents=True)
    zone.touch()
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zone)
    assert desktop.desktop_timezone(localtime, {}) == "America/Sao_Paulo"
    assert desktop.desktop_timezone(localtime, {"TZ": ":Europe/Lisbon"}) == "Europe/Lisbon"
    assert desktop.desktop_timezone(localtime, {"TZ": "../../etc/passwd"}) is None
    assert desktop.desktop_timezone(tmp_path / "missing", {}) is None


def test_sync_writes_only_what_differs(monkeypatch):
    desktop = load(monkeypatch)
    monkeypatch.setattr(desktop, "desktop_locale", lambda: "pt-BR")
    monkeypatch.setattr(desktop, "desktop_timezone", lambda: "America/Sao_Paulo")
    platform = Platform(**{"persist.sys.timezone": "America/Sao_Paulo"})
    assert desktop.sync(platform) == ["persist.sys.locale"]
    assert platform.props["persist.sys.locale"] == "pt-BR"
    assert desktop.sync(platform) == [] and platform.writes == ["persist.sys.locale"]


def test_sync_leaves_android_alone_when_the_desktop_value_is_unknown(monkeypatch):
    desktop = load(monkeypatch)
    monkeypatch.setattr(desktop, "desktop_locale", lambda: None)
    monkeypatch.setattr(desktop, "desktop_timezone", lambda: None)
    platform = Platform()
    assert desktop.sync(platform) == [] and platform.writes == []


@pytest.mark.parametrize(("release", "image"), [("20.0", True), ("18.1", False)])
def test_diagnose_reports_integrations_without_starting_android(monkeypatch, release, image):
    desktop = load(monkeypatch, release)
    bus = SimpleNamespace(name_has_owner=lambda name: name == "org.freedesktop.Notifications")
    result = desktop.diagnose(bus, None)
    assert result["notifications_image"] is image
    assert result["notifications_server"] is True
    assert result["running"] is False and "android_locale" not in result
    running = desktop.diagnose(None, Platform(**{"persist.sys.locale": "en-US"}))
    assert running["notifications_server"] is False
    assert running["android_locale"] == "en-US" and running["android_timezone"] == ""


def test_gbinder_version_check(monkeypatch):
    desktop = load(monkeypatch)
    assert desktop._version_at_least("1.3.0", (1, 3))
    assert not desktop._version_at_least("1.2.9", (1, 3))
    assert not desktop._version_at_least(None, (1, 3))


def test_shortcuts_and_notifications_name_the_instance_desktop_entry():
    users = (VENDOR / "tools/services/user_manager.py").read_text()
    assert '"StartupWMClass", "waydroid." + packageName' in users
    assert "multipak_desktop.sync(platformService)" in users
    notifications = (VENDOR / "tools/services/notification_manager.py").read_text()
    assert 'f"waydroid.{package_name}"' not in notifications
    assert "instance().desktop_name(str(package_name)).removesuffix" in notifications

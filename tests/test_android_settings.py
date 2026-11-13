import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from multipak import android_settings as settings

VENDOR = Path(__file__).resolve().parents[1] / "vendor/waydroid"


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    context = SimpleNamespace(
        current=lambda: SimpleNamespace(
            user_state=tmp_path,
            exchange=tmp_path / "exchange",
            owner=1000,
            home=str(tmp_path),
            id="androida",
        )
    )
    monkeypatch.setitem(sys.modules, "multipak_instance", context)
    spec = importlib.util.spec_from_file_location(
        "multipak_input_test", VENDOR / "multipak_input.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


class Platform:
    def __init__(self):
        self.touch = "org.other.app"
        self.keyboard = "1"
        self.policy = "immersive.full=*"
        self.secure = {}

    def getprop(self, key, default):
        assert key == "persist.waydroid.fake_touch"
        return self.touch

    def setprop(self, key, value):
        assert key == "persist.waydroid.fake_touch"
        self.touch = value

    def settingsGetString(self, namespace, key):
        if (namespace, key) == (2, "policy_control"):
            return self.policy
        if namespace == 0 and key != "show_ime_with_hard_keyboard":
            return self.secure.get(key, "")
        assert (namespace, key) == (0, "show_ime_with_hard_keyboard")
        return self.keyboard

    def settingsPutString(self, namespace, key, value):
        if (namespace, key) == (2, "policy_control"):
            self.policy = value
            return
        if namespace == 0 and key != "show_ime_with_hard_keyboard":
            self.secure[key] = value
            return
        assert (namespace, key) == (0, "show_ime_with_hard_keyboard")
        self.keyboard = value


def test_keyboard_defaults_off_once_then_respects_container_changes(inputs, tmp_path):
    platform = Platform()
    inputs.initialize_keyboard(platform)
    assert platform.keyboard == "0"
    assert json.loads((tmp_path / "input-preferences.json").read_text()) == {"keyboard": False}
    platform.keyboard = "1"
    inputs.initialize_keyboard(platform)
    assert platform.keyboard == "1"


def test_touch_changes_preserve_other_apps_and_keyboard_is_container_wide(inputs):
    platform = Platform()
    result = inputs.apply(platform, "org.example.game", True, False)
    assert result == {
        "touch": True,
        "keyboard": False,
        "navigation": False,
        "arrows": False,
        "touch_changed": True,
    }
    assert platform.touch == "org.other.app,org.example.game"
    assert inputs.read(platform, "org.other.app")["keyboard"] is False
    inputs.apply(platform, "org.example.game", False, True)
    assert platform.touch == "org.other.app"


def test_property_limit_and_wildcards_do_not_destroy_existing_rules(inputs):
    assert inputs.touch_value("org.example.*", "org.example.game", True) == "org.example.*"
    with pytest.raises(ValueError, match="shared"):
        inputs.touch_value("org.example.*,org.other.app", "org.example.game", False)
    with pytest.raises(ValueError, match="limit"):
        inputs.touch_value("a" * 90, "org.example.game", True)
    with pytest.raises(ValueError, match="package"):
        inputs.touch_value("", "org.example.game;evil", True)


def test_companion_is_owner_only_and_uses_explicit_named_instance(inputs, monkeypatch, tmp_path):
    executable = tmp_path / ".local/share/multipak-runtime/bin/multipak-android-settings"
    executable.parent.mkdir(parents=True)
    executable.touch()
    calls = []
    monkeypatch.setattr(inputs.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    monkeypatch.setattr(inputs.os, "getuid", lambda: 1000)
    monkeypatch.delenv("MULTIPAK_NO_ANDROID_SETTINGS", raising=False)
    inputs.companion("org.example.game")
    assert calls[0][0] == [
        str(executable),
        "--instance",
        "androida",
        "--package",
        "org.example.game",
    ]
    assert calls[0][1]["start_new_session"] is True
    monkeypatch.setattr(inputs.os, "getuid", lambda: 1001)
    inputs.companion("org.example.game")
    assert len(calls) == 1


@pytest.mark.parametrize("identifier", ["", "system-waydroid", "../waydroid", "personal"])
def test_personal_or_unknown_environments_are_rejected_before_commands(identifier, monkeypatch):
    monkeypatch.setattr(settings, "discover", lambda _: [])
    runner = SimpleNamespace(run=lambda *_a, **_kw: pytest.fail("unexpected command"))
    with pytest.raises(ValueError):
        settings.configure(runner, identifier, "org.example.game", touch=True, keyboard=False)
    with pytest.raises(ValueError):
        settings.reopen(runner, identifier, "org.example.game", lambda *_: None)


def test_configuration_routes_only_to_selected_instance_and_checks_result(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "discover", lambda _: [{"id": "androida", "label": "A"}])
    monkeypatch.setattr(settings, "ensure_runtime", lambda _: None)
    runner = SimpleNamespace(
        run=lambda args, **kw: (
            calls.append((args, kw))
            or '{"touch":true,"keyboard":false,"navigation":false,"arrows":false}'
        )
    )
    assert (
        settings.configure(runner, "androida", "org.example.game", touch=True, keyboard=False)[
            "label"
        ]
        == "A"
    )
    assert calls[0][0] == [
        "/usr/local/bin/multipak-waydroid",
        "--instance",
        "androida",
        "app-config",
        "set",
        "org.example.game",
        "--touch",
        "on",
        "--keyboard",
        "off",
    ]
    runner.run = lambda *_a, **_kw: '{"touch":"true","keyboard":false}'
    with pytest.raises(RuntimeError, match="confirm"):
        settings.configure(runner, "androida", "org.example.game")


def test_reopen_never_stops_container_and_requires_launch_confirmation(monkeypatch):
    monkeypatch.setattr("multipak.privileged.ensure_installed", lambda _: None)
    calls = []
    monkeypatch.setattr(settings, "configure", lambda *_: {"touch": True, "keyboard": False})
    fake = SimpleNamespace(command=lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(
        settings,
        "InstanceWaydroid",
        lambda _r, ident: fake if ident == "androida" else pytest.fail("wrong instance"),
    )
    runner = SimpleNamespace(
        run=lambda args, **kw: calls.append(args) or "MULTIPAK_ANDROID_APP_REOPENED\n"
    )
    settings.reopen(runner, "androida", "org.example.game", lambda *_: None)
    assert calls[0] == ("prop", "set", "waydroid.active_apps", "org.example.game")
    assert calls[1][0] == "pkexec"
    assert calls[1][-3:] == ["reopen-app", "androida", "org.example.game"]
    runner.run = lambda *_a, **_kw: "Error: activity not started"
    with pytest.raises(RuntimeError, match="confirm"):
        settings.reopen(runner, "androida", "org.example.game", lambda *_: None)


def test_removed_navigation_override_is_cleared_without_changing_input(inputs, tmp_path):
    platform = Platform()
    inputs.apply(platform, "org.example.game", True, False, True)
    assert platform.policy == "immersive.status=*"
    inputs.launch_policy(platform, "true")
    assert platform.policy == "immersive.full=*"
    assert platform.touch == "org.other.app,org.example.game"
    assert platform.keyboard == "0"
    assert json.loads((tmp_path / "input-preferences.json").read_text()) == {"keyboard": False}
    inputs.launch_policy(platform, "false")
    assert platform.policy == "immersive.status=*"


@pytest.mark.parametrize("state", ["RUNNING", "FROZEN"])
def test_privileged_reopen_confirms_activity_without_restarting_android(monkeypatch, state):
    from multipak import android_instances_helper as helper

    calls = []
    monkeypatch.setattr(
        helper,
        "metadata",
        lambda ident, owner: {} if ident == "androida" else pytest.fail("wrong instance"),
    )

    def run(args):
        calls.append(args)
        if args[0].endswith("lxc-info"):
            return state
        if "resolve-activity" in args:
            return "org.example.game/.MainActivity\n"
        if "start" in args:
            return "Status: ok\nActivity: org.example.game/.MainActivity\n"
        return ""

    monkeypatch.setattr(helper, "run", run)
    helper.reopen_application(object(), "androida", "org.example.game")
    assert all("session" not in call and "systemctl" not in call for call in calls)
    force = next(call for call in calls if "force-stop" in call)
    assert force[-3:] == ["--user", "0", "org.example.game"]
    assert calls[-1][-2:] == ["-n", "org.example.game/.MainActivity"]
    assert "-W" in calls[-1]


@pytest.mark.parametrize("component", ["No activity found", "org.other.app/.Main", "/bad"])
def test_no_force_stop_if_android_cannot_resolve_target(monkeypatch, component):
    from multipak import android_instances_helper as helper

    calls = []
    monkeypatch.setattr(helper, "metadata", lambda *_: {})

    def run(args):
        calls.append(args)
        return "RUNNING" if args[0].endswith("lxc-info") else component

    monkeypatch.setattr(helper, "run", run)
    with pytest.raises(RuntimeError, match="launcher"):
        helper.reopen_application(object(), "androida", "org.example.game")
    assert not any("force-stop" in call for call in calls)


def test_arrow_profiles_and_accessibility_services_are_preserved(inputs):
    platform = Platform()
    platform.secure[inputs.ACCESSIBILITY] = "org.reader/.ReaderService"
    inputs.set_arrows(platform, "org.example.game", True)
    inputs.set_arrows(platform, "org.other.game", True)
    assert inputs.read(platform, "org.example.game")["arrows"] is True
    assert inputs.read(platform, "org.third.game")["arrows"] is False
    assert platform.secure[inputs.ACCESSIBILITY] == "org.reader/.ReaderService:" + inputs.SERVICE
    inputs.set_arrows(platform, "org.example.game", False)
    assert inputs.read(platform, "org.other.game")["arrows"] is True
    assert inputs.SERVICE in platform.secure[inputs.ACCESSIBILITY]
    inputs.set_arrows(platform, "org.other.game", False)
    assert platform.secure[inputs.ACCESSIBILITY] == "org.reader/.ReaderService"
    assert platform.secure["accessibility_enabled"] == "1"
    assert platform.touch == "org.other.app"
    assert platform.keyboard == "1"


def test_arrow_settings_require_android_confirmation(inputs):
    platform = Platform()
    platform.settingsPutString = lambda *_: None
    with pytest.raises(RuntimeError, match="confirm"):
        inputs.set_arrows(platform, "org.example.game", True)


def test_mapper_install_is_scoped_cached_and_preserves_preferences(inputs, tmp_path):
    installed = []
    paths = []
    platform = Platform()
    manager = SimpleNamespace(ListPackages=lambda: installed)

    def install(path):
        paths.append(path)
        assert path.startswith("/data/waydroid_tmp/multipak-gestures-")
        assert path.endswith("/gestures.apk")
        installed.append(inputs.MAPPER)
        return 0

    platform.installApp = install
    inputs.initialize_keyboard(platform)
    inputs.ensure_mapper(platform, manager)
    inputs.ensure_mapper(platform, manager)
    assert len(paths) == 1
    saved = json.loads((tmp_path / "input-preferences.json").read_text())
    assert saved["keyboard"] is False and len(saved["gestures_apk_sha256"]) == 64
    assert not list((tmp_path / "exchange/apks").iterdir())


def test_read_does_not_report_arrows_enabled_when_service_was_reset(inputs):
    platform = Platform()
    inputs.set_arrows(platform, "org.example.game", True)
    platform.secure[inputs.ACCESSIBILITY] = ""
    assert inputs.read(platform, "org.example.game")["arrows"] is False

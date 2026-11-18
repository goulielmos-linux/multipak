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


def test_keyboard_follows_the_container_menu_at_each_launch(inputs, tmp_path):
    platform = Platform()
    inputs.apply_keyboard(platform)
    assert platform.keyboard == "0"  # Off until chosen.
    (tmp_path / "input-preferences.json").write_text(json.dumps({"keyboard": True}))
    inputs.apply_keyboard(platform)
    assert platform.keyboard == "1"
    platform.keyboard = "0"  # Changed inside Android: the menu wins at the next launch.
    inputs.apply_keyboard(platform)
    assert platform.keyboard == "1"


def test_the_keyboard_command_saves_and_applies_when_android_runs(
    inputs, tmp_path, monkeypatch, capsys
):
    platform, calls = Platform(), []
    manager = SimpleNamespace(
        GetSession=lambda: {"state": "FROZEN"},
        Unfreeze=lambda: calls.append("unfreeze"),
        Freeze=lambda: calls.append("freeze"),
    )
    tools = SimpleNamespace(
        helpers=SimpleNamespace(ipc=SimpleNamespace(DBusContainerService=lambda: manager))
    )
    interfaces = SimpleNamespace(IPlatform=SimpleNamespace(get_service=lambda _args: platform))
    for name, module in {
        "tools": tools,
        "tools.helpers": tools.helpers,
        "tools.helpers.ipc": tools.helpers.ipc,
        "tools.interfaces": interfaces,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    inputs.keyboard_run(SimpleNamespace(mode="on"))
    assert platform.keyboard == "1" and calls == ["unfreeze", "freeze"]
    assert json.loads(capsys.readouterr().out) == {"keyboard": True}
    manager.GetSession = lambda: {}  # Android off: saved for the next launch only.
    inputs.keyboard_run(SimpleNamespace(mode="off"))
    assert platform.keyboard == "1" and json.loads(capsys.readouterr().out) == {"keyboard": False}
    inputs.keyboard_run(SimpleNamespace(mode="get"))
    assert json.loads(capsys.readouterr().out) == {"keyboard": False}


def test_touch_changes_preserve_other_apps_and_keyboard_is_container_wide(inputs):
    platform = Platform()
    result = inputs.apply(platform, "org.example.game", True, False)
    assert result == {
        "touch": True,
        "keyboard": False,
        "navigation": False,
        "arrows": False,
        "links_android": False,
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
            or '{"touch":true,"keyboard":false,"navigation":false,"arrows":false,'
            '"links_android":false}'
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
    settings.configure(runner, "androida", "org.example.game", touch=True)
    assert "--keyboard" not in calls[-1][0]
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
        assert path.startswith("/data/waydroid_tmp/multipak-controller-")
        assert path.endswith("/controller.apk")
        installed.append(inputs.MAPPER)
        return 0

    platform.installApp = install
    inputs.apply(platform, "org.example.game", True, False)
    inputs.ensure_mapper(platform, manager)
    inputs.ensure_mapper(platform, manager)
    assert len(paths) == 1
    saved = json.loads((tmp_path / "input-preferences.json").read_text())
    assert saved["keyboard"] is False and len(saved["controller_apk_sha256"]) == 64
    assert not list((tmp_path / "exchange/apks").iterdir())


def test_a_container_with_the_old_app_moves_to_the_controller(inputs, tmp_path):
    installed = ["org.example.game", inputs.OLD_MAPPER]
    platform = Platform()
    platform.secure = {
        "multipak_arrow_packages": "org.example.game",
        "enabled_accessibility_services": "org.other/.Reader:"
        + inputs.OLD_MAPPER
        + "/.ArrowService",
        "accessibility_enabled": "1",
    }
    platform.installApp = lambda path: installed.append(inputs.MAPPER) or 0
    manager = SimpleNamespace(
        ListPackages=lambda: list(installed),
        RemoveOldHelper=lambda: installed.remove(inputs.OLD_MAPPER),
    )
    (tmp_path / "input-preferences.json").write_text(json.dumps({"gestures_apk_sha256": "old"}))
    assert inputs.ensure_mapper(platform, manager)
    assert installed == ["org.example.game", inputs.MAPPER]
    assert (
        platform.secure["enabled_accessibility_services"] == "org.other/.Reader:" + inputs.SERVICE
    )
    assert platform.secure["multipak_arrow_packages"] == "org.example.game"
    assert inputs.read(platform, "org.example.game")["arrows"]
    saved = json.loads((tmp_path / "input-preferences.json").read_text())
    assert "gestures_apk_sha256" not in saved and len(saved["controller_apk_sha256"]) == 64
    assert not inputs.ensure_mapper(platform, manager)


def test_a_new_app_listed_a_moment_later_is_installed(inputs):
    installed, pending = [inputs.OLD_MAPPER], []
    platform = Platform()
    platform.installApp = lambda path: pending.append(inputs.MAPPER) or 0

    def listing():
        if pending and len(pending) < 3:
            pending.append(None)  # Android lists the new app on the third look.
        elif pending and inputs.MAPPER not in installed:
            installed.append(inputs.MAPPER)
        return list(installed)

    manager = SimpleNamespace(
        ListPackages=listing, RemoveOldHelper=lambda: installed.remove(inputs.OLD_MAPPER)
    )
    assert inputs.ensure_mapper(platform, manager)
    assert installed == [inputs.MAPPER]


def test_an_old_app_that_stays_is_tried_again_next_time(inputs):
    installed = [inputs.OLD_MAPPER]
    platform = Platform()

    def install(path):
        if inputs.MAPPER not in installed:  # Installing again only updates it.
            installed.append(inputs.MAPPER)
        return 0

    platform.installApp = install
    manager = SimpleNamespace(ListPackages=lambda: list(installed), RemoveOldHelper=lambda: None)
    with pytest.raises(RuntimeError, match="old"):
        inputs.ensure_mapper(platform, manager)
    manager.RemoveOldHelper = lambda: installed.remove(inputs.OLD_MAPPER)
    assert inputs.ensure_mapper(platform, manager)
    assert installed == [inputs.MAPPER]


def test_read_does_not_report_arrows_enabled_when_service_was_reset(inputs):
    platform = Platform()
    inputs.set_arrows(platform, "org.example.game", True)
    platform.secure[inputs.ACCESSIBILITY] = ""
    assert inputs.read(platform, "org.example.game")["arrows"] is False


def test_links_in_android_keep_other_apps_and_need_confirmation(inputs):
    platform = Platform()
    inputs.set_links_in_android(platform, "org.example.bank", True)
    inputs.set_links_in_android(platform, "org.other.app", True)
    assert platform.secure[inputs.LINKS_ANDROID] == "org.example.bank,org.other.app"
    assert inputs.read(platform, "org.example.bank")["links_android"] is True
    inputs.set_links_in_android(platform, "org.example.bank", False)
    assert platform.secure[inputs.LINKS_ANDROID] == "org.other.app"
    assert inputs.read(platform, "org.example.bank")["links_android"] is False
    with pytest.raises(ValueError):
        inputs.set_links_in_android(platform, "not a package", True)
    platform.settingsPutString = lambda *_: None
    with pytest.raises(RuntimeError, match="confirm"):
        inputs.set_links_in_android(platform, "org.example.bank", True)


def test_apply_changes_links_in_android_only_when_asked(inputs):
    platform = Platform()
    platform.secure[inputs.LINKS_ANDROID] = "org.other.app"
    inputs.apply(platform, "org.example.bank", False, True)
    assert platform.secure[inputs.LINKS_ANDROID] == "org.other.app"
    result = inputs.apply(platform, "org.example.bank", False, True, links_android=True)
    assert result["links_android"] is True
    assert platform.secure[inputs.LINKS_ANDROID] == "org.example.bank,org.other.app"


def test_configuration_passes_links_in_android(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "discover", lambda _: [{"id": "androida", "label": "A"}])
    monkeypatch.setattr(settings, "ensure_runtime", lambda _: None)
    reply = (
        '{"touch":false,"keyboard":false,"navigation":false,"arrows":false,"links_android":true}'
    )
    runner = SimpleNamespace(run=lambda args, **kw: calls.append(args) or reply)
    result = settings.configure(
        runner, "androida", "org.example.bank", touch=False, keyboard=False, links_android=True
    )
    assert result["links_android"] is True
    assert calls[0][-2:] == ["--links-android", "on"]


def test_media_volume_starts_at_maximum_only_once(inputs, tmp_path):
    calls = []
    manager = SimpleNamespace(MediaVolumeMax=lambda: calls.append(1))
    assert inputs.initialize_volume(manager) is True
    # Later changes made inside Android are kept: the next sessions do nothing.
    assert inputs.initialize_volume(manager) is False
    assert calls == [1]
    assert json.loads((tmp_path / "input-preferences.json").read_text())["media_volume"] is True


def test_media_volume_failure_is_retried_next_session(inputs):
    def broken():
        raise RuntimeError("not ready")

    with pytest.raises(RuntimeError):
        inputs.initialize_volume(SimpleNamespace(MediaVolumeMax=broken))
    calls = []
    assert inputs.initialize_volume(SimpleNamespace(MediaVolumeMax=lambda: calls.append(1)))
    assert calls == [1]


def test_the_old_app_is_removed_with_a_fixed_command():
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    method = source[source.index("def RemoveOldHelper") :].split("\n    @", 1)[0]
    assert "self._authorize(sender, conn)" in method
    assert 'self._cmd("package", "uninstall", multipak_input.OLD_MAPPER)' in method


def test_media_volume_uses_only_fixed_commands():
    source = (VENDOR / "tools/actions/container_manager.py").read_text()
    method = source[source.index("def MediaVolumeMax") :].split("\n    @", 1)[0]
    assert 'volume = ("media_session", "volume", "--stream", "3")' in method
    assert 'for probe in (("--get",), ("--set", "999")):' in method
    assert 'self._cmd(*volume, "--set", str(highest))' in method
    assert "self._authorize(sender, conn)" in method
    session = (VENDOR / "tools/services/user_manager.py").read_text()
    assert "multipak_input.initialize_volume(tools.helpers.ipc.DBusContainerService())" in session


def test_the_container_cannot_shut_down_the_host_filesystem():
    # Waydroid issue 2438: LineageOS 20 sends F2FS_IOC_SHUTDOWN, which reaches a host f2fs /data.
    rules = (VENDOR / "data/configs/waydroid.seccomp").read_text().splitlines()
    assert rules[:2] == ["2", "blacklist"]
    shutdown = (2 << 30) | (4 << 16) | (ord("X") << 8) | 125
    assert shutdown == 0x8004587D
    assert f"ioctl errno 1 [1,{0xFFFFFFFF},SCMP_CMP_MASKED_EQ,{shutdown}]" in rules
    assert "reboot" in rules


def test_media_volume_range_is_read_from_both_android_versions(inputs):
    # Real answers: Android 13 --get, and the refusal Android 11 gives for a too-high index.
    assert inputs.media_volume_max("[V] volume is 4 in range [0..15]\n") == 15
    refused = "usage: ...\nError: invalid volume index 999 for stream 3 (should be in [0..25])\n"
    assert inputs.media_volume_max(refused) == 25
    assert inputs.media_volume_max("") is None

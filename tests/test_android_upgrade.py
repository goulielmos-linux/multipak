import json
import shutil
from types import SimpleNamespace

import pytest

from multipak import android_instances as environments
from multipak import android_upgrade_helper as helper
from multipak import privileged


@pytest.fixture
def frontend(monkeypatch):
    calls = []
    settings = dict(active=True, updated=True, ready=True, sdk="33", bridge="libndk_translation.so")
    entry = dict(id="androida", initialized=True, sdk=33)
    monkeypatch.setattr(environments, "discover", lambda _: [entry])

    class Android:
        def __init__(self, runner, identifier):
            assert identifier == "androida"

        def command(self, *args):
            calls.append(args)
            if args == ("status",):
                return (
                    "Session: RUNNING\nContainer: FROZEN"
                    if settings["active"]
                    else "Session: STOPPED"
                )
            if args[-1] == "ro.build.version.sdk":
                return settings["sdk"]
            return settings["bridge"]

        def ready(self, progress, *, initialize):
            assert initialize is False
            calls.append("ready")
            return settings["ready"]

    def call(runner, subsystem, identifier, **kwargs):
        assert subsystem == "android-upgrade" and identifier == "androida"
        assert runner.defer_cancellation
        calls.append("upgrade")
        if settings.get("fail"):
            raise RuntimeError("download failed")
        if "output" in settings:
            return settings["output"]
        return "MULTIPAK_ANDROID_UPGRADE_RESULT " + json.dumps(
            {"updated": settings["updated"], "bridge": "libndk_translation.so"}
        )

    monkeypatch.setattr(environments, "InstanceWaydroid", Android)
    monkeypatch.setattr(privileged, "call", call)
    runner = SimpleNamespace(defer_cancellation=False, log=lambda _: None)
    return runner, settings, calls, entry


@pytest.mark.parametrize("active,updated", [(True, True), (False, True), (True, False)])
def test_update_targets_one_instance_checks_restart_and_handles_no_update(
    frontend, active, updated
):
    runner, settings, calls, _ = frontend
    settings.update(active=active, updated=updated)
    assert environments.upgrade(runner, "androida") is updated
    assert calls.count("ready") == int(active)
    assert not runner.defer_cancellation


@pytest.mark.parametrize("failure", ["ready", "sdk", "bridge"])
def test_image_update_cannot_report_success_when_restart_or_arm_check_fails(frontend, failure):
    runner, settings, _, _ = frontend
    settings[failure] = False if failure == "ready" else "incorrect"
    with pytest.raises(RuntimeError):
        environments.upgrade(runner, "androida")
    assert not runner.defer_cancellation


@pytest.mark.parametrize(
    "output", ["", 'MULTIPAK_ANDROID_UPGRADE_RESULT {"updated": "yes", "bridge": ""}']
)
def test_missing_confirmation_restores_running_session_and_never_reports_success(frontend, output):
    runner, settings, calls, _ = frontend
    settings["output"] = output
    with pytest.raises(ValueError):
        environments.upgrade(runner, "androida")
    assert "ready" in calls and not runner.defer_cancellation


def test_download_failure_attempts_to_resume_original_environment(frontend):
    runner, settings, calls, _ = frontend
    settings["fail"] = True
    with pytest.raises(RuntimeError, match="download failed"):
        environments.upgrade(runner, "androida")
    assert "ready" in calls and not runner.defer_cancellation


@pytest.mark.parametrize("identifier,prepared", [("system-waydroid", True), ("androida", False)])
def test_personal_or_unprepared_environment_is_rejected_before_privileged_call(
    frontend, identifier, prepared
):
    runner, _, calls, entry = frontend
    entry["initialized"] = prepared
    with pytest.raises(ValueError):
        environments.upgrade(runner, identifier)
    assert not calls


@pytest.fixture
def image_layout(tmp_path, monkeypatch):
    directory = tmp_path / "instances/androida"
    (directory / "images").mkdir(parents=True)
    (directory / "lxc/androida").mkdir(parents=True)
    (directory / "private/data").mkdir(parents=True)
    (directory / "private/data/saved-game").write_bytes(b"personal data")
    for name in (
        "images/system.img",
        "images/vendor.img",
        "waydroid_base.prop",
        "lxc/androida/config",
        "lxc/androida/config_nodes",
        "lxc/androida/config_session",
    ):
        (directory / name).write_bytes(b"original " + name.encode())
    (directory / "waydroid.cfg").write_text(
        f"[waydroid]\nimages_path={directory / 'images'}\nsystem_datetime=1\nvendor_datetime=1\n"
        "system_ota=https://ota.waydro.id/system/lineage/waydroid_x86_64/VANILLA.json\n"
        "vendor_ota=https://ota.waydro.id/vendor/waydroid_x86_64/MAINLINE.json\n"
        "[properties]\nro.dalvik.vm.native.bridge=libndk_translation.so\n"
    )
    (tmp_path / "unrelated").write_bytes(b"other container")
    monkeypatch.setattr(helper.instances, "BASE", tmp_path)
    monkeypatch.setattr(helper.instances, "metadata", lambda identifier, owner: {"data_layout": 2})
    monkeypatch.setattr(helper, "protected_directory", lambda _: None)
    monkeypatch.setattr(helper, "protected_file", lambda _: None)
    monkeypatch.setattr(helper.instances, "mounted_below", lambda *args: False)
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "/usr/bin/cp":
            shutil.copy2(argv[-2], argv[-1])
        return ""

    monkeypatch.setattr(helper.instances, "run", run)
    return directory, calls


@pytest.mark.parametrize("mode", ["updated", "current", "failed", "empty", "properties"])
def test_upstream_update_snapshots_restore_failures_and_preserve_app_data(
    image_layout, monkeypatch, capsys, mode
):
    directory, calls = image_layout
    original = (directory / "waydroid.cfg").read_bytes()
    original_image = (directory / "images/system.img").read_bytes()

    class Process:
        def __init__(self, argv, **kwargs):
            assert argv[-4:] == ["--instance", "androida", "--details-to-stdout", "upgrade"]
            assert kwargs["start_new_session"] and kwargs["env"] == helper.CLEAN_ENV

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, *, timeout):
            if mode == "current":
                return 0
            (directory / "images/system.img").write_bytes(b"" if mode == "empty" else b"updated")
            text = original.decode().replace("system_datetime=1", "system_datetime=2")
            if mode == "properties":
                text = text.replace("libndk_translation.so", "another.so")
            (directory / "waydroid.cfg").write_text(text)
            return 1 if mode == "failed" else 0

    monkeypatch.setattr(helper.subprocess, "Popen", Process)
    if mode in ("failed", "empty", "properties"):
        with pytest.raises(RuntimeError):
            helper.upgrade(object(), "androida")
        assert (directory / "images/system.img").read_bytes() == original_image
        assert (directory / "waydroid.cfg").read_bytes() == original
        assert any("systemctl" in call[0] for call in calls)
    else:
        helper.upgrade(object(), "androida")
        line = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("MULTIPAK_ANDROID_UPGRADE_RESULT ")
        )
        assert json.loads(line.split(" ", 1)[1])["updated"] is (mode == "updated")
    assert (directory / "private/data/saved-game").read_bytes() == b"personal data"
    assert (directory.parents[1] / "unrelated").read_bytes() == b"other container"
    assert not list(directory.glob(".upgrade-backup-*"))
    assert all("private/data" not in str(call) for call in calls)


def test_recovery_backup_is_retained_if_android_cannot_be_unmounted(image_layout, monkeypatch):
    directory, _ = image_layout
    monkeypatch.setattr(helper.instances, "mounted_below", lambda *args: True)
    backup = directory / ".backup"
    backup.mkdir()
    with pytest.raises(RuntimeError, match="retained"):
        helper.restore(directory, backup, ["images/system.img"], "androida")
    assert backup.exists() and (directory / "images/system.img").stat().st_size


def test_symlinked_update_inputs_are_rejected_before_root_copy(tmp_path):
    source = tmp_path / "image"
    source.symlink_to("/etc/shadow")
    with pytest.raises(PermissionError):
        helper.protected_file(source)
    with pytest.raises(OSError):
        helper.protected_directory(source)

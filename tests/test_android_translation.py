import configparser
import hashlib
import io
import threading
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_android import FakeRunner, inspect_apk, make_apk
from test_android import setup as android_setup

from multipak import android
from multipak import android_translation as translation
from multipak import android_translation_helper as helper
from multipak.models import AndroidPart
from multipak.process import Cancelled

setup = android_setup


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    monkeypatch.setattr(translation.platform, "machine", lambda: "x86_64")


@pytest.fixture
def arm(tmp_path):
    return inspect_apk(make_apk(tmp_path / "arm.apk", abis=("arm64-v8a",)))


def runner(*, bridge="0", **options):
    result = FakeRunner(**options)
    original = result.run

    def run(argv, **kwargs):
        if argv == [android.WAYDROID, "prop", "get", "ro.dalvik.vm.native.bridge"]:
            result.calls.append(argv)
            return bridge
        return original(argv, **kwargs)

    result.run = run
    return result


def test_arm_requirement_is_visible_before_any_mutation(arm):
    process = runner()
    plan = translation.inspect_plan(arm, process)
    assert plan.initialized and plan.translation == "required"
    assert all(call[0] != "pkexec" for call in process.calls)
    assert not any("start" in call or "init" in call for call in process.calls)


@pytest.mark.parametrize("initialized,running", [(False, False), (True, False)])
def test_unavailable_android_is_not_started_by_inspection(arm, initialized, running):
    process = runner(initialized=initialized, running=running)
    plan = translation.inspect_plan(arm, process)
    assert plan.initialized == initialized and plan.translation == "check"
    assert not any("prop" in call or "start" in call or "pkexec" in call for call in process.calls)


def test_reuses_existing_arm_support_and_native_apps_do_not_request_translation(arm):
    process = runner(bridge="libhoudini.so")
    process.abis += ",arm64-v8a"
    assert translation.inspect_plan(arm, process).translation == "available"
    native = replace(arm, architecture="arm64-v8a,x86_64")
    assert translation.inspect_plan(native, process).translation == "native"


def test_advertised_arm_abi_without_bridge_does_not_mean_available(arm):
    process = runner()
    process.abis += ",arm64-v8a"
    assert translation.inspect_plan(arm, process).translation == "required"
    with pytest.raises(RuntimeError, match="translation layer"):
        android.Waydroid(process).compatible(arm)


def test_other_android_versions_and_existing_incompatible_bridges_are_preserved(arm):
    process = runner()
    process.sdk = "35"
    assert translation.inspect_plan(arm, process).translation == "unsupported"
    process = runner(bridge="libhoudini.so")
    process.abis += ",armeabi-v7a"
    plan = translation.inspect_plan(arm, process)
    assert plan.translation == "unsupported" and "preserved" in plan.explanation


def test_bundle_requires_one_common_abi_across_its_features(arm):
    def part(name, abi, target):
        return AndroidPart(name, name, 1, "hash", "abi", target, abi)

    mixed = replace(
        arm,
        architecture="x86_64,arm64-v8a",
        android_parts=(
            part("base-arm", "arm64-v8a", ""),
            part("base-x86", "x86_64", ""),
            part("feature-arm", "arm64-v8a", "feature"),
        ),
    )
    assert translation.needs_arm(mixed, ("x86_64", "x86"))
    assert translation.matches_abis(mixed, ("arm64-v8a",))


def test_backend_never_installs_translation_without_explicit_consent(setup, monkeypatch):
    engine, apk = setup
    make_apk(apk, abis=("arm64-v8a",))
    monkeypatch.setattr(android, "install_translation", lambda *_: pytest.fail("No consent"))
    with pytest.raises(RuntimeError, match="allow its installation"):
        engine.install_android(inspect_apk(apk))
    assert not engine.tx.android_package_owned
    assert not engine.tx.android_translation_allowed


def test_consent_is_persisted_and_bridge_is_rechecked_before_app_install(setup, monkeypatch):
    engine, apk = setup
    make_apk(apk, abis=("arm64-v8a",))
    called = []

    def install(waydroid, progress):
        called.append("install")
        waydroid.runner.abis += ",arm64-v8a"

    monkeypatch.setattr(android, "install_translation", install)
    tx = engine.install_android(inspect_apk(apk), allow_translation=True)
    assert called == ["install"] and tx.status == "installed"
    assert engine.store.transactions()[0].android_translation_allowed


def test_unactivated_translation_does_not_install_app(setup, monkeypatch):
    engine, apk = setup
    make_apk(apk, abis=("arm64-v8a",))
    monkeypatch.setattr(android, "install_translation", lambda *_: None)
    with pytest.raises(RuntimeError, match="translation layer"):
        engine.install_android(inspect_apk(apk), allow_translation=True)
    assert not engine.tx.android_package_owned
    assert not any(call[1:3] == ["app", "install"] for call in engine.runner.calls)


def archive_bytes(extra=()):
    output = io.BytesIO()
    prefix = f"vendor_google_proprietary_ndk_translation-prebuilt-{helper.REVISION}/prebuilts/"
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("lib/libndk_translation.so", "lib64/libndk_translation.so", "bin/arm/linker"):
            archive.writestr(prefix + name, b"test fixture only")
        for name, data in extra:
            archive.writestr(prefix + name, data)
    return output.getvalue()


def waydroid_tree(tmp_path):
    base = tmp_path / "waydroid"
    (base / "rootfs/system").mkdir(parents=True)
    (base / "rootfs/system/build.prop").write_text("ro.build.version.sdk=33\n")
    (base / "waydroid.cfg").write_text(
        "[waydroid]\narch = x86_64\nmount_overlays = True\n"
        "[properties]\npersist.test.preserved = value\n"
    )
    (base / "data").mkdir()
    (base / "data/keep-apps").write_text("do not change")
    return base


def test_privileged_helper_uses_only_verified_bytes(tmp_path, monkeypatch):
    path = tmp_path / "translation.zip"
    data = archive_bytes()
    path.write_bytes(data)
    with pytest.raises(ValueError, match="integrity"):
        helper.verified_archive(path)
    monkeypatch.setattr(helper, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    assert helper.verified_archive(path) == data
    path.write_bytes(data + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        helper.verified_archive(path)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "lib/../../escape", "lib\\escape"])
def test_archive_paths_are_rejected_before_writing(name):
    with pytest.raises(ValueError, match="archive entry"):
        helper.payload(archive_bytes([(name, b"bad")]))


def test_overlay_and_properties_are_applied_without_changing_apps(tmp_path, monkeypatch):
    base = waydroid_tree(tmp_path)
    calls = []
    monkeypatch.setattr(helper.shutil, "which", lambda *_: None)
    helper.apply(archive_bytes(), base=base, run=calls.append)
    cfg = configparser.ConfigParser()
    cfg.read(base / "waydroid.cfg")
    assert cfg["properties"]["persist.test.preserved"] == "value"
    assert cfg["properties"]["ro.dalvik.vm.native.bridge"] == "libndk_translation.so"
    assert (base / "overlay/system/lib64/libndk_translation.so").is_file()
    assert (base / "overlay/system/bin/arm/linker").stat().st_mode & 0o777 == 0o755
    assert (base / "data/keep-apps").read_text() == "do not change"
    assert calls[0][-2:] == ["container", "stop"] and calls[-1][-2:] == ["upgrade", "-o"]
    assert (base / "waydroid.cfg.before-multipak-arm").is_file()


def test_failed_upgrade_restores_configuration_and_preserves_other_files(tmp_path, monkeypatch):
    base = waydroid_tree(tmp_path)
    original = (base / "waydroid.cfg").read_bytes()
    other = base / "overlay/system/etc/keep"
    other.parent.mkdir(parents=True)
    other.write_text("other addon")
    calls = []

    def run(argv):
        calls.append(argv)
        if len(calls) == 2:
            raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(helper.shutil, "which", lambda *_: None)
    with pytest.raises(RuntimeError, match="simulated"):
        helper.apply(archive_bytes(), base=base, run=run)
    assert (base / "waydroid.cfg").read_bytes() == original
    assert not (base / "overlay/system/lib64/libndk_translation.so").exists()
    assert other.read_text() == "other addon"
    assert (base / "data/keep-apps").read_text() == "do not change"
    assert len(calls) == 3  # Regenerate the original configuration after rollback.


@pytest.mark.parametrize("symlink", [False, True])
def test_overlay_conflicts_and_symlinks_never_stop_or_modify_android(tmp_path, symlink):
    base = waydroid_tree(tmp_path)
    original = (base / "waydroid.cfg").read_bytes()
    root = base / "overlay/system"
    if symlink:
        (base / "elsewhere").mkdir()
        (base / "overlay").symlink_to(base / "elsewhere", target_is_directory=True)
    else:
        (root / "lib").mkdir(parents=True)
        (root / "lib/libndk_translation.so").write_bytes(b"existing addon")
    with pytest.raises(ValueError):
        helper.apply(archive_bytes(), base=base, run=lambda *_: pytest.fail("Do not stop Android"))
    assert (base / "waydroid.cfg").read_bytes() == original
    assert not (base / "waydroid.cfg.before-multipak-arm").exists()


def test_container_stop_must_unmount_android_before_overlay_changes(tmp_path, monkeypatch):
    base = waydroid_tree(tmp_path)
    original = (base / "waydroid.cfg").read_bytes()
    monkeypatch.setattr(helper.os.path, "ismount", lambda *_: True)
    calls = []
    with pytest.raises(RuntimeError, match="could not stop"):
        helper.apply(archive_bytes(), base=base, run=calls.append)
    assert (base / "waydroid.cfg").read_bytes() == original
    assert not (base / "overlay").exists()
    assert len(calls) == 1 and calls[0][-2:] == ["container", "stop"]


def test_download_progress_hash_validation_and_cancellation(tmp_path, monkeypatch):
    data = b"sample archive" * 50000
    monkeypatch.setattr(translation, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())

    def opener(*args, **kwargs):
        stream = io.BytesIO(data)
        stream.geturl = lambda: "https://example.org/archive.zip"
        stream.headers = {"Content-Length": str(len(data))}
        return stream

    process = SimpleNamespace(cancelled=threading.Event())
    updates = []
    path = tmp_path / "ok.zip"
    translation.download(path, process, lambda *args: updates.append(args), opener=opener)
    assert path.read_bytes() == data
    assert updates[0][0] == -1 and updates[-1][0] == 100
    assert any(0 < percent < 100 for percent, _ in updates)
    monkeypatch.setattr(translation, "ARCHIVE_SHA256", "bad")
    with pytest.raises(ValueError, match="integrity"):
        translation.download(tmp_path / "bad.zip", process, lambda *_: None, opener=opener)
    process.cancelled.set()
    with pytest.raises(Cancelled):
        translation.download(tmp_path / "cancel.zip", process, lambda *_: None, opener=opener)


@pytest.mark.parametrize("authorize", [True, False])
def test_translation_setup_restarts_and_checks_android_after_authorization(
    tmp_path, monkeypatch, authorize
):
    process = runner()
    calls = []
    active = False

    class Waydroid:
        runner = process

        def command(self, *args, **kwargs):
            assert args[:2] == ("prop", "get"), "Do not stop Android before authorization"
            return (
                "33"
                if args[2] == "ro.build.version.sdk"
                else ("libndk_translation.so" if active else "0")
            )

        def ready(self, progress, *, initialize):
            assert active and initialize is False
            calls.append("ready")
            return True

    def privileged_call(selected_runner, subsystem, *args, **kwargs):
        nonlocal active
        assert selected_runner is process and subsystem == "arm"
        calls.append(args)
        assert process.defer_cancellation
        assert Path(args[0]).read_bytes() == b"downloaded fixture"
        if not authorize:
            raise RuntimeError("authorization declined")
        active = True
        return "MULTIPAK_ARM_TRANSLATION_INSTALLED\n"

    monkeypatch.setattr("multipak.privileged.call", privileged_call)

    def download(path, runner, progress):
        path.write_bytes(b"downloaded fixture")

    monkeypatch.setattr(translation, "download", download)
    monkeypatch.setattr(
        translation, "restart_android", lambda w, progress: w.ready(progress, initialize=False)
    )
    monkeypatch.setattr(translation.host, "is_flatpak", lambda: False)
    monkeypatch.setattr(translation.host, "temporary_directory", lambda: tmp_path)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            "sse4_2" if str(path) == "/proc/cpuinfo" else original(path, *a, **kw)
        ),
    )
    if authorize:
        translation.install_translation(Waydroid(), lambda *_: None)
        assert active and calls[-1] == "ready"
    else:
        with pytest.raises(RuntimeError, match="authorization declined"):
            translation.install_translation(Waydroid(), lambda *_: None)
        assert not active and len(calls) == 1
    assert not process.defer_cancellation
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bridge", ["libndk_translation.so", "0"])
def test_restart_finishes_before_pending_cancellation_and_checks_bridge(monkeypatch, bridge):
    original = SimpleNamespace(cancelled=threading.Event(), log=lambda *_: None)
    original.cancelled.set()
    calls = []

    class Waydroid:
        def __init__(self, process):
            assert not process.cancelled.is_set()
            self.runner = SimpleNamespace(run=self.run)

        def run(self, args, **kwargs):
            calls.append(args)
            return "deactivating"

        def ready(self, progress, *, initialize):
            assert initialize is False
            progress(35, "Starting Android")
            calls.append("ready")

        def command(self, *args):
            assert args == ("prop", "get", "ro.dalvik.vm.native.bridge")
            return bridge

    def progress(*_):
        raise Cancelled

    monkeypatch.setattr(android, "Waydroid", Waydroid)
    if bridge == "0":
        with pytest.raises(RuntimeError, match="did not become active"):
            translation.restart_android(SimpleNamespace(runner=original), progress)
    else:
        translation.restart_android(SimpleNamespace(runner=original), progress)
    assert calls[-1] == "ready" and calls[1][2] == "stop"
    assert original.cancelled.is_set()

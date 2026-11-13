import configparser
import json
import struct
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from multipak import android, android_initializer
from multipak.android_apk import ANDROID_NS, NO_INDEX, inspect_apk, manifest_elements
from multipak.engine import Engine
from multipak.metadata import inspect_package, package_kind
from multipak.process import Cancelled, CommandError


def manifest(
    *,
    package_id="org.example.demo",
    abis=(),
    split=False,
    utf8=True,
    feature=False,
    target="",
    required_types="",
    split_types="",
    dependency="",
    code=24,
    sdk=23,
    target_sdk=0,
):
    """Minimal binary Android XML with typed attributes and a launcher activity."""
    strings = [
        ANDROID_NS,
        "manifest",
        "package",
        package_id,
        "versionCode",
        "versionName",
        "2.4",
        "uses-sdk",
        "minSdkVersion",
        "targetSdkVersion",
        "application",
        "label",
        "Demo Android",
        "split",
        "config.en",
        "activity",
        "intent-filter",
        "action",
        "category",
        "name",
        "android.intent.action.MAIN",
        "android.intent.category.LAUNCHER",
        "isFeatureSplit",
        "configForSplit",
        "requiredSplitTypes",
        "splitTypes",
        "uses-split",
    ]
    for value in (split, target, required_types, split_types, dependency):
        if isinstance(value, str) and value and value not in strings:
            strings.append(value)
    blob, offsets = bytearray(), []
    for value in strings:
        offsets.append(len(blob))
        raw = value.encode("utf-8" if utf8 else "utf-16-le")
        if utf8:
            blob.extend(bytes([len(value), len(raw)]) + raw + b"\0")
        else:
            blob.extend(struct.pack("<H", len(value)) + raw + b"\0\0")
    start = 28 + 4 * len(strings)
    pool = struct.pack(
        "<HH6I", 1, 28, start + len(blob), len(strings), 0, 0x100 if utf8 else 0, start, 0
    )
    pool += struct.pack(f"<{len(strings)}I", *offsets) + blob

    def attr(name, value, kind=3, namespace=ANDROID_NS):
        return struct.pack(
            "<IIIHBBI",
            strings.index(namespace) if namespace else NO_INDEX,
            strings.index(name),
            NO_INDEX,
            8,
            0,
            kind,
            strings.index(value) if kind == 3 else value,
        )

    def begin(name, attributes=b""):
        return (
            struct.pack("<HHIII", 0x102, 16, 36 + len(attributes), 1, NO_INDEX)
            + struct.pack(
                "<II6H", NO_INDEX, strings.index(name), 20, 20, len(attributes) // 20, 0, 0, 0
            )
            + attributes
        )

    def end(name):
        return struct.pack("<HHIIIII", 0x103, 16, 24, 1, NO_INDEX, NO_INDEX, strings.index(name))

    attrs = (
        attr("package", package_id, namespace=None)
        + attr("versionCode", code, 0x10)
        + attr("versionName", "2.4")
    )
    if split:
        attrs += attr("split", split if isinstance(split, str) else "config.en", namespace=None)
    if feature:
        attrs += attr("isFeatureSplit", 1, 0x12)
    if target:
        attrs += attr("configForSplit", target, namespace=None)
    for key, value in (("requiredSplitTypes", required_types), ("splitTypes", split_types)):
        if value:
            attrs += attr(key, value)
    body = (
        pool
        + begin("manifest", attrs)
        + begin(
            "uses-sdk",
            attr("minSdkVersion", sdk, 0x10)
            + (attr("targetSdkVersion", target_sdk, 0x10) if target_sdk else b""),
        )
        + end("uses-sdk")
        + (begin("uses-split", attr("name", dependency)) + end("uses-split") if dependency else b"")
        + begin("application", attr("label", "Demo Android"))
        + begin("activity")
        + begin("intent-filter")
        + begin("action", attr("name", "android.intent.action.MAIN"))
        + end("action")
        + begin("category", attr("name", "android.intent.category.LAUNCHER"))
        + end("category")
        + end("intent-filter")
        + end("activity")
        + end("application")
        + end("manifest")
    )
    return struct.pack("<HHI", 3, 8, 8 + len(body)) + body


def make_apk(path, *, abis=(), **kwargs):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", manifest(**kwargs))
        archive.writestr("classes.dex", b"dex\n035\0not executable fixture")
        for abi in abis:
            archive.writestr(f"lib/{abi}/libdemo.so", b"not executed")
    return path


def initialization_args(argv):
    from multipak.privileged import HELPER

    if argv[:3] == ["pkexec", HELPER, "initialize"]:
        assert Path(argv[3]).is_file()
        return [
            "pkexec",
            android.WAYDROID,
            *android_initializer.initialization_arguments(
                argv[4:], channels_file=Path("/nonexistent-multipak-test-channels.cfg")
            ),
        ]
    if argv[:4] == ["pkexec", HELPER, "waydroid", "personal"]:
        return ["pkexec", android.WAYDROID, *argv[4:]]
    return argv


class FakeRunner:
    def __init__(
        self, *, initialized=True, running=True, apps=(), fail_install=False, install_error=False
    ):
        self.cancelled = threading.Event()
        self.defer_cancellation = False
        self.initialized, self.running = initialized, running
        self.rootfs = initialized
        self.installed = set(apps)
        self.calls = []
        self.fail_install, self.install_error = fail_install, install_error
        self.sdk, self.abis = "33", "x86_64,x86"

    def run(self, argv, **kwargs):
        from multipak.privileged import MANIFEST

        if argv == ["cat", MANIFEST]:
            digest = (
                Path(android.__file__)
                .with_name("assets")
                .joinpath("privileged.sha256")
                .read_text()
                .strip()
            )
            return json.dumps({"bundle_sha256": digest})
        argv = initialization_args(argv)
        self.calls.append(argv)
        if argv == ["test", "-s", android.CONFIG]:
            if not self.initialized:
                raise CommandError(argv, 1, "")
            return ""
        if argv == ["test", "-d", android.ROOTFS]:
            if not self.rootfs:
                raise CommandError(argv, 1, "")
            return ""
        if argv[:3] == ["pkexec", android.WAYDROID, "init"]:
            self.initialized = self.rootfs = True
            return ""
        if argv[:2] == ["pkexec", "/usr/bin/restorecon"]:
            return ""
        if argv[:2] == ["systemctl", "is-active"]:
            return "active\n"
        if argv[0] == "systemd-run":
            self.running = True
            return ""
        if argv[0] == "systemctl":
            return ""
        if argv[0] == "pkexec" and android.REMOVE_ANDROID in argv:
            assert 'pm uninstall -k "$package"' in android.REMOVE_ANDROID
            self.installed.discard(argv[-1])
            return "Success\nMULTIPAK_ANDROID_PACKAGE_REMOVED\n"
        assert argv[0] == android.WAYDROID, argv
        if argv[1:] == ["status"]:
            return (
                "Session:\tRUNNING\nContainer:\tRUNNING\n"
                if self.running
                else "Session:\tSTOPPED\n"
            )
        if argv[1:3] == ["prop", "get"]:
            return self.sdk if argv[3] == "ro.build.version.sdk" else self.abis
        if argv[1:3] == ["prop", "set"]:
            return ""
        if argv[1:] == ["app", "list"]:
            return "".join(
                f"Name: Demo\npackageName: {name}\ncategories:\n" for name in self.installed
            )
        if argv[1:3] == ["app", "install"]:
            if self.install_error:
                return "[ERROR] Failed to access IPlatform service\n"
            if not self.fail_install:
                self.installed.add("org.example.demo")
            return ""
        raise AssertionError(argv)


@pytest.fixture
def setup(store, tmp_path, monkeypatch):
    monkeypatch.setattr(android.host, "is_flatpak", lambda: False)
    monkeypatch.setattr(android.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(android.host, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    engine = Engine(store, threading.Event())
    engine.preflight = lambda: pytest.fail("Android must not require Podman or Distrobox")
    engine.runner = FakeRunner()
    apk = make_apk(tmp_path / "a package; $(never execute).rpm")
    return engine, apk


@pytest.mark.parametrize("utf8", [True, False])
def test_binary_manifest_metadata_uses_content_not_extension(tmp_path, utf8):
    path = make_apk(tmp_path / "renamed.rpm", utf8=utf8, abis=("arm64-v8a", "x86_64"))
    package = inspect_package(path, None)
    assert package_kind(path) == "apk"
    assert (package.name, package.version, package.desktop_entry) == (
        "Demo Android",
        "2.4",
        "org.example.demo",
    )
    assert package.architecture == "arm64-v8a,x86_64"
    assert package.android_min_sdk == 23 and package.android_version_code == 24


@pytest.mark.parametrize(
    "package_id", ["org.example;touch", "org.example\nExec=evil", "../escape", "org..example"]
)
def test_reject_invalid_package_identifiers(tmp_path, package_id):
    with pytest.raises(ValueError, match="Invalid Android package ID"):
        inspect_apk(make_apk(tmp_path / "bad.apk", package_id=package_id))


def test_split_apk_and_plain_zip_are_not_installable(tmp_path):
    with pytest.raises(ValueError, match="complete APK"):
        inspect_apk(make_apk(tmp_path / "split.apk", split=True))
    path = tmp_path / "archive.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("unrelated.txt", "not an Android application")
    with pytest.raises(ValueError, match="Android package"):
        package_kind(path)


def test_duplicate_and_oversized_manifest_are_rejected(tmp_path):
    path = make_apk(tmp_path / "duplicate.apk")
    with zipfile.ZipFile(path, "a") as archive, pytest.warns(UserWarning):
        archive.writestr("AndroidManifest.xml", manifest())
    with pytest.raises(ValueError, match="duplicate"):
        inspect_apk(path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", b"\0" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="oversized"):
        inspect_apk(path)


@pytest.mark.parametrize("offset", [4, 12, 24, 36, 60])
def test_truncated_manifest_chunks_are_rejected(offset):
    data = manifest()
    with pytest.raises(ValueError):
        manifest_elements(data[:-offset])


def test_install_and_remove_keeps_shared_android_and_other_apps(setup, monkeypatch):
    engine, apk = setup
    engine.runner.installed.add("org.example.other")
    original = apk.read_bytes()
    tx = engine.install_android(inspect_apk(apk))
    assert tx.method == "waydroid" and tx.status == "installed" and tx.android_package_owned
    desktop = engine.store.launcher(tx, tx.launchers[0])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(desktop)
    assert parser["Desktop Entry"]["Exec"].endswith('app launch "org.example.demo"')
    assert "show-full-ui" not in str(engine.runner.calls)
    assert "init" not in str(engine.runner.calls)
    assert str(apk) not in str(engine.runner.calls)  # Install the retained snapshot.
    runner = engine.runner
    monkeypatch.setattr(android, "Runner", lambda *_: runner)
    engine.cleanup(tx)
    assert runner.installed == {"org.example.other"}
    assert not desktop.exists() and engine.store.transactions() == []
    assert apk.read_bytes() == original
    assert not any("reset" in call or "stop" in call for call in runner.calls)


def test_first_install_initializes_android_and_starts_a_background_session(setup):
    engine, apk = setup
    engine.runner = FakeRunner(initialized=False, running=False)
    engine.install_android(inspect_apk(apk))
    init = [
        args for args in engine.runner.calls if args[:3] == ["pkexec", android.WAYDROID, "init"]
    ]
    assert len(init) == 1
    assert init[0][-6:] == [
        "-c",
        android_initializer.SYSTEM_CHANNEL,
        "-v",
        android_initializer.VENDOR_CHANNEL,
        "-r",
        "lineage",
    ]
    assert ["systemctl", "--user", "reset-failed", android.SESSION_UNIT] not in engine.runner.calls
    session = next(call for call in engine.runner.calls if call[0] == "systemd-run")
    assert session[-3:] == [android.WAYDROID, "session", "start"]
    assert "--setenv=WAYLAND_DISPLAY=wayland-0" in session


@pytest.mark.parametrize("message", ["Authorization denied", "Failed to get system OTA channel"])
def test_other_initialization_errors_do_not_change_channels_or_retry(setup, message):
    engine, apk = setup
    runner = engine.runner = FakeRunner(initialized=False, running=False)
    original = runner.run
    first = [
        "pkexec",
        android.WAYDROID,
        *android_initializer.initialization_arguments(
            ["init", "-s", "VANILLA"], Path("/nonexistent-multipak-test-channels.cfg")
        ),
    ]

    def run(argv, **kwargs):
        if initialization_args(argv) == first:
            runner.calls.append(first)
            raise CommandError(argv, 1, message)
        return original(argv, **kwargs)

    runner.run = run
    with pytest.raises(RuntimeError, match=message):
        engine.install_android(inspect_apk(apk))
    assert [args for args in runner.calls if args[:3] == first[:3]] == [first]
    assert not runner.running and not runner.initialized
    assert not engine.tx.android_package_owned


def test_interrupted_initialization_with_config_but_no_rootfs_is_prepared_again(setup):
    engine, apk = setup
    engine.runner.rootfs = False
    assert engine.install_android(inspect_apk(apk)).status == "installed"
    assert any(args[:3] == ["pkexec", android.WAYDROID, "init"] for args in engine.runner.calls)


def test_failed_existing_session_unit_is_reset_before_restart(setup):
    engine, apk = setup
    runner = engine.runner = FakeRunner(running=False)
    original = runner.run

    def run(argv, **kwargs):
        if argv == ["systemctl", "--user", "is-active", android.SESSION_UNIT]:
            runner.calls.append(argv)
            return "failed\n"
        return original(argv, **kwargs)

    runner.run = run
    assert engine.install_android(inspect_apk(apk)).status == "installed"
    reset = ["systemctl", "--user", "reset-failed", android.SESSION_UNIT]
    assert runner.calls.count(reset) == 1
    start = next(i for i, argv in enumerate(runner.calls) if argv[0] == "systemd-run")
    assert runner.calls.index(reset) < start


def test_readiness_waits_for_android_platform_and_reports_waiting(setup):
    engine, _ = setup
    runner = engine.runner
    original = runner.run
    attempts, updates = [], []

    def run(argv, **kwargs):
        if argv == [android.WAYDROID, "app", "list"] and not attempts:
            attempts.append(argv)
            return "Failed to get service waydroidplatform, trying again...\n"
        return original(argv, **kwargs)

    runner.run = run
    assert android.Waydroid(runner).ready(lambda *args: updates.append(args)) is True
    assert (-1, "Waiting for Android to finish starting…") in updates
    assert len(attempts) == 1
    assert [android.WAYDROID, "app", "list"] in runner.calls


def test_platform_startup_warning_is_recovered_when_app_list_follows(setup):
    engine, _ = setup
    engine.runner.run = lambda *_args, **_kwargs: (
        "Failed to get service waydroidplatform, trying again...\n"
        "Name: Demo\npackageName: org.example.demo\n"
    )
    assert android.Waydroid(engine.runner).apps() == {"org.example.demo": "Demo"}


def test_preexisting_application_is_never_claimed_or_removed(setup, monkeypatch):
    engine, apk = setup
    engine.runner.installed.add("org.example.demo")
    with pytest.raises(RuntimeError, match="already installed"):
        engine.install_android(inspect_apk(apk))
    tx = engine.store.transactions()[0]
    assert tx.android_package_owned is False
    monkeypatch.setattr(android, "Runner", lambda *_: pytest.fail("Do not touch Android"))
    engine.cleanup(tx)
    assert "org.example.demo" in engine.runner.installed
    assert not any(call[1:3] == ["app", "install"] for call in engine.runner.calls)


def test_zero_exit_with_error_output_is_not_success(setup):
    engine, apk = setup
    engine.runner.install_error = True
    with pytest.raises(RuntimeError, match="could not complete"):
        engine.install_android(inspect_apk(apk))
    assert engine.tx.status == "failed"
    assert engine.tx.launchers == []


def test_no_success_without_confirmed_installation(setup, monkeypatch):
    engine, apk = setup
    engine.runner.fail_install = True
    ticks = iter([0, 100])
    monkeypatch.setattr(android.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(android.Waydroid, "ready", lambda *_: None)
    with pytest.raises(RuntimeError, match="did not confirm"):
        engine.install_android(inspect_apk(apk))
    assert engine.tx.status == "failed" and not engine.tx.launchers


def test_retry_validates_retained_package_hash(setup):
    engine, apk = setup
    engine.runner.install_error = True
    with pytest.raises(RuntimeError):
        engine.install_android(inspect_apk(apk))
    tx = engine.tx
    staged = engine.store.directory(tx.id) / "package.apk"
    staged.write_bytes(staged.read_bytes() + b"changed")
    before = len(engine.runner.calls)
    with pytest.raises(RuntimeError, match="incomplete or has changed"):
        engine.retry(tx)
    assert len(engine.runner.calls) == before


def test_retry_after_successful_install_does_not_install_again(setup):
    engine, apk = setup
    tx = engine.install_android(inspect_apk(apk))
    engine.retry(tx)
    assert sum(call[1:3] == ["app", "install"] for call in engine.runner.calls) == 1


def test_reuses_waydroid_desktop_without_overwriting_it(setup):
    engine, apk = setup
    desktop = engine.store.applications / "waydroid.org.example.demo.desktop"
    content = (
        "[Desktop Entry]\nType=Application\nName=Existing shortcut\n"
        "Exec=/usr/bin/waydroid app launch org.example.demo\n"
    )
    desktop.write_text(content)
    tx = engine.install_android(inspect_apk(apk))
    assert not tx.launchers
    assert desktop.read_text() == content
    assert len(list(engine.store.applications.glob("*.desktop"))) == 1


@pytest.mark.parametrize("abis", ["arm64-v8a", "armeabi-v7a"])
def test_arm_apk_requires_matching_android_abi(setup, abis):
    engine, apk = setup
    package = replace(inspect_apk(apk), architecture=abis)
    with pytest.raises(RuntimeError, match="translation layer"):
        android.Waydroid(engine.runner).compatible(package)
    engine.runner.abis += "," + abis
    android.Waydroid(engine.runner).compatible(package)


def test_newer_android_requirement_is_reported_before_install(setup):
    engine, apk = setup
    with pytest.raises(RuntimeError, match="newer Android"):
        android.Waydroid(engine.runner).compatible(replace(inspect_apk(apk), android_min_sdk=99))


def test_cancelled_install_does_not_start_android(setup):
    engine, apk = setup
    engine.cancelled.set()
    with pytest.raises(Cancelled):
        engine.install_android(inspect_apk(apk))
    assert not engine.runner.calls


def test_cleanup_failure_keeps_journal_for_retry(setup, monkeypatch):
    engine, apk = setup
    tx = engine.install_android(inspect_apk(apk))
    runner = engine.runner
    monkeypatch.setattr(android, "Runner", lambda *_: runner)
    monkeypatch.setattr(
        android.Waydroid,
        "privileged",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Authorization denied")),
    )
    with pytest.raises(RuntimeError, match="Authorization denied"):
        engine.cleanup(tx)
    assert engine.store.transactions()[0].status == "cleanup_failed"
    assert engine.store.launcher(tx, tx.launchers[0]).exists()


def test_missing_wayland_does_not_initialize_android(setup, monkeypatch):
    engine, _ = setup
    monkeypatch.delenv("WAYLAND_DISPLAY")
    with pytest.raises(RuntimeError, match="Wayland"):
        android.Waydroid(engine.runner).ready()
    assert not engine.runner.calls


def test_cleanup_does_not_recreate_a_manually_removed_android_environment(setup, monkeypatch):
    engine, apk = setup
    tx = engine.install_android(inspect_apk(apk))
    runner = FakeRunner(initialized=False)
    monkeypatch.setattr(android, "Runner", lambda *_: runner)
    engine.cleanup(tx)
    assert engine.store.transactions() == []
    assert not any(call[0] == "pkexec" for call in runner.calls)


def test_app_name_containing_error_does_not_look_like_a_waydroid_failure(setup):
    engine, _ = setup
    engine.runner.run = lambda *_args, **_kwargs: (
        "Name: ERROR Demo\npackageName: org.example.demo\ncategories:\n"
    )
    assert android.Waydroid(engine.runner).apps() == {"org.example.demo": "ERROR Demo"}


def test_cleanup_ignores_stale_waydroid_app_list(setup, monkeypatch):
    engine, apk = setup
    tx = engine.install_android(inspect_apk(apk))
    runner = engine.runner
    # The APK was already removed, while Waydroid still advertises its retained data.
    runner.installed.clear()
    monkeypatch.setattr(android, "Runner", lambda *_: runner)
    monkeypatch.setattr(android.Waydroid, "apps", lambda _: {tx.android_package_id: "Stale app"})
    engine.cleanup(tx)
    assert not engine.store.transactions()
    assert not engine.store.launcher(tx, tx.launchers[0]).exists()
    assert any(android.REMOVE_ANDROID in call for call in runner.calls)


def test_cleanup_requires_package_manager_confirmation(setup, monkeypatch):
    engine, apk = setup
    tx = engine.install_android(inspect_apk(apk))
    monkeypatch.setattr(android, "Runner", lambda *_: engine.runner)
    monkeypatch.setattr(android.Waydroid, "privileged", lambda *a, **kw: "Success\n")
    with pytest.raises(RuntimeError, match="confirm"):
        engine.cleanup(tx)
    assert engine.store.transactions()[0].status == "cleanup_failed"
    assert engine.store.launcher(tx, tx.launchers[0]).exists()

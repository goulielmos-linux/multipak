import json
import os
import subprocess
import threading
import zipfile

import pytest
from test_android import FakeRunner, initialization_args, make_apk

from multipak import android, android_bundle
from multipak.android_apk import inspect_apk
from multipak.android_bundle import extract_parts, inspect_android, select_parts
from multipak.engine import Engine
from multipak.metadata import inspect_package
from multipak.process import Cancelled


def bundle(tmp_path, *, additions=(), base_options=None):
    path = tmp_path / "application.apkm"
    base = make_apk(tmp_path / "base.apk", **(base_options or {}))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(base, "base.apk")
        for index, (member, options) in enumerate(additions):
            part = make_apk(tmp_path / f"split-{index}.apk", **options)
            archive.writestr(member, part.read_bytes())
        archive.writestr("info.json", '{"app_name":"untrusted label"}')
    return path


def multi_bundle(tmp_path):
    return bundle(
        tmp_path,
        additions=(
            (
                "split.x86.apk",
                {"split": "config.x86", "abis": ("x86",), "split_types": "base__abi"},
            ),
            (
                "split.arm64.apk",
                {"split": "config.arm64_v8a", "abis": ("arm64-v8a",), "split_types": "base__abi"},
            ),
            ("split.hdpi.apk", {"split": "config.hdpi", "split_types": "base__density"}),
            ("split.xxhdpi.apk", {"split": "config.xxhdpi", "split_types": "base__density"}),
            ("assets.apk", {"split": "UnityDataAssetPack", "feature": True}),
            ("locale.apk", {"split": "config.pt"}),
        ),
        base_options={"required_types": "base__abi,base__density"},
    )


def test_bundle_is_identified_by_contents_and_selects_parts_without_mixing_abis(tmp_path):
    path = multi_bundle(tmp_path).rename(tmp_path / "renamed.rpm")
    package = inspect_package(path, None)
    assert package.name == "Demo Android"  # info.json cannot override manifest identity.
    assert package.kind == "apk" and package.architecture == "arm64-v8a,x86"
    selected = select_parts(package, ["x86_64", "x86"], 420)
    assert {p.split_id for p in selected} == {
        "",
        "config.x86",
        "config.xxhdpi",
        "UnityDataAssetPack",
        "config.pt",
    }
    target = tmp_path / "unpacked"
    target.mkdir()
    files = extract_parts(path, selected, target)
    assert len(files) == 5
    assert all(p.parent == target for p in files)
    assert not any("arm64" in p.member for p in selected)
    assert select_parts(package, ["arm64-v8a", "x86"], 240)[-1].split_id != "config.x86"


def test_single_apk_behavior_and_required_base_rejection(tmp_path):
    path = make_apk(tmp_path / "demo.apk")
    assert inspect_android(path) == inspect_apk(path)
    path = make_apk(path, required_types="base__density")
    with pytest.raises(ValueError, match="complete APK"):
        inspect_android(path)


@pytest.mark.parametrize("options", [{"package_id": "org.other.app"}, {"code": 25}])
def test_mixed_package_id_or_version_is_rejected(tmp_path, options):
    path = bundle(tmp_path, additions=(("other.apk", options | {"split": "config.pt"}),))
    with pytest.raises(ValueError, match="different applications or versions"):
        inspect_android(path)


@pytest.mark.parametrize("member", ["../escape.apk", "/absolute.apk", "folder\\escape.apk"])
def test_unsafe_archive_paths_are_rejected(tmp_path, member):
    with pytest.raises(ValueError, match="Invalid.*bundle APK"):
        inspect_android(bundle(tmp_path, additions=((member, {"split": "config.pt"}),)))
    assert not (tmp_path.parent / "escape.apk").exists()


def test_missing_base_duplicate_splits_and_size_limits(tmp_path, monkeypatch):
    path = bundle(tmp_path, additions=(("another-base.apk", {}),))
    with pytest.raises(ValueError, match="Duplicate base"):
        inspect_android(path)
    path = bundle(
        tmp_path,
        additions=(
            ("one.apk", {"split": "config.pt"}),
            ("two.apk", {"split": "config.pt"}),
        ),
    )
    with pytest.raises(ValueError, match="duplicate split"):
        inspect_android(path)
    monkeypatch.setattr(android_bundle, "MAX_APK_SIZE", 10)
    with pytest.raises(ValueError, match="oversized"):
        inspect_android(path)


def test_missing_required_split_types_and_dependencies_are_rejected(tmp_path):
    path = bundle(tmp_path, base_options={"required_types": "base__abi"})
    with pytest.raises(ValueError, match="missing required parts"):
        select_parts(inspect_android(path), ["x86"], 320)
    path = bundle(
        tmp_path,
        additions=(
            (
                "feature.apk",
                {
                    "split": "game",
                    "feature": True,
                    "dependency": "missing_feature",
                },
            ),
        ),
    )
    with pytest.raises(ValueError, match="missing required parts"):
        inspect_android(path)


def test_feature_config_groups_and_dependency_selection(tmp_path):
    path = bundle(
        tmp_path,
        additions=(
            ("feature.apk", {"split": "game", "feature": True}),
            ("hdpi.apk", {"split": "game.config.hdpi", "target": "game"}),
            ("xxhdpi.apk", {"split": "game.config.xxhdpi", "target": "game"}),
            ("other.apk", {"split": "other", "feature": True, "dependency": "game"}),
        ),
    )
    selected = select_parts(inspect_android(path), ["x86"], 320)
    assert {p.split_id for p in selected} == {"", "game", "game.config.hdpi", "other"}


def test_incompatible_architecture_and_tampered_parts_are_rejected(tmp_path):
    path = multi_bundle(tmp_path)
    package = inspect_android(path)
    with pytest.raises(ValueError, match="no compatible Android architecture"):
        select_parts(package, ["riscv64"], 320)
    selected = select_parts(package, ["x86"], 320)
    # A changed payload with unchanged manifest identity must still be rejected.
    with zipfile.ZipFile(path) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    payloads["base.apk"] += b"changed"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
    target = tmp_path / "parts"
    target.mkdir()
    with pytest.raises(ValueError, match="size limit|has changed"):
        extract_parts(path, selected, target)


def test_inspection_can_be_cancelled(tmp_path):
    path = multi_bundle(tmp_path)
    with pytest.raises(Cancelled):
        inspect_android(path, checkpoint=lambda: (_ for _ in ()).throw(Cancelled))


class BundleRunner(FakeRunner):
    def run(self, argv, **kwargs):
        argv = initialization_args(argv)
        if argv[:5] == ["pkexec", android.WAYDROID, "shell", "--", "wm"]:
            self.calls.append(argv)
            return "Physical density: 320\nOverride density: 480\n"
        if (
            argv[:6] == ["pkexec", android.WAYDROID, "shell", "--", "sh", "-c"]
            and android.SPLIT_INSTALL in argv
        ):
            self.calls.append(argv)
            if self.fail_install:
                return "Failure [INSTALL_FAILED_INVALID_APK]\n"
            self.installed.add("org.example.demo")
            return "Success\nMULTIPAK_SPLITS_INSTALLED\n"
        return super().run(argv, **kwargs)


@pytest.fixture
def setup_bundle(store, tmp_path, monkeypatch):
    monkeypatch.setattr(android.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(android.host, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        android.host, "directories", lambda: (tmp_path / "data", tmp_path / "config")
    )
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    engine = Engine(store, threading.Event())
    engine.runner = BundleRunner()
    monkeypatch.setattr(android, "Runner", lambda *_: engine.runner)
    return engine, multi_bundle(tmp_path)


def test_bundle_install_uses_one_atomic_session_and_normal_launcher_and_removal(setup_bundle):
    engine, path = setup_bundle
    tx = engine.install_android(inspect_android(path))
    assert tx.status == "installed" and tx.android_package_owned
    calls = [a for a in engine.runner.calls if "multipak-splits" in a]
    assert len(calls) == 1 and "app" not in calls[0]
    assert len(calls[0][9:]) == 10  # five (size, generated path) pairs.
    assert all(p.startswith("/data/waydroid_tmp/multipak-") for p in calls[0][10::2])
    assert not list((android.host.directories()[0] / "waydroid/data/waydroid_tmp").iterdir())
    assert engine.store.launcher(tx, tx.launchers[0]).exists()
    engine.cleanup(tx, preserve_environment=True)
    assert tx.android_package_id not in engine.runner.installed


def test_failed_bundle_install_keeps_snapshot_for_retry_and_cleans_temporary_files(setup_bundle):
    engine, path = setup_bundle
    engine.runner.fail_install = True
    with pytest.raises(RuntimeError, match="could not install the bundle"):
        engine.install_android(inspect_android(path))
    assert not engine.runner.installed
    assert not list((android.host.directories()[0] / "waydroid/data/waydroid_tmp").iterdir())
    engine.runner.fail_install = False
    assert engine.retry(engine.tx).status == "installed"


@pytest.mark.parametrize("fail_at", ["", "install-write", "install-commit", "create-output"])
def test_android_session_script_commits_all_parts_or_abandons(tmp_path, fail_at):
    # Execute the real Android-side script against a harmless fake pm, never Waydroid.
    log = tmp_path / "calls.jsonl"
    pm = tmp_path / "pm"
    pm.write_text(
        "#!/usr/bin/env python3\n"
        + "\n".join(
            [
                "import json, os, sys",
                "with open(os.environ['PM_LOG'], 'a') as f:",
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')",
                "command = sys.argv[1]",
                "fail = os.environ['PM_FAIL']",
                "if fail == command: print('Failure [INVALID_APK]'); sys.exit(1)",
                "if command == 'install-create':",
                "    output = 'Success: created install session [42]'",
                "    print('unexpected output' if fail == 'create-output' else output)",
                "else: print('Success')",
            ]
        )
    )
    pm.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/sh",
            "-c",
            android.SPLIT_INSTALL,
            "multipak-splits",
            "30",
            "10",
            "/data/waydroid_tmp/multipak-test/part-0.apk",
            "20",
            "/data/waydroid_tmp/multipak-test/part-1.apk",
        ],
        env=os.environ
        | {
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PM_LOG": str(log),
            "PM_FAIL": fail_at,
        },
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if not fail_at:
        assert result.returncode == 0 and "MULTIPAK_SPLITS_INSTALLED" in result.stdout
        assert [a[0] for a in calls] == [
            "install-create",
            "install-write",
            "install-write",
            "install-commit",
        ]
    else:
        assert result.returncode != 0 and "MULTIPAK_SPLITS_INSTALLED" not in result.stdout
        if fail_at != "create-output":
            assert calls[-1] == ["install-abandon", "42"]

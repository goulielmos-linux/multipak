"""Exercise APKS variant selection, XAPK data installation and menu integration."""

import hashlib
import json
import subprocess
import zipfile
from xml.etree import ElementTree

import pytest
from test_android import make_apk
from test_android_bundle import bundle, multi_bundle
from test_android_bundle import setup_bundle as setup_bundle

from multipak.android_apks import fields
from multipak.android_bundle import (
    extract_expansions,
    inspect_android,
    select_parts,
    select_variant,
)
from multipak.android_expansions import COPY_OBB
from multipak.android_menu import register_shortcut


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def pb(key, value):
    if isinstance(value, int):
        return varint(key << 3) + varint(value)
    return varint((key << 3) | 2) + varint(len(value)) + value


def sdk_target(minimum, alternatives=()):
    return pb(1, pb(1, pb(1, minimum))) + b"".join(
        pb(2, pb(1, pb(1, value))) for value in alternatives
    )


def apks_fixture(tmp_path):
    first = make_apk(tmp_path / "first.apk", sdk=21)
    second = make_apk(tmp_path / "second.apk", sdk=30)
    second_split = make_apk(tmp_path / "locale.apk", sdk=30, split="config.pt")
    # A standalone fallback and a newer split variant have independent base APKs.
    variant1 = pb(1, pb(1, sdk_target(21, [30]))) + pb(3, 1)
    variant1 += pb(2, pb(2, pb(2, b"standalone/app.apk") + pb(4, b"")))
    variant2 = pb(1, pb(1, sdk_target(30, [21]))) + pb(3, 2)
    variant2 += pb(
        2,
        pb(2, pb(2, b"splits/base.apk") + pb(3, b"")) + pb(2, pb(2, b"splits/pt.apk") + pb(3, b"")),
    )
    path = tmp_path / "app.apks"
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(first, "standalone/app.apk")
        archive.write(second, "splits/base.apk")
        archive.write(second_split, "splits/pt.apk")
        archive.writestr("toc.pb", pb(1, variant1) + pb(1, variant2))
    return path


@pytest.mark.parametrize("extension", ["apkm", "apks", "xapk"])
def test_split_sets_work_with_all_registered_bundle_formats(tmp_path, extension):
    package = inspect_android(multi_bundle(tmp_path).rename(tmp_path / ("app." + extension)))
    assert package.kind == "apk" and package.android_parts
    assert {part.split_id for part in select_parts(package, ["x86"], 320)} == {
        "",
        "config.x86",
        "config.hdpi",
        "config.pt",
        "UnityDataAssetPack",
    }


def test_bundletool_variants_are_selected_for_actual_android_without_mixing(tmp_path):
    package = inspect_android(apks_fixture(tmp_path))
    assert package.android_min_sdk == 21 and len(package.android_variants) == 2
    old = select_variant(package, 28, ["x86"], 320)
    new = select_variant(package, 33, ["x86"], 320)
    assert [p.member for p in old.android_parts] == ["standalone/app.apk"]
    assert {p.member for p in new.android_parts} == {"splits/base.apk", "splits/pt.apk"}
    with pytest.raises(ValueError, match="no variant compatible"):
        select_variant(package, 19, ["x86"], 320)


def test_bundletool_index_cannot_reference_missing_or_unsafe_files(tmp_path):
    path = apks_fixture(tmp_path)
    with zipfile.ZipFile(path) as archive:
        payloads = {n: archive.read(n) for n in archive.namelist()}
    payloads["toc.pb"] = payloads["toc.pb"].replace(b"splits/base.apk", b"../badbasee.apk")
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in payloads.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match="missing APKs|Invalid protobuf"):
        inspect_android(path)


@pytest.mark.parametrize("raw", [b"\x80", b"\x0a\x7fabc", b"\x00", b"\x0b"])
def test_malformed_protobuf_is_rejected(raw):
    with pytest.raises(ValueError):
        fields(raw)


def test_xapk_obb_identity_digest_and_safe_extraction(tmp_path):
    path = bundle(tmp_path).rename(tmp_path / "app.xapk")
    member = "Android/obb/org.example.demo/main.24.org.example.demo.obb"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(member, b"game data")
    package = inspect_android(path)
    assert package.android_expansions[0].sha256 == hashlib.sha256(b"game data").hexdigest()
    directory = tmp_path / "stage"
    directory.mkdir()
    extracted = extract_expansions(path, package.android_expansions, directory)
    assert extracted[0].parent == directory and extracted[0].read_bytes() == b"game data"


@pytest.mark.parametrize(
    "member",
    [
        "../main.24.org.example.demo.obb",
        "Android/obb/org.other/main.24.org.other.obb",
        "main.24.org.other.obb",
    ],
)
def test_xapk_obb_cannot_escape_or_write_another_app_data(tmp_path, member):
    path = bundle(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(member, b"data")
    with pytest.raises(ValueError, match="OBB|Invalid"):
        inspect_android(path)


def test_obb_copy_is_idempotent_and_preserves_conflicting_existing_data(tmp_path):
    root = tmp_path / "Android"
    source = tmp_path / "payload"
    source.write_bytes(b"verified game data")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    script = COPY_OBB.replace("root=/sdcard/Android", 'root="' + str(root) + '"')
    name = "main.24.org.example.demo.obb"
    command = ["sh", "-c", script, "test", "org.example.demo", str(source), name, digest]
    for _ in range(2):
        result = subprocess.run(command, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert "MULTIPAK_OBB_INSTALLED" in result.stdout
    target = root / "obb/org.example.demo" / name
    target.write_bytes(b"personal existing data")
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert target.read_bytes() == b"personal existing data"
    assert not target.with_suffix(".obb.multipak-tmp").exists()


def test_android_menu_groups_existing_and_new_shortcuts_without_editing_them(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    applications = tmp_path / "applications"
    applications.mkdir()
    for name in ("waydroid.org.example.demo.desktop", "multipak-new.desktop"):
        (applications / name).write_text("[Desktop Entry]\nName=Test\nCategories=Utility;\n")
        register_shortcut(applications, name)
    merged = tmp_path / "config/menus/applications-merged/multipak-android.menu"
    root = ElementTree.fromstring(merged.read_text())
    assert {e.text for e in root.findall(".//Filename")} == {
        "waydroid.org.example.demo.desktop",
        "multipak-new.desktop",
    }
    assert (
        (applications / "waydroid.org.example.demo.desktop")
        .read_text()
        .endswith("Categories=Utility;\n")
    )
    assert len(json.loads((tmp_path / "data/multipak/android-shortcuts.json").read_text())) == 2


def test_xapk_installation_copies_data_before_success_and_retries_without_reinstall(
    setup_bundle, tmp_path, monkeypatch
):
    from multipak import android_expansions

    engine, _ = setup_bundle
    path = bundle(tmp_path).rename(tmp_path / "game.xapk")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("main.24.org.example.demo.obb", b"game data")
    copied = []

    def copy(waydroid, package, staged):
        assert package.desktop_entry in waydroid.apps()
        copied.append(package.android_expansions[0].split_id)
        if len(copied) == 1:
            raise RuntimeError("temporary storage failure")

    monkeypatch.setattr(android_expansions, "install_expansions", copy)
    with pytest.raises(RuntimeError, match="temporary storage failure"):
        engine.install_android(inspect_android(path))
    transaction = engine.store.transactions()[0]
    assert transaction.status == "failed" and transaction.android_package_owned
    calls_before = len([c for c in engine.runner.calls if "multipak-splits" in c])
    result = engine.retry(transaction)
    assert result.status == "installed" and len(copied) == 2
    assert len([c for c in engine.runner.calls if "multipak-splits" in c]) == calls_before


def test_target_android_is_read_separately_from_minimum(tmp_path):
    package = inspect_android(make_apk(tmp_path / "app.apk", sdk=23, target_sdk=33))
    assert package.android_min_sdk == 23 and package.android_target_sdk == 33


def test_bundletool_empty_and_sdk_only_module_targeting():
    from multipak.android_apks import groups, matches

    description = pb(2, b"base.apk") + pb(3, b"")
    for target, expected in ((b"", True), (pb(1, sdk_target(33)), False)):
        metadata = pb(1, b"base") + pb(5, target)
        apk_set = pb(1, metadata) + pb(2, description)
        parsed = groups(pb(1, pb(2, apk_set)))
        selection = parsed[0]["entries"][0]["module_target"]
        assert matches(selection, 30, ["x86"], 320) is expected
        assert matches(selection, 33, ["x86"], 320)

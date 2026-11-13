import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from multipak.android import _launcher
from multipak.android_menu import migrate_installed
from multipak.models import Package, Transaction


def transaction(store):
    tx = Transaction(
        "a" * 32,
        "Android",
        "",
        "com.whatsapp",
        "1",
        "apk",
        "",
        method="waydroid",
        status="installed",
        android_instance_id="androida",
        android_package_id="com.whatsapp",
        android_environment_label="Android A",
    )
    store.save(tx)
    return tx


def exported(instance="androida", label="WhatsApp"):
    return (
        "[Desktop Entry]\nType=Application\n"
        f"Name={label}\nExec=/usr/local/bin/multipak-waydroid --instance {instance} "
        "app launch com.whatsapp\nIcon=whatsapp\nCategories=X-WayDroid-App;\n"
    )


@pytest.mark.parametrize("during_write", [False, True])
def test_async_android_export_uses_one_filename_and_preserves_real_name(
    store, monkeypatch, during_write
):
    tx = transaction(store)
    name = "multipak-waydroid.androida.com.whatsapp.desktop"
    path = store.launcher(tx, name)
    engine = SimpleNamespace(store=store)
    package = Package(Path("app.apk"), "apk", "com.whatsapp", "1", "x86")
    if during_write:
        link = os.link

        def export_then_link(source, destination):
            path.write_text(exported())
            return link(source, destination)

        monkeypatch.setattr(os, "link", export_then_link)
    _launcher(engine, tx, package)
    if not during_write:
        path.write_text(exported())
    _launcher(engine, tx, package)
    assert list(store.applications.glob("*.desktop")) == [path]
    assert path.read_text() == exported()
    assert store.transactions()[0].launchers == [name]
    assert not list(store.applications.glob("*.tmp"))


@pytest.mark.parametrize("already_exported", [False, True])
def test_migration_reconciles_legacy_shortcuts_and_keeps_other_instances(store, already_exported):
    tx = transaction(store)
    legacy_name = f"multipak-{tx.id}-android.desktop"
    legacy = store.launcher(tx, legacy_name)
    legacy.write_text(exported(label="com.whatsapp"))
    tx.launchers = [legacy_name]
    store.save(tx)
    canonical = store.applications / "multipak-waydroid.androida.com.whatsapp.desktop"
    if already_exported:
        canonical.write_text(exported())
    other = store.applications / "multipak-waydroid.androidb.com.whatsapp.desktop"
    other.write_text(exported("androidb"))
    personal = store.applications / "waydroid.com.whatsapp.desktop"
    personal.write_text("personal environment unchanged")
    for _ in range(2):
        migrate_installed(store)
    assert not legacy.exists()
    assert canonical.read_text() == exported(
        label="WhatsApp" if already_exported else "com.whatsapp"
    )
    assert other.read_text() == exported("androidb")
    assert personal.read_text() == "personal environment unchanged"
    assert store.transactions()[0].launchers == [canonical.name]
    assert (store.directory(tx.id) / "legacy-android-launcher.desktop.bak").is_file()
    config = Path(os.environ["XDG_CONFIG_HOME"])
    menu = ElementTree.fromstring(
        (config / "menus/plasma-applications-merged/multipak-android.menu").read_text()
    )
    waydroid = next(m for m in menu.findall("Menu") if m.findtext("Name") == "Waydroid")
    assert [f.text for f in waydroid.findall("Exclude/Filename")] == [canonical.name]


def test_owned_launcher_permission_does_not_extend_to_other_instances_or_packages(store):
    tx = transaction(store)
    for name in (
        "multipak-waydroid.androidb.com.whatsapp.desktop",
        "multipak-waydroid.androida.com.instagram.android.desktop",
        "waydroid.com.whatsapp.desktop",
        "../outside.desktop",
    ):
        with pytest.raises(ValueError):
            store.launcher(tx, name)
    with pytest.raises(ValueError):
        store.launcher(
            replace(tx, method="distrobox"), "multipak-waydroid.androida.com.whatsapp.desktop"
        )

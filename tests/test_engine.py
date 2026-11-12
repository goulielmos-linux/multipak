import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from multipak.engine import OWNER_LABEL
from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS, Package
from multipak.process import CommandError

DEBIAN = next(e for e in ENVIRONMENTS if e.id == "debian-stable")


def package_for(deb, engine):
    return inspect_package(deb, engine.runner)


def test_install_verifies_package_then_exports_then_reports_success(fake_engine, deb):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    assert tx.status == "installed"
    assert tx.container_id == "container123"
    assert tx.image_id == "sha256:image123"
    assert len(tx.sha256) == 64
    assert engine.test_progress[-1][0] == 100
    assert len(tx.launchers) == 1
    assert engine.store.launcher(tx, tx.launchers[0]).is_file()
    assert not (engine.store.directory(tx.id) / "package.deb").exists()
    # Shell metacharacters in the original filename never reach a shell command string.
    assert all(isinstance(argv, list) for argv in engine.runner.calls)
    manifest = json.loads((engine.store.directory(tx.id) / "manifest.json").read_text())
    assert manifest["status"] == "installed"


def test_deb_downloads_and_installs_local_file_in_one_apt_transaction(fake_engine, deb):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    calls = [call for call in engine.runner.calls if "apt-get" in call]
    assert len(calls) == 1
    assert calls[0][-2:] == ["install", f"/tmp/multipak-{tx.id}/package.deb"]
    assert "--download-only" not in calls[0]
    assert "--no-download" not in calls[0]


def test_deb_keeps_maintainer_script_stdout_open(fake_engine, deb, tmp_path):
    engine = fake_engine
    engine.install(package_for(deb, engine), DEBIAN)
    apt = next(call for call in engine.runner.calls if "apt-get" in call)
    config = tmp_path / "package-config"
    # Reproduce APT's StartDpkg FD_CLOEXEC behavior without installing a package.
    # GNU sed -i checks stdout on exit even when editing a file in place.
    launch = """
import os, sys
if 'APT::Status-Fd=1' in sys.argv:
    os.set_inheritable(1, False)
os.execvp('sh', ['sh', '-ec', 'sed -i s/before/after/ "$1"; printf configured',
                 'postinst', sys.argv[1]])
"""

    def configure(arguments):
        config.write_text("before\n")
        return subprocess.run(
            [sys.executable, "-c", launch, str(config), *arguments],
            capture_output=True,
            text=True,
        )

    broken = configure(["APT::Status-Fd=1"])
    assert broken.returncode != 0
    assert "Bad file descriptor" in broken.stderr
    fixed = configure(apt)
    assert fixed.returncode == 0, fixed.stderr
    assert fixed.stdout == "configured"
    assert config.read_text() == "after\n"


@pytest.mark.parametrize(
    ("error", "recovers"),
    [
        ("E: Failed to fetch https://example.org/package.deb", True),
        ("Setting up demo-app\ndpkg: error processing package demo-app", False),
        ("Setting up demo-app\npostinst: Failed to fetch configuration", False),
        ("E: Internal Error, Pathname to install is not absolute", False),
    ],
)
def test_deb_refreshes_indexes_only_before_package_configuration(
    fake_engine, deb, monkeypatch, error, recovers
):
    engine = fake_engine
    run = engine.runner.run
    failed = False

    def fail_once(argv, **kwargs):
        nonlocal failed
        if "apt-get" in argv and "install" in argv and not failed:
            failed = True
            engine.runner.calls.append(argv)
            raise CommandError(argv, 100, error)
        return run(argv, **kwargs)

    monkeypatch.setattr(engine.runner, "run", fail_once)
    if recovers:
        assert engine.install(package_for(deb, engine), DEBIAN).status == "installed"
    else:
        with pytest.raises(CommandError):
            engine.install(package_for(deb, engine), DEBIAN)
    calls = [call for call in engine.runner.calls if "apt-get" in call]
    assert ["update" if "update" in call else "install" for call in calls] == (
        ["install", "update", "install"] if recovers else ["install"]
    )


@pytest.mark.parametrize("custom_name", ["", "My-Tests"])
def test_new_containers_override_multiline_image_description(fake_engine, deb, custom_name):
    engine = fake_engine
    engine.install(package_for(deb, engine), DEBIAN, container_name=custom_name)
    labels = engine.runner.container["Config"]["Labels"]
    assert labels["org.opencontainers.image.description"] == "MultiPak"
    assert labels[OWNER_LABEL] == engine.tx.id


def test_installed_distrobox_list_does_not_show_phantom_ubuntu(tmp_path, fake_engine, deb):
    # Feed simulated Podman output to the installed Distrobox list parser.
    candidates = (
        shutil.which("distrobox-list"),
        "/usr/bin/distrobox-list",
        "/run/host/usr/bin/distrobox-list",
    )
    script = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
    if script is None:
        pytest.skip("Requires the host Distrobox script for the compatibility regression")
    engine = fake_engine
    engine.install(package_for(deb, engine), DEBIAN)
    description = engine.runner.container["Config"]["Labels"][
        "org.opencontainers.image.description"
    ]
    podman = tmp_path / "podman"
    podman.write_text('#!/bin/sh\nprintf "%s\\n" "$MULTIPAK_TEST_LIST"\n')
    podman.chmod(0o700)

    def listing(value):
        row = (
            "abc123|docker.io/library/ubuntu:latest|MultiPak-Ubuntu-26.04|Up 1 second|"
            f"manager=distrobox,org.opencontainers.image.description={value}"
            ",org.opencontainers.image.title=ubuntu,/usr/bin/distrobox-export"
        )
        result = subprocess.run(
            ["/bin/sh", str(script), "--no-color"],
            env=os.environ | {"DBX_CONTAINER_MANAGER": str(podman), "MULTIPAK_TEST_LIST": row},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.splitlines()[1:]

    assert len(listing("The Ubuntu container image\nDescription continuation\n")) == 2
    rows = listing(description)
    assert len(rows) == 1
    assert "MultiPak-Ubuntu-26.04" in rows[0]


def test_network_failure_preserves_snapshot_for_retry(fake_engine, deb):
    engine = fake_engine
    engine.runner.fail_download = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    tx = engine.tx
    assert tx.status == "failed"
    assert (engine.store.directory(tx.id) / "package.deb").is_file()
    assert all(p < 100 for p, _ in engine.test_progress)
    engine.runner.fail_download = False
    assert engine.retry(tx).status == "installed"
    creates = [argv for argv in engine.runner.calls if argv[:2] == ["distrobox", "create"]]
    assert len(creates) == 1


def test_retry_after_failed_creation(fake_engine, deb):
    engine = fake_engine
    engine.runner.create_failure_after_pull = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    assert engine.tx.container_id == ""
    engine.runner.create_failure_after_pull = False
    assert engine.retry(engine.tx).status == "installed"


def test_retry_refuses_changed_snapshot(fake_engine, deb):
    engine = fake_engine
    engine.runner.fail_download = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    tx = engine.tx
    (engine.store.directory(tx.id) / "package.deb").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="has changed"):
        engine.retry(tx)


def test_cleanup_removes_own_container_launchers_home_and_new_image(fake_engine, deb, monkeypatch):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    (engine.environment_home / "user-file").write_text("data")
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert engine.runner.container is None
    assert not engine.runner.image_exists
    assert not engine.store.directory(tx.id).exists()
    assert not engine.store.launcher(tx, tx.launchers[0]).exists()


def test_cleanup_preserves_existing_images(fake_engine, deb, monkeypatch):
    engine = fake_engine
    engine.runner.image_exists = True
    tx = engine.install(package_for(deb, engine), DEBIAN)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert engine.runner.image_exists
    assert not any(argv[:3] == ["podman", "image", "rm"] for argv in engine.runner.calls)


def test_cleanup_after_pull_but_before_container_creation(fake_engine, deb, monkeypatch):
    engine = fake_engine
    engine.runner.create_failure_after_pull = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    tx = engine.tx
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert not engine.runner.image_exists


@pytest.mark.parametrize("failure", ["fail_remove", "fail_image_remove"])
def test_cleanup_failure_keeps_a_recoverable_journal(fake_engine, deb, monkeypatch, failure):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    setattr(engine.runner, failure, True)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    with pytest.raises(CommandError):
        engine.cleanup(tx)
    assert tx.status == "cleanup_failed"
    assert engine.store.directory(tx.id).exists()
    setattr(engine.runner, failure, False)
    engine.cleanup(tx)
    assert not engine.store.directory(tx.id).exists()


def test_cleanup_refuses_unowned_or_replaced_container(fake_engine, deb, monkeypatch):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    engine.runner.container["Config"]["Labels"][OWNER_LABEL] = "someone-else"
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    with pytest.raises(RuntimeError, match="does not belong"):
        engine.cleanup(tx)
    assert engine.runner.container is not None
    assert not any(argv[:2] == ["podman", "rm"] for argv in engine.runner.calls)


def test_store_refuses_out_of_scope_paths(store):
    with pytest.raises(ValueError):
        store.directory("../../outside")


def reuse_debian(engine):
    from multipak.models import ExistingEnvironment

    engine.runner.image_exists = True
    engine.runner.container = {
        "Name": "Debian-apps",
        "Id": "container123",
        "Image": "sha256:image123",
        "Config": {"Labels": {"manager": "distrobox"}},
    }
    return ExistingEnvironment(
        "Debian-apps",
        "container123",
        "debian",
        "13",
        "image123",
        (DEBIAN.image,),
    )


def test_reuse_never_creates_or_deletes_existing_environment(fake_engine, deb, monkeypatch):
    engine = fake_engine
    existing = reuse_debian(engine)
    tx = engine.install(package_for(deb, engine), DEBIAN, existing)
    assert tx.container == "Debian-apps"
    assert not tx.container_created
    assert tx.status == "installed"
    assert not any(call[:2] == ["distrobox", "create"] for call in engine.runner.calls)
    assert not any("--fix-broken" in call or "--configure" in call for call in engine.runner.calls)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert engine.runner.container["Name"] == "Debian-apps"
    assert engine.runner.image_exists
    assert not engine.runner.package_present
    assert not engine.store.directory(tx.id).exists()
    assert not any(call[:2] == ["podman", "rm"] for call in engine.runner.calls)
    assert not any("autoremove" in call for call in engine.runner.calls)


def test_existing_package_is_never_overwritten_or_removed(fake_engine, deb, monkeypatch):
    engine = fake_engine
    existing = reuse_debian(engine)
    engine.runner.package_present = True
    with pytest.raises(RuntimeError, match="already installed"):
        engine.install(package_for(deb, engine), DEBIAN, existing)
    assert not engine.tx.package_install_started
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(engine.tx)
    assert engine.runner.package_present
    assert not any("--remove" in call for call in engine.runner.calls)


def test_shared_environment_cleanup_waits_for_package_manager(fake_engine, deb, monkeypatch):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN, reuse_debian(engine))
    engine.runner.busy = True
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    with pytest.raises(RuntimeError, match="still running"):
        engine.cleanup(tx)
    assert tx.status == "cleanup_failed"
    assert engine.runner.package_present
    assert engine.store.directory(tx.id).is_dir()
    engine.runner.busy = False
    engine.cleanup(tx)


def test_original_environment_and_home_survive_other_app_reference(fake_engine, deb, monkeypatch):
    from dataclasses import replace

    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    home = engine.environment_home
    (home / "shared-data").write_text("keep me")
    other = replace(
        tx, id="a" * 32, package_name="another-app", container_created=False, launchers=[]
    )
    engine.store.save(other)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert (home / "shared-data").read_text() == "keep me"
    assert engine.store.directory(other.id).is_dir()
    assert engine.runner.container is not None


def test_temp_cleanup_failure_does_not_turn_verified_install_into_failure(fake_engine, deb):
    fake_engine.runner.fail_temp_cleanup = True
    tx = fake_engine.install(package_for(deb, fake_engine), DEBIAN)
    assert tx.status == "installed"
    assert tx.warnings
    assert tx.launchers
    assert fake_engine.test_progress[-1][0] == 100


def test_reuse_revalidates_distribution_before_mutating(fake_engine, deb):
    engine = fake_engine
    existing = reuse_debian(engine)
    engine.runner.release = 'ID=fedora\nVERSION_ID="44"\n'
    with pytest.raises(RuntimeError, match="has changed"):
        engine.install(package_for(deb, engine), DEBIAN, existing)
    assert not engine.tx.package_install_started
    assert not any("--run" in call for call in engine.runner.calls)


def test_retry_of_original_owner_uses_shared_transaction_rules(fake_engine, deb):
    from dataclasses import replace

    engine = fake_engine
    engine.runner.fail_download = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    tx = engine.tx
    engine.store.save(replace(tx, id="b" * 32, container_created=False, launchers=[]))
    engine.runner.fail_download = False
    engine.runner.calls.clear()
    engine.retry(tx)
    assert engine.runner.defer_cancellation
    assert any("--run" in call for call in engine.runner.calls)
    assert not any("--fix-broken" in call for call in engine.runner.calls)


def test_fedora_rpm_reuses_toolbox_without_creating_another_environment(
    fake_engine,
    tmp_path,
    monkeypatch,
):
    from multipak.models import ExistingEnvironment

    engine = fake_engine
    path = tmp_path / "chrome.rpm"
    path.write_bytes(b"test fixture")
    package = Package(path, "rpm", "google-chrome-stable", "157", "x86_64")
    reuse_debian(engine)
    engine.runner.container["Name"] = "Fedora-apps"
    engine.runner.release = 'ID=fedora\nVERSION_ID="44"\n'
    profile = next(e for e in ENVIRONMENTS if e.id == "fedora-44")
    existing = ExistingEnvironment("Fedora-apps", "container123", "fedora", "44")
    monkeypatch.setattr("multipak.engine.inspect_package", lambda *_: package)
    tx = engine.install(package, profile, existing)
    assert tx.container == "Fedora-apps"
    assert tx.status == "installed"
    commands = engine.runner.calls
    assert not any(call[:2] == ["distrobox", "create"] for call in commands)
    assert sum("dnf" in call and "--run" in call for call in commands) == 2


def test_only_one_mutating_operation_at_a_time(store):
    with store.lock(), pytest.raises(RuntimeError, match="already in progress"), store.lock():
        pass


def test_pre_cancelled_operation_creates_nothing(fake_engine, deb):
    from multipak.process import Cancelled, Runner

    fake_engine.cancelled.set()
    fake_engine.runner = Runner(fake_engine.cancelled, lambda _: None)
    with pytest.raises(Cancelled):
        fake_engine.install(inspect_package(deb, Runner(threading.Event(), lambda _: None)), DEBIAN)
    assert not fake_engine.store.transactions()


def test_new_container_has_readable_resolved_release_name(fake_engine, deb):
    tx = fake_engine.install(package_for(deb, fake_engine), DEBIAN)
    assert tx.container == "MultiPak-Debian-13"
    assert tx.environment_id == "debian-13"
    assert fake_engine.environment_home.name == "home"
    labels = fake_engine.runner.container["Config"]["Labels"]
    assert labels["org.opencontainers.image.description"] == "MultiPak"


def test_custom_container_name_survives_installation_and_retry(fake_engine, deb):
    engine = fake_engine
    engine.runner.fail_download = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN, container_name="My-Tests")
    tx = engine.tx
    assert tx.container == "My-Tests"
    assert tx.custom_container_name
    engine.runner.fail_download = False
    engine.retry(tx)
    assert engine.tx.container == "My-Tests"
    creates = [call for call in engine.runner.calls if call[:2] == ["distrobox", "create"]]
    assert len(creates) == 1
    assert (engine.environment_home).is_dir()


def test_custom_name_collision_preserves_existing_container(fake_engine, deb):
    engine = fake_engine
    existing = reuse_debian(engine)
    with pytest.raises(ValueError, match="already exists"):
        engine.install(package_for(deb, engine), DEBIAN, container_name=existing.name)
    assert engine.runner.container["Name"] == existing.name
    assert not any(call[:2] == ["distrobox", "create"] for call in engine.runner.calls)


@pytest.mark.parametrize("name", ["../other", "/tmp/other", "with spaces", "", "a" * 64])
def test_invalid_custom_names_are_rejected(name):
    from multipak.models import valid_environment_name

    assert not valid_environment_name(name)


def test_new_environment_registers_container_icon_and_cleanup_removes_shortcut(
    fake_engine, deb, monkeypatch
):
    import configparser

    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    shortcut = engine.store.applications / f"{tx.container}.desktop"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(shortcut)
    assert parser["Desktop Entry"]["NoDisplay"] == "true"
    assert parser["Desktop Entry"]["X-MultiPak-ContainerID"] == tx.container_id
    from pathlib import Path

    icon = Path(parser["Desktop Entry"]["Icon"])
    assert icon.read_bytes() == Path("src/multipak/kirigami/assets/container.svg").read_bytes()
    assert parser["Desktop Entry"]["Name"] == "MultiPak - Debian 13"
    assert icon.parent.name == "distrobox"
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert not shortcut.exists()


def test_reusing_personal_environment_does_not_replace_icon(fake_engine, deb):
    engine = fake_engine
    existing = reuse_debian(engine)
    shortcut = engine.store.applications / f"{existing.name}.desktop"
    original = "[Desktop Entry]\nIcon=personal-icon\n"
    shortcut.write_text(original)
    engine.install(package_for(deb, engine), DEBIAN, existing)
    assert shortcut.read_text() == original


def test_reuse_revalidates_resolved_stable_version_before_installing(fake_engine, deb):
    engine = fake_engine
    existing = reuse_debian(engine)
    engine.runner.release = 'ID=debian\nVERSION_ID="12"\n'
    with pytest.raises(RuntimeError, match="distribution or version has changed"):
        engine.install(package_for(deb, engine), DEBIAN, existing)
    assert not any("--download-only" in call for call in engine.runner.calls)


def test_explicit_new_container_creates_numbered_container_without_removing_default(
    fake_engine, deb
):
    engine = fake_engine
    existing = reuse_debian(engine)
    engine.runner.container["Name"] = "MultiPak-Debian-13"
    tx = engine.install(package_for(deb, engine), DEBIAN, force_new_environment=True)
    assert tx.container == "C2-MultiPak-Debian-13"
    assert tx.container_created
    assert not any(call[:2] == ["podman", "rm"] for call in engine.runner.calls)
    assert existing.container_id


def test_default_container_is_rechecked_before_creating_another(fake_engine, deb):
    from multipak.environments import SHARED_LABEL

    engine = fake_engine
    reuse_debian(engine)
    engine.runner.container["Name"] = "MultiPak-Debian-13"
    engine.runner.container["Config"]["Labels"][SHARED_LABEL] = "1"
    tx = engine.install(package_for(deb, engine), DEBIAN)
    assert tx.container == "MultiPak-Debian-13"
    assert not tx.container_created
    assert not any(call[:2] == ["distrobox", "create"] for call in engine.runner.calls)


def test_custom_named_container_keeps_kontainer_default_icon(fake_engine, deb):
    from multipak.environments import CUSTOM_LABEL, discover

    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN, container_name="Test-Debian")
    assert not (engine.store.applications / f"{tx.container}.desktop").exists()
    assert engine.runner.container["Config"]["Labels"][CUSTOM_LABEL] == "1"
    found = discover(engine.runner)
    assert found[0].custom_named
    assert not found[0].multipak_managed


def test_manager_uninstall_last_program_preserves_environment_and_home(
    fake_engine, deb, monkeypatch
):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    home = engine.environment_home
    (home / "personal-file").write_text("keep")
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx, preserve_environment=True)
    assert engine.runner.container is not None
    assert engine.runner.image_exists
    assert not engine.runner.package_present
    assert (home / "personal-file").read_text() == "keep"
    assert not engine.store.transactions()
    assert not engine.store.launcher(tx, tx.launchers[0]).exists()


def test_failed_manager_uninstall_keeps_preservation_on_retry(fake_engine, deb, monkeypatch):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.runner.busy = True
    with pytest.raises(RuntimeError, match="still running"):
        engine.cleanup(tx, preserve_environment=True)
    persisted = engine.store.transactions()[0]
    assert persisted.preserve_environment_on_cleanup
    engine.runner.busy = False
    engine.cleanup(persisted)
    assert engine.runner.container is not None
    assert not engine.runner.package_present


@pytest.mark.parametrize("name", ["MultiPak-Test", "multipak-app", "C2-MultiPak-Debian-13"])
def test_custom_creation_cannot_use_the_managed_namespace(fake_engine, deb, name):
    engine = fake_engine
    with pytest.raises(ValueError, match="reserved"):
        engine.install(package_for(deb, engine), DEBIAN, container_name=name)
    assert not any(call[:2] == ["distrobox", "create"] for call in engine.runner.calls)
    assert not engine.store.transactions()


def test_image_download_reports_progress_before_version_lookup_and_container_creation(
    fake_engine, deb
):
    engine = fake_engine
    engine.install(package_for(deb, engine), DEBIAN)
    calls = engine.runner.calls
    pull = ["podman", "pull", "--policy=missing", DEBIAN.image]
    fetch = calls.index(pull)
    resolve = next(i for i, call in enumerate(calls) if call[:2] == ["podman", "run"])
    create = next(i for i, call in enumerate(calls) if call[:2] == ["distrobox", "create"])
    assert fetch < resolve < create
    assert any(percent == 50 and "part 1" in text for percent, text in engine.test_progress)
    assert (-1, "Initializing the container…") in engine.test_progress


def test_rpm_reports_download_progress_during_setup_and_install(fake_engine, tmp_path):
    engine = fake_engine
    fedora = next(env for env in ENVIRONMENTS if env.id == "fedora-latest")
    engine.runner.release = 'ID=fedora\nVERSION_ID="45"\n'
    package_path = tmp_path / "demo.rpm"
    package_path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")
    package = Package(package_path, "rpm", "demo", "1-1.fc45", "x86_64")
    run = engine.runner.run

    def emit_download(argv, **kwargs):
        if argv[0] == "rpm":
            return b"demo\x1f1-1.fc45\x1fx86_64" if kwargs.get("binary") else ""
        if callback := kwargs.get("on_output"):
            if argv[0] == "distrobox" and argv[-1] == "true":
                callback("\x1b[0mInstalling basic packages...\n")
                callback("[1/3] basic-1.noarch  25% | 1.0 MiB/s | 500 KiB\r")
            if "--downloadonly" in argv:
                callback("[1/2] dependency-1.noarch  50% | 1.0 MiB/s | 1.0 MiB\r")
        return run(argv, **kwargs)

    engine.runner.run = emit_download
    assert engine.install(package, fedora).status == "installed"
    progress = engine.test_progress
    assert (-1, "Preparing the container's basic components…") in progress
    assert (25, "Downloading basic-1.noarch — 25%") in progress
    assert (50, "Downloading dependency-1.noarch — 50%") in progress


def test_failed_image_download_does_not_create_or_initialize_a_container(fake_engine, deb):
    engine = fake_engine
    run = engine.runner.run

    def fail_pull(argv, **kwargs):
        if argv[:2] == ["podman", "pull"]:
            raise CommandError(argv, 125, "download interrupted")
        return run(argv, **kwargs)

    engine.runner.run = fail_pull
    with pytest.raises(CommandError, match="download interrupted"):
        engine.install(package_for(deb, engine), DEBIAN)
    assert not any(call[:2] == ["distrobox", "create"] for call in engine.runner.calls)
    assert not any(percent == 100 for percent, _ in engine.test_progress)

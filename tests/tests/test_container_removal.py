"""Optional container removal uses only a simulated Podman and temporary files."""

from dataclasses import replace

import pytest

from multipak.containers import remember_container, remove_container, uninstall_application
from multipak.environments import OWNER_LABEL
from multipak.models import Transaction
from multipak.process import CommandError


@pytest.fixture
def installed(fake_engine, monkeypatch):
    engine = fake_engine
    runner = engine.runner
    tx = Transaction(
        "a" * 32,
        "My-apps",
        "image",
        "demo",
        "1",
        "deb",
        "hash",
        status="installed",
        container_id="c" * 64,
        container_created=True,
        custom_container_name=True,
        managed_home=True,
        package_install_started=True,
    )
    engine.store.save(tx)
    runner.container = {
        "Id": tx.container_id,
        "Name": tx.container,
        "Image": "sha256:image123",
        "Config": {"Labels": {"manager": "distrobox", OWNER_LABEL: tx.id}},
    }
    runner.package_present = runner.image_exists = True
    original_run = runner.run

    def run(argv, **kwargs):
        if argv[:2] == ["podman", "ps"]:
            import json

            runner.calls.append(argv)
            return json.dumps(
                [{"Id": tx.container_id, "Names": [tx.container]}] if runner.container else []
            )
        if argv[:3] == ["podman", "container", "exists"]:
            runner.calls.append(argv)
            if runner.container is None:
                raise CommandError(argv, 1, "")
            assert argv[-1] == runner.container["Id"]
            return ""
        return original_run(argv, **kwargs)

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr("multipak.engine.Runner", lambda *args: runner)
    home = engine.store.environment_directory(tx.container) / "home"
    home.mkdir(parents=True)
    (home / "personal.txt").write_text("keep me")
    runner.container["Mounts"] = [{"Type": "bind", "Source": str(home)}]
    return engine, tx, home


def test_offer_follows_successful_uninstall_without_deleting_container(installed):
    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    assert candidate.name == tx.container
    assert not engine.store.transactions()
    assert not engine.runner.package_present
    assert engine.runner.container is not None
    assert not any(call[:2] == ["podman", "rm"] for call in engine.runner.calls)
    assert (home / "personal.txt").read_text() == "keep me"


@pytest.mark.parametrize("same_name", [False, True])
def test_other_reference_blocks_offer_and_ownership_outlives_creator(installed, same_name):
    engine, tx, _home = installed
    other = replace(
        tx,
        id="b" * 32,
        package_name="second",
        container_created=False,
        container_id="d" * 64 if same_name else tx.container_id,
        container=tx.container if same_name else "Other-name",
    )
    engine.store.save(other)
    assert uninstall_application(engine, tx) is None
    assert engine.runner.container is not None
    # Return to the real identity for removal of the last app. The creator's
    # transaction has gone; its independent ownership record must still work.
    other.container, other.container_id = tx.container, tx.container_id
    engine.store.save(other)
    candidate = uninstall_application(engine, other)
    assert candidate.owner_id == tx.id


def test_external_reused_container_is_not_offered_even_with_multipak_name(installed):
    engine, tx, _home = installed
    tx.container_created = False
    assert uninstall_application(engine, tx) is None
    assert engine.runner.container is not None


def test_existing_owned_shortcut_allows_offer(installed):
    engine, tx, _home = installed
    tx.container_created = False
    shortcut = engine.store.applications / f"{tx.container}.desktop"
    shortcut.write_text(
        "[Desktop Entry]\nX-MultiPak-ContainerIcon=true\n"
        f"X-MultiPak-ContainerID={tx.container_id}\n"
    )
    candidate = uninstall_application(engine, tx)
    assert candidate is not None
    remove_container(engine, candidate)
    assert not shortcut.exists()


@pytest.mark.parametrize("change", ["owner", "name", "record", "reference"])
def test_confirmation_rechecks_identity_ownership_and_references(installed, change):
    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    if change == "owner":
        engine.runner.container["Config"]["Labels"][OWNER_LABEL] = "b" * 32
    elif change == "name":
        engine.runner.container["Name"] = "Renamed"
    elif change == "record":
        remember_container(engine.store, replace(tx, container_id="d" * 64))
    else:
        engine.store.save(replace(tx, id="b" * 32, container_created=False))
    with pytest.raises((ValueError, RuntimeError)):
        remove_container(engine, candidate)
    assert not any(call[:2] == ["podman", "rm"] for call in engine.runner.calls)
    assert (home / "personal.txt").exists()


def test_explicit_removal_targets_only_confirmed_id_and_keeps_home_and_image(installed):
    engine, tx, home = installed
    remember_container(engine.store, tx)
    candidate = uninstall_application(engine, tx)
    remove_container(engine, candidate)
    assert engine.runner.container is None
    assert engine.runner.image_exists
    assert (home / "personal.txt").read_text() == "keep me"
    assert not (home.parent / "container.json").exists()
    assert [call for call in engine.runner.calls if call[:2] == ["podman", "rm"]] == [
        ["podman", "rm", "--force", "--time", "2", tx.container_id]
    ]
    assert not any(call[:3] == ["podman", "image", "rm"] for call in engine.runner.calls)


def test_failed_removal_keeps_record_and_can_be_retried(installed):
    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    engine.runner.fail_remove = True
    with pytest.raises(CommandError):
        remove_container(engine, candidate)
    assert (home.parent / "container.json").exists()
    assert engine.runner.container is not None
    engine.runner.fail_remove = False
    remove_container(engine, candidate)
    assert engine.runner.container is None
    assert (home / "personal.txt").exists()


def test_failed_application_uninstall_never_offers_or_removes_container(installed):
    engine, tx, _home = installed
    engine.runner.busy = True
    with pytest.raises(RuntimeError):
        uninstall_application(engine, tx)
    assert engine.store.transactions()[0].status == "cleanup_failed"
    assert not any(call[:2] == ["podman", "rm"] for call in engine.runner.calls)


def test_home_deleted_only_when_explicitly_selected_without_following_symlinks(installed, tmp_path):
    engine, tx, home = installed
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "untouched.txt").write_text("keep me")
    (home / "linked-folder").symlink_to(outside, target_is_directory=True)
    candidate = uninstall_application(engine, tx)
    assert candidate.managed_home
    remove_container(engine, candidate, remove_home=True)
    assert not home.exists()
    assert (outside / "untouched.txt").read_text() == "keep me"
    assert engine.runner.image_exists


def test_replaced_home_folder_blocks_deletion(installed, tmp_path):
    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    moved = home.with_name("preserved-home")
    home.rename(moved)
    home.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        remove_container(engine, candidate, remove_home=True)
    assert engine.runner.container is not None
    assert (moved / "personal.txt").exists()


def test_unknown_home_is_kept_and_cannot_be_selected(installed):
    engine, tx, home = installed
    engine.runner.container["Mounts"] = []
    candidate = uninstall_application(engine, tx)
    assert not candidate.managed_home
    with pytest.raises(ValueError):
        remove_container(engine, candidate, remove_home=True)
    assert engine.runner.container is not None
    assert (home / "personal.txt").exists()


def test_another_containers_home_mount_blocks_deletion(installed, monkeypatch):
    import json

    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    run = engine.runner.run

    def shared(argv, **kwargs):
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([{"Id": tx.container_id}, {"Id": "d" * 64}])
        if argv == ["podman", "inspect", "d" * 64]:
            return json.dumps([{"Mounts": [{"Source": str(home)}]}])
        return run(argv, **kwargs)

    monkeypatch.setattr(engine.runner, "run", shared)
    with pytest.raises(RuntimeError):
        remove_container(engine, candidate, remove_home=True)
    assert engine.runner.container is not None
    assert (home / "personal.txt").exists()


def test_failed_home_deletion_can_be_retried_after_container_is_gone(installed, monkeypatch):
    engine, tx, home = installed
    candidate = uninstall_application(engine, tx)
    import shutil

    real_remove = shutil.rmtree
    with monkeypatch.context() as context:
        context.setattr(
            "multipak.containers.shutil.rmtree",
            lambda path: (_ for _ in ()).throw(PermissionError("simulated permission error")),
        )
        with pytest.raises(PermissionError):
            remove_container(engine, candidate, remove_home=True)
    assert engine.runner.container is None
    assert (home.parent / "container.json").exists()
    assert (home / "personal.txt").exists()
    assert shutil.rmtree is real_remove
    remove_container(engine, candidate, remove_home=True)
    assert not home.exists()

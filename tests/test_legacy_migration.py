import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from multipak.kirigami import legacy_migration
from multipak.kirigami.update_notice import installation_allowed

REF = "app/io.github.multipak.manager/x86_64/stable"


@pytest.fixture(autouse=True)
def mock_launcher_migration(monkeypatch):
    monkeypatch.setattr(legacy_migration, "Store", Mock())
    migrate = Mock()
    monkeypatch.setattr(legacy_migration, "migrate_legacy_launchers", migrate)
    return migrate


@pytest.mark.parametrize("scopes", [(), ("--user",), ("--system",), ("--user", "--system")])
def test_detection_matches_the_exact_id_in_both_scopes(monkeypatch, scopes):
    def list_refs(argv, **_options):
        stdout = "io.github.multipak.manager.other/x86_64/stable\n"
        if argv[2] in scopes:
            stdout += REF.removeprefix("app/") + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout)

    monkeypatch.setattr(legacy_migration.host, "run", list_refs)
    assert legacy_migration._installed() == ({scope: [REF] for scope in scopes}, [])


def test_unavailable_flatpak_does_not_claim_the_old_app_is_installed(
    monkeypatch, mock_launcher_migration
):
    run = Mock(side_effect=FileNotFoundError("flatpak"))
    monkeypatch.setattr(legacy_migration.host, "run", run)
    assert legacy_migration.LegacyMigration._check(remove=False) == (False, "")
    assert run.call_count == 2
    mock_launcher_migration.assert_called_once()


def test_removal_targets_only_the_old_app_and_repairs_associations(
    monkeypatch, mock_launcher_migration
):
    installed = {"--user": [REF], "--system": [REF]}
    query = Mock(side_effect=[(installed, []), ({}, [])])

    def uninstall(*_args, **_options):
        mock_launcher_migration.assert_called_once()

    run = Mock(side_effect=uninstall)
    repair = Mock()
    monkeypatch.setattr(legacy_migration, "_installed", query)
    monkeypatch.setattr(legacy_migration.host, "run", run)
    monkeypatch.setattr(legacy_migration, "FileAssociations", lambda: repair)
    assert legacy_migration.LegacyMigration._check(remove=True) == (False, "")
    assert [call.args[0] for call in run.call_args_list] == [
        ["flatpak", "uninstall", scope, "--assumeyes", REF] for scope in installed
    ]
    repair.migrate_legacy.assert_called_once_with(removed=True)


def test_failed_removal_keeps_the_block_and_offers_a_manual_command(monkeypatch):
    installed = {"--system": [REF]}
    monkeypatch.setattr(legacy_migration, "_installed", lambda: (installed, []))
    monkeypatch.setattr(
        legacy_migration.host,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(1, "flatpak", stderr="Access denied")),
    )
    required, message = legacy_migration.LegacyMigration._check(remove=True)
    assert required
    assert "flatpak uninstall io.github.multipak.manager" in message
    assert "Access denied" in message


def test_installation_stays_blocked_until_the_migration_finishes(monkeypatch):
    monkeypatch.setattr(legacy_migration, "_installed", lambda: ({"--user": [REF]}, []))
    migration = legacy_migration.LegacyMigration(enabled=True)
    controller = SimpleNamespace(_legacy_migration=migration)
    assert not installation_allowed(controller)
    migration._finished(*migration._check(remove=False))
    assert migration.required
    assert not installation_allowed(controller)
    migration._finished(False, "")
    assert installation_allowed(controller)


def test_disabled_migration_does_not_call_flatpak(monkeypatch):
    run = Mock()
    monkeypatch.setattr(legacy_migration.host, "run", run)
    migration = legacy_migration.LegacyMigration()
    migration.start()
    migration.remove()
    run.assert_not_called()
    assert installation_allowed(SimpleNamespace(_legacy_migration=migration))


def test_startup_repairs_shortcuts_when_the_old_app_was_removed_manually(
    monkeypatch, mock_launcher_migration
):
    associations = Mock()
    monkeypatch.setattr(legacy_migration, "_installed", lambda: ({}, []))
    monkeypatch.setattr(legacy_migration, "FileAssociations", lambda: associations)
    assert legacy_migration.LegacyMigration._check(remove=False) == (False, "")
    mock_launcher_migration.assert_called_once()
    associations.migrate_legacy.assert_called_once_with(removed=False)


def test_failed_shortcut_migration_prevents_automatic_uninstall(
    monkeypatch, mock_launcher_migration
):
    monkeypatch.setattr(legacy_migration, "_installed", lambda: ({"--user": [REF]}, []))
    mock_launcher_migration.side_effect = OSError("read-only")
    run = Mock()
    monkeypatch.setattr(legacy_migration.host, "run", run)
    required, error = legacy_migration.LegacyMigration._check(remove=True)
    assert required
    assert "AppImage shortcuts" in error
    run.assert_not_called()

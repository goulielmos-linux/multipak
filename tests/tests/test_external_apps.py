from unittest.mock import Mock, patch

import pytest

from multipak.external_apps import KONTAINER_ID, open_kontainer
from multipak.process import CommandError


@pytest.mark.parametrize("result", [True, (True, 123)])
def test_native_kontainer_is_detached_without_a_personal_path(result):
    runner = Mock()
    with (
        patch("multipak.external_apps.host.which", return_value="/usr/bin/kontainer"),
        patch("multipak.external_apps.QProcess.startDetached", return_value=result) as launch,
    ):
        open_kontainer(runner)
    runner.run.assert_not_called()
    launch.assert_called_once_with("/usr/bin/kontainer", [])


def test_flatpak_uses_application_id_without_forcing_user_or_system_installation():
    runner = Mock()
    with (
        patch(
            "multipak.external_apps.host.which",
            side_effect=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
        ),
        patch("multipak.external_apps.QProcess.startDetached", return_value=(True, 123)) as launch,
    ):
        open_kontainer(runner)
    runner.run.assert_called_once_with(
        ["/usr/bin/flatpak", "info", "--show-ref", KONTAINER_ID], timeout=15
    )
    launch.assert_called_once_with("/usr/bin/flatpak", ["run", KONTAINER_ID])


def test_missing_flatpak_app_is_not_launched():
    runner = Mock()
    runner.run.side_effect = CommandError(["flatpak", "info"], 1, "not installed")
    with (
        patch(
            "multipak.external_apps.host.which",
            side_effect=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
        ),
        patch("multipak.external_apps.QProcess.startDetached") as launch,
        pytest.raises(RuntimeError, match="Check that it is installed"),
    ):
        open_kontainer(runner)
    launch.assert_not_called()


def test_missing_native_app_and_flatpak_reports_missing_companion():
    with (
        patch("multipak.external_apps.host.which", return_value=None),
        pytest.raises(RuntimeError, match="was not found"),
    ):
        open_kontainer(Mock())


@pytest.mark.parametrize("result", [False, (False, 0)])
def test_launcher_failure_is_reported(result):
    with (
        patch("multipak.external_apps.host.which", return_value="/usr/bin/kontainer"),
        patch("multipak.external_apps.QProcess.startDetached", return_value=result),
        pytest.raises(RuntimeError, match="Could not open"),
    ):
        open_kontainer(Mock())

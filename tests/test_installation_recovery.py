import base64
import json

import pytest

from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS
from multipak.process import CommandError

DEBIAN = next(e for e in ENVIRONMENTS if e.id == "debian-stable")


def package_for(deb, engine):
    return inspect_package(deb, engine.runner)


# 1. A home folder kept from an earlier container is never deleted by cleanup.


def test_cleanup_keeps_home_folder_that_existed_before_the_installation(
    fake_engine, deb, monkeypatch
):
    engine = fake_engine
    kept = engine.store.environment_directory("MultiPak-Debian-13") / "home"
    kept.mkdir(parents=True)
    (kept / "precious.txt").write_text("kept from a removed container")
    engine.runner.create_failure_after_pull = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    assert engine.tx.container == "MultiPak-Debian-13"
    assert engine.tx.home_created is False
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(engine.tx)
    assert (kept / "precious.txt").read_text() == "kept from a removed container"


def test_cleanup_still_removes_a_home_folder_created_by_the_installation(
    fake_engine, deb, monkeypatch
):
    engine = fake_engine
    tx = engine.install(package_for(deb, engine), DEBIAN)
    assert tx.home_created is True
    home = engine.environment_home
    (home / "file").write_text("created during this installation")
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    engine.cleanup(tx)
    assert not home.exists()
    assert not home.parent.exists()


# 2. An invalid .desktop file is skipped with a warning; the installation succeeds.


def test_invalid_desktop_file_is_skipped_without_failing_the_installation(fake_engine, deb):
    engine = fake_engine
    original = engine.runner.run
    invalid = "[Desktop Entry]\nType=Application\nName=A\nName=B\nExec=x\n"
    valid = "[Desktop Entry]\nType=Application\nName=Good\nExec=/usr/bin/good\n"

    def run(argv, **options):
        output = original(argv, **options)
        if (
            isinstance(output, str)
            and output.startswith("MULTIPAK_RESULT=")
            and ("desktops" in output)
        ):
            entries = [
                {"name": "bad.desktop", "content": base64.b64encode(invalid.encode()).decode()},
                {"name": "good.desktop", "content": base64.b64encode(valid.encode()).decode()},
                {"name": "broken.desktop", "content": "not base64!"},
            ]
            return "MULTIPAK_RESULT=" + json.dumps({"desktops": entries})
        return output

    engine.runner.run = run
    tx = engine.install(package_for(deb, engine), DEBIAN)
    assert tx.status == "installed"
    assert len(tx.launchers) == 1
    assert len(tx.warnings) == 2
    assert "bad.desktop" in tx.warnings[0] and "broken.desktop" in tx.warnings[1]


@pytest.mark.parametrize("operation", ["retry", "cleanup"])
def test_old_busy_marker_prevents_changes_to_the_container(
    fake_engine, deb, monkeypatch, operation
):
    engine = fake_engine
    engine.runner.fail_download = True
    with pytest.raises(CommandError):
        engine.install(package_for(deb, engine), DEBIAN)
    tx = engine.tx
    engine.runner.fail_download = False
    run = engine.runner.run
    start = len(engine.runner.calls)

    def legacy_busy(argv, **options):
        result = run(argv, **options)
        if "--busy" in argv and f"/tmp/multipak-{tx.id}-running.json" in argv:
            return "MULTIPAK_RESULT=" + json.dumps({"busy": True})
        return result

    monkeypatch.setattr(engine.runner, "run", legacy_busy)
    monkeypatch.setattr("multipak.engine.Runner", lambda *_: engine.runner)
    with pytest.raises(RuntimeError, match="still running"):
        getattr(engine, operation)(tx)
    calls = engine.runner.calls[start:]
    assert not any(
        call[:2] == ["podman", "rm"] or "--remove" in call or "apt-get" in call for call in calls
    )

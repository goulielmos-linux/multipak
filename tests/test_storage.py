import errno
from types import SimpleNamespace

import pytest

from multipak.metadata import inspect_package
from multipak.models import ENVIRONMENTS
from multipak.process import CommandError
from multipak.storage import InsufficientSpace

DEBIAN = next(env for env in ENVIRONMENTS if env.id == "debian-stable")


def test_known_package_copy_stops_before_image_pull(fake_engine, deb, monkeypatch):
    package = inspect_package(deb, fake_engine.runner)
    monkeypatch.setattr("multipak.storage.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(InsufficientSpace, match="copy the package"):
        fake_engine.install(package, DEBIAN)
    assert not any(call[:2] == ["podman", "pull"] for call in fake_engine.runner.calls)


@pytest.mark.parametrize("disk_full", [True, False])
def test_install_error_survives_a_failed_journal_write(fake_engine, deb, monkeypatch, disk_full):
    package = inspect_package(deb, fake_engine.runner)
    error = CommandError(
        ["podman", "pull"], 125, "no space left on device" if disk_full else "network failed"
    )
    run, save = fake_engine.runner.run, fake_engine.store.save

    def pull(argv, **options):
        if argv[:2] == ["podman", "pull"]:
            raise error
        return run(argv, **options)

    def save_record(tx):
        if tx.status == "failed":
            raise OSError(errno.ENOSPC, "journal could not be written")
        save(tx)

    monkeypatch.setattr(fake_engine.runner, "run", pull)
    monkeypatch.setattr(fake_engine.store, "save", save_record)
    with pytest.raises(InsufficientSpace if disk_full else CommandError) as caught:
        fake_engine.install(package, DEBIAN)
    if disk_full:
        assert caught.value.__cause__ is error
        assert "disk is full" in str(caught.value)
    else:
        assert caught.value is error

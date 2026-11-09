from conftest import FakeRunner

from multipak.environments import OWNER_LABEL, SHARED_LABEL, discover
from multipak.guest import process_identity, run_transaction, transaction_busy
from multipak.models import ENVIRONMENTS


def existing_runner():
    runner = FakeRunner()
    runner.image_exists = True
    runner.release = 'ID=fedora\nVERSION_ID="44"\n'
    runner.container = {
        "Name": "Fedora-apps",
        "Id": "container123",
        "Image": "sha256:image123",
        "Config": {"Labels": {"manager": "distrobox"}},
    }
    return runner


def test_discovers_fedora_toolbox_and_matches_explicit_version(monkeypatch):
    monkeypatch.setattr("multipak.environments.host.which", lambda _: "/usr/bin/podman")
    runner = existing_runner()
    found = discover(runner)
    assert len(found) == 1
    assert found[0].name == "Fedora-apps"
    profiles = {e.id: e for e in ENVIRONMENTS}
    assert found[0].matches(profiles["fedora-44"])
    assert not found[0].matches(profiles["fedora-43"])
    assert not found[0].matches(profiles["fedora-latest"])
    assert not any(call[:2] == ["distrobox", "enter"] for call in runner.calls)


def test_unshared_private_home_is_not_offered_for_sharing(monkeypatch):
    monkeypatch.setattr("multipak.environments.host.which", lambda _: "/usr/bin/podman")
    runner = existing_runner()
    runner.container["Config"]["Labels"][OWNER_LABEL] = "original-transaction"
    assert discover(runner) == []
    runner.container["Config"]["Labels"][SHARED_LABEL] = "1"
    assert discover(runner)[0].managed_home


def test_busy_marker_tracks_live_process_identity(tmp_path, monkeypatch):
    import json
    import os

    monkeypatch.setattr(
        "multipak.guest.process_identity",
        lambda pid: "12345" if pid == os.getpid() else None,
    )
    marker = tmp_path / "running.json"
    marker.write_text(json.dumps({str(os.getpid()): "12345"}))
    assert transaction_busy(str(marker))
    marker.write_text(json.dumps({"99999999": None}))
    assert not transaction_busy(str(marker))
    marker.write_text(json.dumps({str(os.getpid()): "old-pid-starttime"}))
    assert not transaction_busy(str(marker))
    marker.unlink()
    assert run_transaction(str(marker), ["/bin/true"]) == 0
    assert not marker.exists()


def test_process_identity_reads_starttime_after_parenthesized_command(monkeypatch):
    from pathlib import Path

    stat = "123 (command with spaces) S " + " ".join(["0"] * 18 + ["998877"])
    monkeypatch.setattr(Path, "read_text", lambda _: stat)
    assert process_identity(123) == "998877"


def test_latest_reuses_same_release_even_when_image_ids_differ():
    from multipak.environments import resolve_floating_profiles
    from multipak.models import ExistingEnvironment

    image = "quay.io/fedora/fedora-toolbox:latest"
    environments = [
        ExistingEnvironment("multipak-pending-fedora-45", "exact", "fedora", "45", "old-image"),
        ExistingEnvironment(
            "multipak-pending-fedora-latest", "floating", "fedora", "45", "new-image", (image,)
        ),
        ExistingEnvironment("Fedora-apps", "older", "fedora", "44"),
    ]
    resolved = resolve_floating_profiles(environments, [])
    latest = next(e for e in ENVIRONMENTS if e.id == "fedora-latest")
    assert resolved[0].matches(latest)
    assert resolved[1].matches(latest)
    assert not resolved[2].matches(latest)


def test_latest_can_resolve_from_cached_version_tags_without_latest_container():
    from multipak.environments import resolve_floating_profiles
    from multipak.models import ExistingEnvironment

    environment = ExistingEnvironment("fedora-45", "exact", "fedora", "45")
    images = [
        {
            "Names": [
                "quay.io/fedora/fedora-toolbox:latest",
                "quay.io/fedora/fedora-toolbox:45",
            ]
        }
    ]
    latest = next(e for e in ENVIRONMENTS if e.id == "fedora-latest")
    assert resolve_floating_profiles([environment], images)[0].matches(latest)
    assert not resolve_floating_profiles([environment], [])[0].matches(latest)


def test_floating_resolution_does_not_cross_distribution_families():
    from multipak.environments import resolve_floating_profiles
    from multipak.models import ExistingEnvironment

    environments = [ExistingEnvironment("debian", "other", "debian", "45")]
    images = [
        {
            "Names": [
                "quay.io/fedora/fedora-toolbox:latest",
                "quay.io/fedora/fedora-toolbox:45",
            ]
        }
    ]
    latest = next(e for e in ENVIRONMENTS if e.id == "fedora-latest")
    assert not resolve_floating_profiles(environments, images)[0].matches(latest)

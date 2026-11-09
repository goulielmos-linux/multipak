import json

from multipak import catalog
from multipak.models import Environment, ExistingEnvironment


class CatalogRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv == ["distrobox", "create", "-C"]:
            return "\n".join(
                [
                    "quay.io/fedora/fedora-toolbox:44",
                    "registry.fedoraproject.org/fedora-toolbox:40",
                    "docker.io/library/ubuntu:24.04",
                    "quay.io/toolbx/ubuntu-toolbox:24.04",
                    "docker.io/library/debian:testing",
                ]
            )
        reference = argv[-1]
        if "fedora-toolbox" in reference:
            return json.dumps(
                {
                    "RepoTags": ["43", "44", "45", "46", "latest", "rawhide", "45.20260101"],
                    "Labels": {"org.opencontainers.image.version": "46"},
                }
            )
        if "ubuntu" in reference:
            return json.dumps(
                {
                    "RepoTags": ["22.04", "24.04", "26.04", "latest"],
                    "Labels": {"org.opencontainers.image.version": "26.04"},
                }
            )
        return json.dumps({"RepoTags": ["12", "13", "14", "stable", "testing"], "Labels": {}})


def configure(monkeypatch):
    monkeypatch.setattr(catalog.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(catalog, "debian_stable_version", lambda: "13")


def test_catalog_uses_kontainer_sources_and_refreshes_registry_versions(monkeypatch):
    configure(monkeypatch)
    runner = CatalogRunner()
    profiles = catalog.load_catalog(runner)
    by_id = {item.id: item for item in profiles}
    assert by_id["fedora-45"].image == "quay.io/fedora/fedora-toolbox:45"
    assert by_id["fedora-46"].image == "quay.io/fedora/fedora-toolbox:46"
    assert by_id["ubuntu-latest"].image == "docker.io/library/ubuntu:latest"
    assert by_id["debian-stable"].image == "docker.io/library/debian:stable"
    assert by_id["debian-stable"].resolved_version == "13"
    assert by_id["fedora-latest"].resolved_version == "46"
    assert not any(item.id.endswith("rawhide") for item in profiles)
    assert not any(call[:2] == ["podman", "run"] for call in runner.calls)


def test_fedora46_alternatives_include45_without_latest_or_recommended_duplicate(monkeypatch):
    configure(monkeypatch)
    profiles = list(catalog.load_catalog(CatalogRunner()))
    recommended = next(item for item in profiles if item.id == "fedora-46")
    result = catalog.alternatives(profiles, recommended)
    assert [item.id for item in result] == ["fedora-45", "fedora-44", "fedora-43"]
    alias = next(item for item in profiles if item.id == "fedora-latest")
    assert catalog.alternatives(profiles, alias) == result
    assert ExistingEnvironment("my-fedora", "id", "fedora", "46").matches(alias)
    assert not ExistingEnvironment("old-fedora", "old", "fedora", "45").matches(alias)


def test_package_hint_uses_the_selected_catalog_repository():
    profiles = [
        Environment("fedora-latest", "Fedora", "rpm", "quay.io/fedora/fedora-toolbox:latest")
    ]
    assert (
        catalog.package_environment("fedora-46", profiles).image
        == "quay.io/fedora/fedora-toolbox:46"
    )
    assert catalog.package_environment("fedora-46;command", profiles) is None


def test_catalog_cache_works_without_network_or_distrobox(monkeypatch, tmp_path):
    configure(monkeypatch)
    runner = CatalogRunner()
    cache = tmp_path / "catalog.json"
    profiles = catalog.load_catalog(runner, cache)
    calls = len(runner.calls)
    monkeypatch.setattr(catalog.shutil, "which", lambda name: None)
    assert catalog.load_catalog(runner, cache) == profiles
    assert len(runner.calls) == calls


def test_catalog_rejects_unknown_registries_and_cache_entries(tmp_path):
    assert not any(catalog.parse_catalog("evil.example/fedora:45")["fedora"].values())
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "fedora-45",
                    "title": "Fedora 45",
                    "kind": "rpm",
                    "image": "evil.example/fedora:45",
                }
            ]
        )
    )
    assert catalog.read_cached(path) == ()

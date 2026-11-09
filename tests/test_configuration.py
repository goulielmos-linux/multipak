import pytest

from multipak.kirigami.configuration import Configuration
from multipak.models import Environment, ExistingEnvironment

UBUNTU = Environment("ubuntu-26.04", "Ubuntu 26.04", "deb", "ubuntu:26.04")
DEBIAN = Environment("debian-13", "Debian 13", "deb", "debian:13")
FEDORA = Environment("fedora-45", "Fedora 45", "rpm", "fedora:45")


def configuration(existing=()):
    result = Configuration()
    result.reset((UBUNTU, DEBIAN, FEDORA), existing, UBUNTU)
    return result


def test_catalog_is_limited_to_the_package_family_and_standard_is_independent():
    settings = configuration()
    assert len(settings.profileOptions) == 2
    settings.chooseSuggested(False)
    settings.chooseProfile(1)
    assert settings.selection().environment == DEBIAN
    assert settings.selection().manual
    assert settings.standard_selection().environment == UBUNTU


def test_default_reuses_managed_container_without_creating_duplicates():
    existing = ExistingEnvironment("MultiPak-Ubuntu-26.04", "id", "ubuntu", "26.04")
    settings = configuration((existing,))
    assert settings.standard_selection().existing == existing
    assert settings.selection().existing == existing


def test_existing_picker_filters_compatibility_and_resets_after_base_changes():
    ubuntu = ExistingEnvironment("Personal", "u", "ubuntu", "26.04")
    debian = ExistingEnvironment("Tests", "d", "debian", "13")
    wrong = ExistingEnvironment("Other-version", "old", "ubuntu", "24.04")
    settings = configuration((ubuntu, debian, wrong))
    settings.chooseContainerMode("existing")
    assert settings.existingOptions == [{"name": "Personal"}]
    assert settings.selection().existing == ubuntu
    settings.chooseSuggested(False)
    settings.chooseProfile(1)
    assert settings.selection().existing == debian
    assert settings.standard_selection().existing is None


def test_unavailable_existing_mode_cannot_be_selected_and_cloning_is_not_offered():
    settings = configuration()
    assert {row["value"] for row in settings.containerOptions} == {"default", "existing", "custom"}
    row = settings.containerOptions[1]
    assert not row["available"]
    assert row["label"].endswith("(Unavailable)")
    settings.chooseContainerMode("existing")
    assert settings.containerMode == "default"
    settings.chooseContainerMode("new")
    assert settings.containerMode == "default"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../other",
        "invalid@name",
        "x" * 64,
        "MultiPak-Test",
        "multipak-app",
        "C2-MultiPak-Ubuntu-26.04",
        "MultiPak-Test",
    ],
)
def test_invalid_or_reserved_names_never_produce_an_installation_selection(name):
    settings = configuration()
    settings.chooseContainerMode("custom")
    settings.setCustomName(name)
    assert not settings.valid
    with pytest.raises(ValueError):
        settings.selection()


def test_custom_names_are_checked_against_all_known_containers():
    settings = configuration((ExistingEnvironment("Personal", "id", "debian", "13"),))
    settings.chooseContainerMode("custom")
    settings.setCustomName("Personal")
    assert "already exists" in settings.errorMessage
    settings.setCustomName("My-Tests")
    selection = settings.selection()
    assert selection.custom_name == "My-Tests"
    assert selection.existing is None
    assert settings.standard_selection().custom_name == ""


def test_new_default_uses_multipak_and_unresolved_versions_are_not_invented():
    settings = configuration()
    assert settings.selection().container_name(()) == "MultiPak-Ubuntu-26.04"
    floating = Environment("ubuntu-latest", "Ubuntu LTS", "deb", "ubuntu:latest")
    settings.reset((floating,), (), floating)
    assert settings.selection().container_name(()) == "The name will be assigned during setup"


def test_spaces_in_custom_names_become_hyphens_before_validation_and_selection():
    settings = configuration()
    settings.chooseContainerMode("custom")
    settings.setCustomName("My Apps")
    assert settings.customName == "My-Apps"
    assert settings.valid
    assert settings.selection().custom_name == "My-Apps"
    settings.setCustomName("MultiPak Test")
    assert settings.customName == "MultiPak-Test"
    assert not settings.valid


@pytest.mark.parametrize(
    "family,version,alias_tag,kind,repository",
    [
        ("ubuntu", "26.04", "latest", "deb", "docker.io/library/ubuntu"),
        ("debian", "13", "stable", "deb", "docker.io/library/debian"),
        ("fedora", "45", "latest", "rpm", "quay.io/fedora/fedora-toolbox"),
    ],
)
@pytest.mark.parametrize("alias_first", [True, False])
def test_manual_versions_merge_resolved_aliases_but_keep_the_automatic_suggestion(
    family,
    version,
    alias_tag,
    kind,
    repository,
    alias_first,
):
    title = family.title() + " " + version
    alias = Environment(f"{family}-{alias_tag}", title, kind, repository + ":" + alias_tag, version)
    pinned = Environment(f"{family}-{version}", title, kind, repository + ":" + version)
    profiles = (alias, pinned) if alias_first else (pinned, alias)
    existing = ExistingEnvironment(f"MultiPak-{family.title()}-{version}", "id", family, version)
    settings = Configuration()
    settings.reset(profiles, (existing,), alias)
    assert settings.profileOptions == [{"title": title}]
    assert settings.profiles == (pinned,)
    assert settings.profileIndex == 0
    assert settings.environment is alias
    assert settings.standard_selection().environment is alias
    assert settings.standard_selection().existing is existing
    settings.chooseSuggested(False)
    assert settings.selection().environment is pinned
    assert settings.selection().existing is existing
    settings.chooseSuggested(True)
    assert settings.environment is alias


def test_unresolved_alias_is_not_assumed_to_be_a_specific_release():
    unknown = Environment("ubuntu-latest", "Ubuntu — stable version", "deb", "ubuntu:latest")
    settings = Configuration()
    settings.reset((unknown, UBUNTU, DEBIAN), (), unknown)
    assert settings.profiles == (unknown, UBUNTU, DEBIAN)
    settings.chooseSuggested(False)
    settings.chooseProfile(1)
    assert settings.selection().environment is UBUNTU
    settings.chooseSuggested(True)
    assert settings.profileIndex == 0
    settings.chooseSuggested(False)
    assert settings.selection().environment is UBUNTU


def test_known_alias_without_fixed_tag_stays_available_without_inventing_an_image():
    alias = Environment("debian-stable", "Debian 13", "deb", "debian:stable", "13")
    settings = Configuration()
    settings.reset((alias,), (), alias)
    assert settings.profiles == (alias,)
    settings.chooseSuggested(False)
    assert settings.selection().environment.image == "debian:stable"


def test_equivalent_titles_do_not_merge_different_repositories_or_releases():
    alias = Environment("ubuntu-latest", "Ubuntu 26.04", "deb", "ubuntu:latest", "26.04")
    other = Environment("ubuntu-26.04", "Ubuntu 26.04", "deb", "custom/ubuntu:26.04")
    settings = Configuration()
    settings.reset((alias, other, UBUNTU, DEBIAN), (), alias)
    assert settings.profiles == (UBUNTU, other, DEBIAN)

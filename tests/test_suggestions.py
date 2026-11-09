from pathlib import Path

import pytest

from multipak.models import Environment, Package
from multipak.suggestions import suggestion_details


@pytest.mark.parametrize("name", ["strawberry", "google-chrome-stable", "another-application"])
@pytest.mark.parametrize(
    "hint,source,expected,evidence",
    [
        ("fedora-45", "metadata", "High", "internal metadata"),
        ("fedora-45", "filename", "Medium", "filename"),
        ("", "", "Low", "latest Fedora version"),
    ],
)
def test_suggestion_strength_depends_on_evidence_not_application_name(
    name, hint, source, expected, evidence
):
    package = Package(Path("/tmp/app.rpm"), "rpm", name, "1", "noarch", "", hint, source)
    environment = Environment("fedora-latest", "Fedora 45", "rpm", "example:latest", "45")
    strength, text = suggestion_details(package, environment, "Fedora 45")
    assert strength == expected
    assert evidence in text
    assert "dependency compatibility" not in text


@pytest.mark.parametrize("version", ["44", "45"])
def test_strong_rpm_suggestion_explains_metadata_evidence(version):
    package = Package(
        Path("/tmp/app.rpm"),
        "rpm",
        "app",
        f"1.fc{version}",
        "noarch",
        distro_hint=f"fedora-{version}",
        distro_hint_source="metadata",
    )
    environment = Environment(f"fedora-{version}", f"Fedora {version}", "rpm", f"example:{version}")
    strength, text = suggestion_details(package, environment, f"Fedora {version}")
    assert strength == "High"
    assert text == (
        "Distribution detection confidence: High.\n\n"
        f"The package's internal metadata includes .fc{version}, "
        f"which means it was built for Fedora {version}."
    )


def test_weak_rpm_suggestion_explains_fallback():
    package = Package(Path("/tmp/app.rpm"), "rpm", "app", "1", "noarch")
    environment = Environment("fedora-latest", "Fedora 45", "rpm", "example:latest", "45")
    strength, text = suggestion_details(package, environment, "Fedora 45")
    assert strength == "Low"
    assert text == (
        "Distribution detection confidence: Low.\n\n"
        "MultiPak could not determine which RPM-based distribution this "
        "package was built for, so it will use the latest Fedora version "
        "as the default environment: Fedora 45.\n\nIf the application does not "
        "work after installation, it may have been built for a different "
        "Fedora version or another distribution, such as Nobara, Rocky "
        "Linux, AlmaLinux or openSUSE. Uninstall it through Application "
        "Manager — MultiPak and look for a package built for your chosen "
        "distribution."
    )


def test_filename_conflict_is_explained_and_internal_metadata_wins():
    package = Package(
        Path("/tmp/app.fc44.rpm"),
        "rpm",
        "app",
        "1.fc45",
        "noarch",
        "",
        "fedora-45",
        "metadata",
        "fedora-44",
    )
    environment = Environment("fedora-45", "Fedora 45", "rpm", "example:45")
    strength, text = suggestion_details(package, environment, "Fedora 45")
    assert strength == "High"
    assert "filename indicates Fedora 44" in text
    assert "internal metadata took priority" in text

from pathlib import Path

import pytest

from multipak.models import Environment, Package
from multipak.suggestions import android_suggestion_details, suggestion_details


@pytest.mark.parametrize("name", ["strawberry", "google-chrome-stable", "another-application"])
@pytest.mark.parametrize(
    "hint,source,expected,evidence",
    [
        ("fedora-45", "metadata", "High", "Release field"),
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
        "The Release field in the package metadata indicates that "
        f"it was built for Fedora {version}."
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


def test_android_recommendation_explains_minimum_and_target_as_android_versions():
    package = Package(
        Path("app.apk"),
        "apk",
        "app",
        "1",
        "x86",
        android_min_sdk=23,
        android_target_sdk=36,
    )
    strength, text = android_suggestion_details(package, "Android 13", 33)
    assert strength == "Medium"
    assert text.startswith("Identification confidence: Medium.\n\n")
    assert "requires Android 6.0 or later" in text
    assert "recommends Android 13" in text
    assert "developed targeting Android 16" in text
    assert "some features may behave differently" in text
    assert "API" not in text and "SDK" not in text


@pytest.mark.parametrize("minimum,target", [(1, 0), (23, 0), (34, 36), (99, 100), (23, 33)])
def test_android_recommendation_handles_missing_newer_and_matching_versions(minimum, target):
    package = Package(
        Path("app.apk"),
        "apk",
        "app",
        "1",
        "x86",
        android_min_sdk=minimum,
        android_target_sdk=target,
    )
    strength, text = android_suggestion_details(package, "Android 13", 33)
    if minimum > 33:
        assert strength == "Low"
        assert "does not meet that requirement" in text
        assert "recommends Android 13" not in text
    elif target == 33:
        assert strength == "High"
        assert "can use this same version to prepare the environment" in text
        assert "some features may behave differently" not in text
    if minimum == 1:
        assert strength == "Low" and "cannot confirm" in text
    if target == 0:
        assert "does not say which Android version" in text
    assert "API" not in text and "SDK" not in text
    assert "99" not in text and "100" not in text

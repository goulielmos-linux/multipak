import json
import sys
from pathlib import Path

import pytest

from multipak.kirigami import about

ROOT = Path(__file__).resolve().parents[1]


def test_the_built_libraries_appear_with_their_license(tmp_path, monkeypatch):
    versions = tmp_path / "bundled-versions.json"
    versions.write_text(json.dumps({"lua": "5.4.8", "popt": "1.19"}), encoding="utf-8")
    monkeypatch.setattr(about, "BUNDLED_VERSIONS", str(versions))
    rows = {row["name"]: row for row in about.bundled_tools()}
    assert rows["Lua"]["version"] == "5.4.8"
    assert rows["popt"]["version"] == "1.19"
    assert all(row["licenseUrl"].startswith("https://") for row in rows.values())


def test_outside_the_flatpak_they_are_not_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(about, "BUNDLED_VERSIONS", str(tmp_path / "missing.json"))
    names = {row["name"] for row in about.bundled_tools()}
    assert not names & {"Lua", "popt"}


def test_the_build_records_the_manifest_versions(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "packaging"))
    sys.modules.pop("install_flatpak", None)
    import install_flatpak

    versions = install_flatpak.bundled_versions()
    assert versions["lua"] and versions["popt"] and versions["rpm"]


def test_the_section_lists_nothing_outside_the_flatpak(monkeypatch):
    # Outside the Flatpak, unsquashfs and rpm would be the computer's own, not the bundled ones.
    pytest.importorskip("PySide6.QtCore")
    monkeypatch.setattr(about.host, "is_flatpak", lambda: False)
    info = about.AboutInfo.__new__(about.AboutInfo)
    assert about.AboutInfo.tools.fget(info) == []

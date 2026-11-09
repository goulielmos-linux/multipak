import gzip
import threading

import pytest
from conftest import make_deb

from multipak import metadata
from multipak.metadata import decompress_control, inspect_package, parse_control
from multipak.process import Runner


@pytest.mark.parametrize("compression", ["", "gz", "xz", "bz2", "zst"])
def test_reads_real_ar_control_archive(tmp_path, compression):
    path = make_deb(tmp_path / "test.deb", compression=compression)
    package = inspect_package(path, Runner(threading.Event(), lambda _: None))
    assert (package.name, package.version, package.architecture) == ("demo-app", "1.2", "all")
    assert "libc6" in package.dependencies
    assert not (tmp_path / "control").exists()


def test_multiline_control():
    assert parse_control("Depends: alpha,\n beta\n") == {"Depends": "alpha, beta"}


def test_rejects_architecture_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    path = make_deb(tmp_path / "arm.deb", "Package: demo\nVersion: 1\nArchitecture: arm64\n")
    with pytest.raises(ValueError, match="Architecture"):
        inspect_package(path, Runner(threading.Event(), lambda _: None))


def test_rejects_invalid_deb(tmp_path):
    path = tmp_path / "broken.deb"
    path.write_bytes(b"not a package")
    with pytest.raises(ValueError, match="DEB"):
        inspect_package(path, Runner(threading.Event(), lambda _: None))


def test_bounds_decompressed_control(monkeypatch):
    monkeypatch.setattr(metadata, "MAX_CONTROL_ARCHIVE", 100)
    with pytest.raises(ValueError, match="limit"):
        decompress_control("control.tar.gz", gzip.compress(b"x" * 101))


def test_rpm_metadata_uses_query_and_never_executes_scripts(tmp_path):
    path = tmp_path / "demo.rpm"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")
    calls = []

    class Query:
        def run(self, argv):
            calls.append(argv)
            return "demo\x1f1.0-1.fc44\x1fnoarch" if "--queryformat" in argv else "libc.so.6"

    package = inspect_package(path, Query())
    assert package.distro_hint == "fedora-44"
    assert all("-qp" in command for command in calls)
    assert all("-i" not in command for command in calls)


@pytest.mark.parametrize(
    "filename,release,hint,source",
    [
        ("app.fc45.rpm", "1", "fedora-45", "filename"),
        ("renamed.rpm", "1.fc44", "fedora-44", "metadata"),
        ("app.fc45.rpm", "1.fc44", "fedora-44", "metadata"),
        ("app.rpm", "1", "", ""),
        ("app.fc44.fc45.rpm", "1", "", ""),
    ],
)
def test_rpm_destination_prefers_internal_metadata_then_unambiguous_filename(
    tmp_path, filename, release, hint, source
):
    path = tmp_path / filename
    path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")

    class Query:
        def run(self, argv):
            return f"app\x1f1.0-{release}\x1fnoarch" if "--queryformat" in argv else ""

    package = inspect_package(path, Query())
    assert package.distro_hint == hint
    assert package.distro_hint_source == source


@pytest.mark.parametrize("filename", ["renamed.zip", "renamed.rpm", "no-extension"])
def test_deb_is_identified_by_contents_even_when_renamed(tmp_path, filename):
    path = make_deb(tmp_path / filename)
    package = inspect_package(path, Runner(threading.Event(), lambda _: None))
    assert package.kind == "deb"
    assert package.name == "demo-app"


def test_renamed_rpm_uses_rpm_parser(tmp_path):
    path = tmp_path / "package.deb"
    path.write_bytes(b"\xed\xab\xee\xdb\x03\x00\x00\x00")

    class Query:
        def run(self, argv):
            assert argv[0] == "rpm"
            return "demo\x1f1.0-1.fc45\x1fnoarch" if "--queryformat" in argv else ""

    assert inspect_package(path, Query()).kind == "rpm"


def test_ar_archive_with_control_is_not_automatically_a_deb(tmp_path):
    from conftest import ar_member

    path = tmp_path / "fake.deb"
    path.write_bytes(b"!<arch>\n" + ar_member("control.tar", b"fake control"))
    with pytest.raises(ValueError, match="DEB"):
        inspect_package(path, Runner(threading.Event(), lambda _: None))

import io
import stat
import tarfile
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from multipak import metadata, pisi

METADATA = """\
<PISI>
    <Source><Name>demo-source</Name></Source>
    <Package>
        <Name>demo-app</Name>
        <Summary xml:lang="tr">Uygulama</Summary>
        <Summary xml:lang="en">A test application</Summary>
        <RuntimeDependencies>
            <Dependency versionFrom="1.2" releaseTo="9">libdemo</Dependency>
            <Dependency>xdg-utils</Dependency>
        </RuntimeDependencies>
        <History>
            <Update release="12"><Version>2.4_rc1</Version></Update>
            <Update release="11"><Version>2.3</Version></Update>
        </History>
        <Distribution>PisiLinux</Distribution>
        <DistributionRelease>2.0</DistributionRelease>
        <Architecture>x86_64</Architecture>
        <PackageFormat>1.2</PackageFormat>
    </Package>
</PISI>
"""


def make_pisi(path, *, xml=METADATA, compression=ZIP_DEFLATED, with_image=True):
    with ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("metadata.xml", xml)
        archive.writestr("files.xml", "<Files/>")
        if with_image:
            image = io.BytesIO()
            with tarfile.open(fileobj=image, mode="w:xz") as files:
                script = b"#!/bin/sh\nexit 77\n"
                member = tarfile.TarInfo("usr/bin/demo-app")
                member.size = len(script)
                files.addfile(member, io.BytesIO(script))
            archive.writestr("install.tar.xz", image.getvalue(), compress_type=ZIP_STORED)
    return path


@pytest.mark.parametrize("compression", [ZIP_STORED, ZIP_DEFLATED])
@pytest.mark.parametrize("filename", ["demo-app-2.4_rc1-12-p2-x86_64.pisi", "renamed.zip"])
def test_reads_pisi_binary_package(tmp_path, monkeypatch, compression, filename):
    path = make_pisi(tmp_path / filename, compression=compression)
    monkeypatch.setattr(metadata.platform, "machine", lambda: "x86_64")
    assert metadata.package_kind(path) == "pisi"
    package = metadata.inspect_package(path, None)
    assert (package.kind, package.name, package.version, package.architecture) == (
        "pisi",
        "demo-app",
        "2.4_rc1-12",
        "x86_64",
    )
    assert pisi.read_info(path) == pisi.Info(
        name="demo-app",
        version="2.4_rc1",
        release="12",
        architecture="x86_64",
        distribution_release="2.0",
        summary="A test application",
        dependencies="libdemo (version >= 1.2, release <= 9)\nxdg-utils",
    )
    assert package.dependencies == "libdemo (version >= 1.2, release <= 9)\nxdg-utils"
    assert (package.distro_hint, package.distro_hint_source) == ("pisilinux-rolling", "default")
    assert list(tmp_path.iterdir()) == [path]


def test_rejects_an_ordinary_zip_with_a_pisi_suffix(tmp_path):
    path = tmp_path / "sources.pisi"
    with ZipFile(path, "w") as archive:
        archive.writestr("README", "Application sources")
    with pytest.raises(ValueError, match="required Pisi Linux binary package files"):
        metadata.package_kind(path)


def test_the_metadata_decides_the_format_not_the_pisi_suffix(tmp_path):
    # Solus's eopkg shares PiSi's binary format (see test_eopkg.py).
    path = make_pisi(tmp_path / "demo-app.pisi", xml=METADATA.replace("PisiLinux", "Solus"))
    assert metadata.package_kind(path) == "eopkg"
    path = make_pisi(tmp_path / "other.pisi", xml=METADATA.replace("PisiLinux", "Pardus"))
    with pytest.raises(ValueError, match="does not identify Pisi Linux"):
        metadata.package_kind(path)


def test_reads_alternative_dependencies_components_and_an_optional_build(tmp_path):
    xml = METADATA.replace(
        "</RuntimeDependencies>",
        """<AnyDependency>
               <Dependency>editor-a</Dependency>
               <Dependency releaseFrom="8">editor-b</Dependency>
           </AnyDependency>
           <Component>system.base</Component></RuntimeDependencies><Build>4</Build>""",
    )
    info = pisi.read_info(make_pisi(tmp_path / "demo-app.pisi", xml=xml))
    assert info.build == "4"
    assert info.dependencies.splitlines()[-2:] == [
        "editor-a | editor-b (release >= 8)",
        "Component system.base",
    ]


def test_rejects_unsupported_package_format(tmp_path):
    xml = METADATA.replace(
        "<PackageFormat>1.2</PackageFormat>", "<PackageFormat>1.1</PackageFormat>"
    )
    path = make_pisi(tmp_path / "demo-app.pisi", xml=xml)
    with pytest.raises(ValueError, match="Unsupported Pisi Linux binary package format"):
        pisi.read_info(path)


def test_rejects_pisi_without_an_installation_archive(tmp_path):
    path = make_pisi(tmp_path / "demo-app.pisi", with_image=False)
    with pytest.raises(ValueError, match="required Pisi Linux binary package files"):
        pisi.read_info(path)


@pytest.mark.parametrize(
    "xml,message",
    [
        ("<PISI>", "could not be read"),
        (
            METADATA.replace("<History>", "<Previous>").replace("</History>", "</Previous>"),
            "history",
        ),
        (METADATA.replace('release="12"', 'release="not-a-release"'), "Invalid Pisi Linux package"),
        (METADATA.replace("<Name>demo-app</Name>", "<Name>a</Name><Name>b</Name>"), "Name"),
    ],
)
def test_rejects_invalid_package_metadata(tmp_path, xml, message):
    path = make_pisi(tmp_path / "demo-app.pisi", xml=xml)
    with pytest.raises(ValueError, match=message):
        pisi.read_info(path)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_rejects_xml_document_type_declarations(tmp_path, encoding):
    xml = '<!DOCTYPE PISI [<!ENTITY name "demo-app">]>' + METADATA
    path = make_pisi(tmp_path / "demo-app.pisi", xml=xml.encode(encoding))
    with pytest.raises(ValueError, match="document type declaration"):
        pisi.read_info(path)


def test_rejects_duplicate_metadata_member(tmp_path):
    path = make_pisi(tmp_path / "demo-app.pisi")
    with ZipFile(path, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
        archive.writestr("metadata.xml", METADATA)
    with pytest.raises(ValueError, match="duplicate Pisi Linux package member"):
        pisi.read_info(path)


def test_rejects_symlink_for_installation_archive(tmp_path):
    path = make_pisi(tmp_path / "demo-app.pisi", with_image=False)
    member = ZipInfo("install.tar.xz")
    member.create_system = 3
    member.external_attr = stat.S_IFLNK << 16
    with ZipFile(path, "a") as archive:
        archive.writestr(member, "another.tar.xz")
    with pytest.raises(ValueError, match="Invalid or duplicate Pisi Linux package member"):
        pisi.read_info(path)


def test_bounds_package_metadata(tmp_path, monkeypatch):
    path = make_pisi(tmp_path / "demo-app.pisi")
    monkeypatch.setattr(pisi, "MAX_METADATA", 100)
    with pytest.raises(ValueError, match="metadata exceeds the read limit"):
        pisi.read_info(path)


def test_bounds_zip_directory(tmp_path, monkeypatch):
    path = make_pisi(tmp_path / "demo-app.pisi")
    monkeypatch.setattr(pisi, "MAX_ZIP_DIRECTORY", 100)
    with pytest.raises(ValueError, match="directory exceeds the read limit"):
        pisi.read_info(path)


def test_reports_pisi_architecture_mismatch(tmp_path, monkeypatch):
    path = make_pisi(tmp_path / "demo-app.pisi")
    monkeypatch.setattr(metadata.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="Architecture x86_64 is incompatible with aarch64"):
        metadata.inspect_package(path, None)

"""Inspect binary packages from Pisi Linux (.pisi) and Solus (.eopkg).

Solus's eopkg is a fork of PiSi and keeps its binary format: a ZIP with metadata.xml,
files.xml and install.tar.xz. The metadata's Distribution tells them apart.
"""

import io
import re
import stat
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from multipak.localization import tr

MAX_METADATA = 1024 * 1024
MAX_ZIP_DIRECTORY = 4 * 1024 * 1024
REQUIRED_MEMBERS = ("metadata.xml", "files.xml", "install.tar.xz")
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]*")
XML_LANGUAGE = "{http://www.w3.org/XML/1998/namespace}lang"
DEPENDENCY_LIMITS = (
    ("version", "version ="),
    ("versionFrom", "version >="),
    ("versionTo", "version <="),
    ("release", "release ="),
    ("releaseFrom", "release >="),
    ("releaseTo", "release <="),
)
# Distribution names, without spaces or punctuation, and MultiPak's family for each.
DISTRIBUTIONS = {"pisilinux": "pisilinux", "pisignulinux": "pisilinux", "solus": "solus"}
LABELS = {"pisilinux": "Pisi Linux", "solus": "Solus"}


@dataclass(frozen=True)
class Info:
    name: str
    version: str
    release: str
    architecture: str
    distribution_release: str
    summary: str
    dependencies: str
    build: str = ""
    # pisilinux or solus.
    family: str = "pisilinux"


class _ZipReader(io.BufferedReader):
    """Limit the central directory read made by zipfile, even for ZIP64 files."""

    def __init__(self, raw, label: str):
        super().__init__(raw)
        self.label = label

    def read(self, size: int = -1) -> bytes:
        message = tr("{0} package directory exceeds the read limit.").format(self.label)
        if size > MAX_ZIP_DIRECTORY:
            raise ValueError(message)
        data = super().read(MAX_ZIP_DIRECTORY + 1 if size < 0 else size)
        if len(data) > MAX_ZIP_DIRECTORY:
            raise ValueError(message)
        return data


class _MetadataTree(ElementTree.TreeBuilder):
    def __init__(self, label: str):
        super().__init__()
        self.label = label

    def doctype(self, name: str, public_id: str | None, system_id: str | None) -> None:
        raise ValueError(
            tr("{0} metadata XML must not contain a document type declaration.").format(self.label)
        )


def read_info(path: Path) -> Info:
    """Read metadata without unpacking the installation archive or running scripts."""
    # Until the metadata names the distribution, messages follow the file name.
    label = LABELS["solus"] if path.name.lower().endswith(".eopkg") else LABELS["pisilinux"]
    if re.search(r"\.delta\.(?:pisi|eopkg)$", path.name, re.IGNORECASE):
        # A delta package holds only the files changed since an earlier release.
        raise ValueError(
            tr(
                "This is a {0} delta package, which only updates an installed earlier "
                "version. Download the full package instead."
            ).format(label)
        )
    try:
        with (
            _ZipReader(path.open("rb", buffering=0), label) as stream,
            ZipFile(stream) as archive,
        ):
            raw = _metadata_xml(archive, label)
        root = ElementTree.fromstring(
            raw, parser=ElementTree.XMLParser(target=_MetadataTree(label))
        )
    except (
        BadZipFile,
        OSError,
        EOFError,
        NotImplementedError,
        zlib.error,
        ElementTree.ParseError,
        LookupError,
    ) as error:
        raise ValueError(
            tr("The {0} package could not be read: {1}").format(label, error)
        ) from error

    packages = root.findall("Package")
    if root.tag != "PISI" or len(packages) != 1:
        raise ValueError(tr("Invalid {0} binary package metadata.").format(label))
    package = packages[0]
    distribution = _field(package, "Distribution", label)
    family = DISTRIBUTIONS.get(re.sub(r"[^a-z]", "", distribution.lower()))
    if family is None:
        raise ValueError(tr("The package metadata does not identify Pisi Linux or Solus."))
    label = LABELS[family]
    if _field(package, "PackageFormat", label) != "1.2":
        raise ValueError(tr("Unsupported {0} binary package format.").format(label))
    name = _field(package, "Name", label)
    architecture = _field(package, "Architecture", label).lower()
    architecture = {"x86-64": "x86_64", "amd64": "x86_64", "arm64": "aarch64"}.get(
        architecture, architecture
    )
    distribution_release = _field(package, "DistributionRelease", label)
    updates = package.findall("History/Update")
    if not updates:
        raise ValueError(tr("The {0} package has no release history.").format(label))
    version = _field(updates[0], "Version", label)
    release = updates[0].get("release", "").strip()
    build = _field(package, "Build", label) if package.find("Build") is not None else ""
    if (
        not PACKAGE_NAME.fullmatch(name)
        or not VERSION.fullmatch(version)
        or not re.fullmatch(r"[0-9]+", release)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", architecture)
    ):
        raise ValueError(
            tr("Invalid {0} package name, version, release or architecture.").format(label)
        )
    if build and not re.fullmatch(r"[0-9]+", build):
        raise ValueError(tr("Missing or invalid {0} metadata: {1}.").format(label, "Build"))
    summaries = package.findall("Summary")
    english = next((item for item in summaries if item.get(XML_LANGUAGE) == "en"), None)
    summary = english if english is not None else next(iter(summaries), None)
    return Info(
        name,
        version,
        release,
        architecture,
        distribution_release,
        " ".join("".join(summary.itertext()).split()) if summary is not None else "",
        _dependencies(package, label),
        build,
        family,
    )


def _metadata_xml(archive: ZipFile, label: str) -> bytes:
    members: dict[str, ZipInfo] = {}
    for member in archive.infolist():
        if member.filename not in REQUIRED_MEMBERS:
            continue
        if (
            member.filename in members
            or member.filename != member.orig_filename
            or member.file_size <= 0
            or member.flag_bits & 1
            or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
        ):
            raise ValueError(
                tr("Invalid or duplicate {0} package member: {1}.").format(label, member.filename)
            )
        members[member.filename] = member
    if len(members) != len(REQUIRED_MEMBERS):
        raise ValueError(
            tr("The ZIP does not contain the required {0} binary package files.").format(label)
        )
    metadata = members["metadata.xml"]
    if metadata.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
        raise ValueError(tr("Unsupported {0} metadata compression.").format(label))
    if metadata.file_size > MAX_METADATA or metadata.compress_size > MAX_METADATA + 65536:
        raise ValueError(tr("{0} package metadata exceeds the read limit.").format(label))
    with archive.open(metadata) as source:
        return source.read()


def _field(element: ElementTree.Element, key: str, label: str) -> str:
    fields = element.findall(key)
    if len(fields) != 1 or len(fields[0]):
        raise ValueError(tr("Missing or invalid {0} metadata: {1}.").format(label, key))
    value = (fields[0].text or "").strip()
    if not value:
        raise ValueError(tr("Missing or invalid {0} metadata: {1}.").format(label, key))
    return value


def _dependencies(package: ElementTree.Element, label: str) -> str:
    dependencies = [
        _dependency(entry, label) for entry in package.findall("RuntimeDependencies/Dependency")
    ]
    for group in package.findall("RuntimeDependencies/AnyDependency"):
        alternatives = [_dependency(entry, label) for entry in group.findall("Dependency")]
        if not alternatives:
            raise ValueError(tr("Invalid {0} package dependency.").format(label))
        dependencies.append(" | ".join(alternatives))
    for component in package.findall("RuntimeDependencies/Component"):
        name = (component.text or "").strip()
        if not PACKAGE_NAME.fullmatch(name):
            raise ValueError(tr("Invalid {0} package dependency.").format(label))
        dependencies.append(tr("Component {0}").format(name))
    return "\n".join(dependencies)


def _dependency(entry: ElementTree.Element, label: str) -> str:
    name = (entry.text or "").strip()
    if not PACKAGE_NAME.fullmatch(name):
        raise ValueError(tr("Invalid {0} package dependency.").format(label))
    limits = []
    for key, limit in DEPENDENCY_LIMITS:
        value = entry.get(key, "").strip()
        if value:
            if not VERSION.fullmatch(value):
                raise ValueError(tr("Invalid {0} package dependency.").format(label))
            limits.append(f"{limit} {value}")
    return f"{name} ({', '.join(limits)})" if limits else name

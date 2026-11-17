"""Read the property lists at the start of a Void Linux binary package."""

import plistlib
import re
import tarfile
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

from multipak.localization import tr


@dataclass(frozen=True)
class Info:
    name: str
    version: str
    architecture: str
    dependencies: str
    description: str


def read_info(archive: tarfile.TarFile) -> Info | None:
    metadata = {}
    # xbps-create writes optional scripts first, then the two plists, then the payload.
    for member in archive:
        name = member.name.removeprefix("./")
        if name in ("INSTALL", "REMOVE", "INSTALL.msg", "REMOVE.msg", "."):
            continue
        if name not in ("props.plist", "files.plist"):
            break
        if not member.isfile() or name in metadata or member.size > 24 * 1024 * 1024:
            raise ValueError(tr("Invalid XBPS metadata file: {0}.").format(name))
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(tr("Invalid XBPS metadata file: {0}.").format(name))
        try:
            value = plistlib.loads(source.read(), fmt=plistlib.FMT_XML)
        except (ValueError, TypeError, OverflowError, ExpatError) as exc:
            raise ValueError(tr("Invalid XBPS metadata file: {0}.").format(name)) from exc
        if not isinstance(value, dict):
            raise ValueError(tr("Invalid XBPS metadata file: {0}.").format(name))
        metadata[name] = value
        if len(metadata) == 2:
            return _properties(metadata["props.plist"])
    return None


def _properties(props: dict) -> Info:
    name = props.get("pkgname")
    pkgver = props.get("pkgver")
    arch = props.get("architecture")
    description = props.get("short_desc", "")
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name)
        or not isinstance(pkgver, str)
        or not pkgver.startswith(name + "-")
        or not isinstance(arch, str)
        or not re.fullmatch(r"[A-Za-z0-9_]+(?:-musl)?", arch)
        or not isinstance(description, str)
    ):
        raise ValueError(tr("Invalid XBPS package properties."))
    version = pkgver.removeprefix(name + "-")
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+_]*_[0-9]+", version):
        raise ValueError(tr("Invalid XBPS package version."))

    lists = {}
    for key in ("run_depends", "shlib-requires"):
        values = props.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(tr("Invalid XBPS dependency list: {0}.").format(key))
        lists[key] = values
    # xbps-src already adds the package that provides each required library to
    # run_depends; the library names only help when a hand-made package lists none.
    dependencies = lists["run_depends"] or lists["shlib-requires"]
    return Info(name, version, arch, "\n".join(dict.fromkeys(dependencies)), description)

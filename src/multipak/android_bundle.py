"""Read Android bundles and select split APKs without executing their contents.

Bundle inspection and split classification follow App Manager's ApkFile.java:
https://github.com/MuntashirAkon/AppManager/blob/master/app/src/main/java/io/github/muntashirakon/AppManager/apk/ApkFile.java
SPDX-License-Identifier: GPL-3.0-or-later
"""

import hashlib
import json
import re
import stat
import struct
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .android_apk import (
    ANDROID_NS,
    MAX_ENTRIES,
    MAX_MANIFEST,
    _read,
    inspect_apk,
    manifest_elements,
)
from .localization import tr
from .models import AndroidPart

MAX_APKS = 512
MAX_APK_SIZE = 2 * 1024**3
MAX_BUNDLE_SIZE = 8 * 1024**3
ABI_NAMES = {
    "arm64_v8a": "arm64-v8a",
    "armeabi_v7a": "armeabi-v7a",
    "armeabi": "armeabi",
    "x86": "x86",
    "x86_64": "x86_64",
    "mips": "mips",
    "mips64": "mips64",
    "riscv64": "riscv64",
}
DENSITIES = {
    "ldpi": 120,
    "mdpi": 160,
    "tvdpi": 213,
    "hdpi": 240,
    "xhdpi": 320,
    "xxhdpi": 480,
    "xxxhdpi": 640,
}


def _entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES or len({item.filename for item in entries}) != len(entries):
        raise ValueError("Too many or duplicate bundle entries")
    apks = [item for item in entries if item.filename.lower().endswith(".apk")]
    if not apks or len(apks) > MAX_APKS or sum(item.file_size for item in apks) > MAX_BUNDLE_SIZE:
        raise ValueError("Missing APKs or bundle exceeds the size limit")
    payloads = [i for i in entries if i.filename.lower().endswith((".apk", ".obb"))]
    if sum(i.file_size for i in payloads) > MAX_BUNDLE_SIZE:
        raise ValueError("Bundle exceeds the size limit")
    for item in payloads:
        path = PurePosixPath(item.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in item.filename
            or "\0" in item.filename
            or item.is_dir()
            or stat.S_ISLNK(item.external_attr >> 16)
            or item.flag_bits & 1
            or not 0 < item.file_size <= MAX_APK_SIZE
            or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
        ):
            raise ValueError("Invalid, encrypted or oversized bundle APK")
    return apks


def _copy(source, target, limit, checkpoint):
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(1024 * 1024):
        checkpoint()
        size += len(chunk)
        if size > limit:
            raise ValueError("Bundle APK exceeds the size limit")
        target.write(chunk)
        digest.update(chunk)
    return digest.hexdigest(), size


def _part(path, member, digest, size):
    with zipfile.ZipFile(path) as apk:
        if len(apk.infolist()) > MAX_ENTRIES:
            raise ValueError("Too many APK archive entries")
        elements = manifest_elements(_read(apk, "AndroidManifest.xml", MAX_MANIFEST))
        root = elements[0][2]

        def attr(name, default="", namespace=ANDROID_NS):
            return root.get((namespace, name), (0, default))[1]

        package_id = attr("package", namespace="")
        code, major = attr("versionCode", 0), attr("versionCodeMajor", 0)
        if (
            not isinstance(code, int)
            or not 0 < code < 2**32
            or not isinstance(major, int)
            or major < 0
        ):
            raise ValueError("Invalid Android version code")
        split_id = attr("split", namespace="")
        if (
            not isinstance(split_id, str)
            or split_id
            and not re.fullmatch(r"[A-Za-z0-9_.]+", split_id)
        ):
            raise ValueError("Invalid Android split identifier")
        target = attr("configForSplit", namespace="") or attr("configForSplit")
        if not isinstance(target, str):
            raise ValueError("Invalid Android split target")
        abis = tuple(
            sorted(
                {
                    item.filename.split("/")[1]
                    for item in apk.infolist()
                    if re.fullmatch(r"lib/[A-Za-z0-9_-]+/[^/]+\.so", item.filename)
                }
            )
        )
        kind, qualifier = ("base" if not split_id else "feature"), ""
        if split_id and not attr("isFeatureSplit", 0):
            match = re.fullmatch(r"(?:(.+)\.)?config\.(.+)", split_id)
            if match:
                inferred_target, qualifier = match.groups()
                target = target or inferred_target or ""
                if qualifier in ABI_NAMES:
                    kind, qualifier = "abi", ABI_NAMES[qualifier]
                    if abis and abis != (qualifier,):
                        raise ValueError("Split ABI does not match its native libraries")
                    abis = (qualifier,)
                elif qualifier in DENSITIES or re.fullmatch(r"[1-9][0-9]*dpi", qualifier):
                    kind = "density"
                elif re.fullmatch(r"[a-z]{2,3}(?:[-_]r?[A-Za-z0-9]+)*", qualifier):
                    kind = "locale"
                else:
                    raise ValueError(tr("Unsupported Android split: {0}").format(split_id))
        dependencies = tuple(
            attrs.get((ANDROID_NS, "name"), (0, ""))[1]
            for parents, tag, attrs in elements
            if parents == ("manifest",) and tag == "uses-split"
        )
        sdk = next(
            (
                attrs.get((ANDROID_NS, "minSdkVersion"), (0, 1))[1]
                for parents, tag, attrs in elements
                if parents == ("manifest",) and tag == "uses-sdk"
            ),
            1,
        )
        if not isinstance(sdk, int) or sdk < 1:
            raise ValueError("Invalid minimum Android SDK")
        types = []
        for key in ("requiredSplitTypes", "splitTypes"):
            value = attr(key)
            if not isinstance(value, str):
                raise ValueError("Invalid Android split types")
            types.append(tuple(value.split(",")) if value else ())
        return (
            package_id,
            (major << 32) | code,
            AndroidPart(
                member,
                split_id,
                size,
                digest,
                kind,
                target,
                qualifier,
                abis,
                dependencies,
                *types,
                sdk,
            ),
        )


def inspect_android(path, *, checkpoint=lambda: None, _group=None):
    """Accept APK, APKM, APKS and XAPK by contents, regardless of extension."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            if "AndroidManifest.xml" in archive.namelist():
                return inspect_apk(path)
            if "toc.pb" in archive.namelist() and _group is None:
                from .android_apks import groups

                variants = []
                package = None
                for group in groups(_read(archive, "toc.pb", 8 * 1024 * 1024)):
                    current = inspect_android(path, checkpoint=checkpoint, _group=group)
                    if package and (current.desktop_entry, current.android_version_code) != (
                        package.desktop_entry,
                        package.android_version_code,
                    ):
                        raise ValueError("APKS variants have different package identities")
                    package = package or current
                    variants.append({**group, "parts": current.android_parts})
                return replace(
                    package,
                    android_bundle_format="APKS",
                    android_variants=tuple(variants),
                    android_min_sdk=min(
                        p.min_sdk for v in variants for p in v["parts"] if p.kind == "base"
                    ),
                    architecture=",".join(
                        sorted({abi for v in variants for p in v["parts"] for abi in p.abis})
                    )
                    or "any",
                )
            parts, base = [], None
            identity = None
            with tempfile.TemporaryDirectory(prefix="multipak-apkm-") as directory:
                members = {e["member"] for e in _group["entries"]} if _group else None
                available = _entries(archive)
                if members is not None and not members <= {i.filename for i in available}:
                    raise ValueError("APKS index references missing APKs")
                for index, item in enumerate(available):
                    if members is not None and item.filename not in members:
                        continue
                    checkpoint()
                    target = Path(directory) / f"{index}.apk"
                    with archive.open(item) as source, target.open("wb") as output:
                        digest, size = _copy(source, output, item.file_size, checkpoint)
                    if size != item.file_size:
                        raise ValueError("Incomplete bundle APK")
                    package_id, code, part = _part(target, item.filename, digest, size)
                    if identity is None:
                        identity = package_id, code
                    if identity != (package_id, code):
                        raise ValueError(
                            tr(
                                "The APKs in this bundle belong to different applications "
                                "or versions."
                            )
                        )
                    if part.kind == "base":
                        if base is not None:
                            raise ValueError("Duplicate base APK")
                        base = inspect_apk(target, bundle_base=True)
                    parts.append(part)
                    target.unlink()
            if base is None or len({p.split_id for p in parts}) != len(parts):
                raise ValueError("Missing base APK or duplicate split identifiers")
            ids = {p.split_id for p in parts}
            for part in parts:
                if part.target not in ids or any(dep not in ids for dep in part.dependencies):
                    raise ValueError(tr("This Android bundle is missing required parts."))
            architecture = ",".join(sorted({abi for part in parts for abi in part.abis})) or "any"
            name, icon = base.name, base.icon_data
            # Optional bundle metadata is only a display fallback. Manifest package
            # identity, versions and compatibility always come from the APKs.
            if name == base.desktop_entry and "info.json" in archive.namelist():
                try:
                    info = json.loads(_read(archive, "info.json", 64 * 1024))
                    label = info.get("app_name") if isinstance(info, dict) else None
                    if (
                        isinstance(info, dict)
                        and info.get("pname") == base.desktop_entry
                        and isinstance(label, str)
                        and 0 < len(label.strip()) <= 1024
                        and not any(ord(c) < 32 for c in label)
                    ):
                        name = label.strip()
                except (ValueError, UnicodeError):
                    pass
            if not icon and "icon.png" in archive.namelist():
                try:
                    png = _read(archive, "icon.png", 2 * 1024 * 1024)
                    if png.startswith(b"\x89PNG\r\n\x1a\n"):
                        icon = png
                except ValueError:
                    pass
            expansions = []
            destinations = set()
            for item in archive.infolist():
                if not item.filename.lower().endswith(".obb"):
                    continue
                filename = PurePosixPath(item.filename).name
                pattern = (
                    r"(?:main|patch)\.[1-9][0-9]*\." + re.escape(base.desktop_entry) + r"\.obb"
                )
                if not re.fullmatch(pattern, filename) or filename in destinations:
                    raise ValueError("Invalid or duplicate OBB destination")
                allowed = {
                    filename,
                    base.desktop_entry + "/" + filename,
                    "Android/obb/" + base.desktop_entry + "/" + filename,
                }
                if item.filename not in allowed:
                    raise ValueError("Invalid OBB archive path")
                destinations.add(filename)
                with archive.open(item) as source:
                    digest = hashlib.sha256()
                    count = 0
                    while chunk := source.read(1024 * 1024):
                        checkpoint()
                        count += len(chunk)
                        if count > item.file_size:
                            raise ValueError("OBB exceeds its declared size")
                        digest.update(chunk)
                if count != item.file_size:
                    raise ValueError("Incomplete OBB")
                expansions.append(
                    AndroidPart(item.filename, filename, count, digest.hexdigest(), kind="obb")
                )
            bundle_format = (
                "XAPK" if expansions or "manifest.json" in archive.namelist() else "APKM"
            )
            return replace(
                base,
                path=path,
                name=name,
                icon_data=icon,
                icon_extension=".png" if icon else "",
                architecture=architecture,
                android_parts=tuple(sorted(parts, key=lambda p: p.split_id)),
                android_bundle_format=bundle_format,
                android_expansions=tuple(expansions),
            )
    except (
        zipfile.BadZipFile,
        KeyError,
        OSError,
        ValueError,
        RuntimeError,
        struct.error,
        UnicodeError,
    ) as error:
        raise ValueError(tr("Could not read the Android package: {0}").format(error)) from error


def select_parts(package, abis, density):
    """Keep base/features/locales and one compatible ABI and density per feature."""
    parts = package.android_parts
    selected = [p for p in parts if p.kind not in ("abi", "density")]
    groups = {p.target for p in parts if p.kind == "abi"}
    # One ABI across the whole application avoids mixing 32- and 64-bit features.
    candidates = [
        abi
        for abi in abis
        if all(
            any(p.target == group and p.kind == "abi" and p.qualifier == abi for p in parts)
            for group in groups
        )
        and all(not p.abis or abi in p.abis for p in selected)
    ]
    if (groups or any(p.abis for p in selected)) and not candidates:
        raise ValueError(
            tr(
                "This bundle has no compatible Android architecture. "
                "An ARM translation layer may be required."
            )
        )
    if groups:
        selected.extend(p for p in parts if p.kind == "abi" and p.qualifier == candidates[0])
    for group in {p.target for p in parts if p.kind == "density"}:
        choices = [p for p in parts if p.kind == "density" and p.target == group]

        def distance(part):
            dpi = DENSITIES.get(part.qualifier)
            if dpi is None:
                dpi = int(part.qualifier.removesuffix("dpi"))
            return abs(dpi - density), -dpi

        selected.append(min(choices, key=distance))
    provided = {value for part in selected for value in part.split_types}
    ids = {part.split_id for part in selected}
    if any(
        not set(p.required_types) <= provided or not set(p.dependencies) <= ids for p in selected
    ):
        raise ValueError(tr("This Android bundle is missing required parts."))
    return tuple(sorted(selected, key=lambda p: p.split_id))


def extract_parts(path, selected, directory, *, checkpoint=lambda: None):
    """Use generated destination names and verify every retained APK's digest."""
    result = []
    with zipfile.ZipFile(path) as archive:
        entries = {item.filename: item for item in _entries(archive)}
        for index, part in enumerate(selected):
            target = Path(directory) / f"part-{index}.apk"
            with archive.open(entries[part.member]) as source, target.open("xb") as output:
                digest, size = _copy(source, output, part.size, checkpoint)
            if digest != part.sha256 or size != part.size:
                raise ValueError(tr("The file has changed. Open the package again."))
            result.append(target)
    return tuple(result)


def select_variant(package, sdk, abis, density):
    """Choose one complete APKS variant, never mixing APKs across variants."""
    if not package.android_variants:
        return package
    from .android_apks import matches

    for variant in sorted(package.android_variants, key=lambda v: v["number"], reverse=True):
        if not matches(variant["target"], sdk, abis, density):
            continue
        entries = {
            e["member"]
            for e in variant["entries"]
            if matches(e["target"], sdk, abis, density)
            and matches(e.get("module_target", {}), sdk, abis, density)
        }
        parts = tuple(p for p in variant["parts"] if p.member in entries)
        if sum(p.kind == "base" for p in parts) != 1:
            continue
        candidate = replace(package, android_parts=parts, android_variants=())
        try:
            selected = select_parts(candidate, abis, density)
        except ValueError:
            continue
        if all(p.min_sdk <= sdk for p in selected):
            return candidate
    raise ValueError(tr("This APKS bundle has no variant compatible with the selected Android."))


def extract_expansions(path, parts, directory, *, checkpoint=lambda: None):
    result = []
    with zipfile.ZipFile(path) as archive:
        _entries(archive)
        for index, part in enumerate(parts):
            target = Path(directory) / f"expansion-{index}.obb"
            with archive.open(part.member) as source, target.open("xb") as output:
                digest, size = _copy(source, output, part.size, checkpoint)
            if (digest, size) != (part.sha256, part.size):
                raise ValueError(tr("The file has changed. Open the package again."))
            result.append(target)
    return tuple(result)

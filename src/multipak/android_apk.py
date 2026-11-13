"""Read Android binary XML and APK metadata without executing package contents."""

import re
import struct
import zipfile
from pathlib import Path

from .localization import tr
from .models import Package

ANDROID_NS = "http://schemas.android.com/apk/res/android"
NO_INDEX = 0xFFFFFFFF
MAX_MANIFEST = 4 * 1024 * 1024
MAX_RESOURCES = 32 * 1024 * 1024
MAX_ENTRIES = 100_000


def valid_package_id(value):
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", value))


def _chunks(data, start, end):
    while start < end:
        if start + 8 > end:
            raise ValueError("Truncated Android resource chunk")
        kind, header, size = struct.unpack_from("<HHI", data, start)
        if header < 8 or size < header or start + size > end:
            raise ValueError("Invalid Android resource chunk")
        yield kind, header, data[start : start + size]
        start += size


def _strings(data):
    if len(data) < 28:
        raise ValueError("Invalid Android string pool")
    _, header, size, count, styles, flags, offset, _ = struct.unpack_from("<HH6I", data)
    if count > MAX_MANIFEST // 4 or header + 4 * (count + styles) > offset or offset > size:
        raise ValueError("Invalid Android string pool offsets")
    result = []
    utf8 = bool(flags & 0x100)

    def length(pos):
        unit = 1 if utf8 else 2
        if pos + unit > len(data):
            raise ValueError("Truncated Android string length")
        first = int.from_bytes(data[pos : pos + unit], "little")
        pos += unit
        mask = 0x80 if utf8 else 0x8000
        if first & mask:
            if pos + unit > len(data):
                raise ValueError("Truncated Android string length")
            first = ((first & (mask - 1)) << (unit * 8)) | int.from_bytes(
                data[pos : pos + unit], "little"
            )
            pos += unit
        return first, pos

    for index in range(count):
        pos = offset + struct.unpack_from("<I", data, header + index * 4)[0]
        size, pos = length(pos)
        if utf8:
            size, pos = length(pos)
        else:
            size *= 2
        terminator = 1 if utf8 else 2
        if pos + size + terminator > len(data) or any(data[pos + size : pos + size + terminator]):
            raise ValueError("Invalid Android string")
        result.append(data[pos : pos + size].decode("utf-8" if utf8 else "utf-16-le"))
    return result


def manifest_elements(data):
    if len(data) < 8:
        raise ValueError("Missing Android binary XML")
    kind, header, size = struct.unpack_from("<HHI", data)
    if kind != 3 or header != 8 or size != len(data):
        raise ValueError("Invalid Android binary XML")
    strings, stack, result = None, [], []

    def string(index):
        if strings is None or index >= len(strings):
            raise ValueError("Invalid Android string reference")
        return strings[index]

    for kind, chunk_header, chunk in _chunks(data, header, size):
        if kind == 1:
            if strings is not None:
                raise ValueError("Duplicate Android string pool")
            strings = _strings(chunk)
        elif kind == 0x102:
            if chunk_header != 16 or len(chunk) < 36:
                raise ValueError("Invalid Android XML element")
            namespace, name, start, stride, count = struct.unpack_from("<IIHHH", chunk, 16)
            if stride < 20 or start < 20 or 16 + start + stride * count > len(chunk):
                raise ValueError("Invalid Android XML attributes")
            tag = string(name)
            attrs = {}
            for index in range(count):
                pos = 16 + start + index * stride
                ns, key, raw, value_size, zero, value_type, value = struct.unpack_from(
                    "<IIIHBBI", chunk, pos
                )
                if value_size != 8 or zero:
                    raise ValueError("Invalid Android XML value")
                key = ("" if ns == NO_INDEX else string(ns), string(key))
                if key in attrs:
                    raise ValueError("Duplicate Android XML attribute")
                if value_type == 3:
                    decoded = string(value)
                elif raw != NO_INDEX and value_type not in (1, 2):
                    decoded = string(raw)
                else:
                    decoded = value
                attrs[key] = (value_type, decoded)
            result.append((tuple(stack), tag, attrs))
            stack.append(tag)
        elif kind == 0x103:
            if (
                chunk_header != 16
                or len(chunk) < 24
                or not stack
                or stack.pop() != string(struct.unpack_from("<I", chunk, 20)[0])
            ):
                raise ValueError("Unbalanced Android XML")
    if stack or not result or result[0][:2] != ((), "manifest"):
        raise ValueError("Missing Android manifest root")
    return result


def _resource_strings(data, wanted):
    """Resolve simple string resources, preferring the default locale."""
    resolved = {}
    if len(data) < 12:
        return resolved
    kind, header, size = struct.unpack_from("<HHI", data)
    if kind != 2 or size != len(data):
        return resolved
    strings = []
    for kind, _, chunk in _chunks(data, header, size):
        if kind == 1:
            strings = _strings(chunk)
        elif kind == 0x200 and len(chunk) >= 284:
            package_id = struct.unpack_from("<I", chunk, 8)[0]
            package_header = struct.unpack_from("<H", chunk, 2)[0]
            type_offset = struct.unpack_from("<I", chunk, 284)[0] if package_header >= 288 else 0
            for subtype, subheader, sub in _chunks(chunk, package_header, len(chunk)):
                if subtype != 0x201 or subheader < 28:
                    continue
                type_id, flags, _, count, offset = struct.unpack_from("<BBHII", sub, 8)
                if flags or count > MAX_RESOURCES // 4 or subheader + count * 4 > len(sub):
                    continue  # Sparse/compact tables are optional; fall back to the package ID.
                default_locale = not any(sub[28:32])
                for resource in wanted:
                    if (
                        resource >> 24 != package_id
                        or (resource >> 16) & 0xFF != type_id + type_offset
                    ):
                        continue
                    index = resource & 0xFFFF
                    if index >= count:
                        continue
                    entry_offset = struct.unpack_from("<I", sub, subheader + index * 4)[0]
                    if entry_offset == NO_INDEX:
                        continue
                    pos = offset + entry_offset
                    if pos + 8 > len(sub):
                        continue
                    entry_size, entry_flags, _ = struct.unpack_from("<HHI", sub, pos)
                    pos += entry_size
                    if entry_size < 8 or entry_flags & 1 or pos + 8 > len(sub):
                        continue
                    value_size, zero, value_type, value = struct.unpack_from("<HBBI", sub, pos)
                    if value_size == 8 and not zero and value_type == 3 and value < len(strings):
                        if resource not in resolved or default_locale:
                            resolved[resource] = strings[value]
    return resolved


def _read(archive, name, limit):
    entries = [item for item in archive.infolist() if item.filename == name]
    if len(entries) != 1 or entries[0].file_size > limit or entries[0].flag_bits & 1:
        raise ValueError("Missing, duplicate, oversized or encrypted APK metadata")
    with archive.open(entries[0]) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("APK metadata exceeds the read limit")
    return data


def inspect_apk(path: Path, *, bundle_base=False) -> Package:
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > MAX_ENTRIES:
                raise ValueError("Too many APK archive entries")
            elements = manifest_elements(_read(archive, "AndroidManifest.xml", MAX_MANIFEST))
            root = elements[0][2]
            package_id = root.get(("", "package"), (0, ""))[1]
            if not isinstance(package_id, str) or not valid_package_id(package_id):
                raise ValueError("Invalid Android package ID")
            if (
                ("", "split") in root
                or not bundle_base
                and (
                    root.get((ANDROID_NS, "isSplitRequired"), (0, 0))[1]
                    or root.get((ANDROID_NS, "requiredSplitTypes"), (0, ""))[1]
                )
            ):
                raise ValueError(
                    tr("Choose a complete APK or open the APKM bundle containing all its parts.")
                )
            applications = [
                attrs
                for parents, tag, attrs in elements
                if parents == ("manifest",) and tag == "application"
            ]
            if len(applications) != 1:
                raise ValueError("Missing or duplicate Android application")
            app = applications[0]
            if not bundle_base and app.get((ANDROID_NS, "isSplitRequired"), (0, 0))[1]:
                raise ValueError(
                    tr("Choose a complete APK or open the APKM bundle containing all its parts.")
                )
            launcher = False
            actions, categories = set(), set()
            for parents, tag, attrs in elements:
                if tag == "intent-filter":
                    actions, categories = set(), set()
                if len(parents) >= 2 and parents[-2:] in (
                    ("activity", "intent-filter"),
                    ("activity-alias", "intent-filter"),
                ):
                    name = attrs.get((ANDROID_NS, "name"), (0, ""))[1]
                    if tag == "action":
                        actions.add(name)
                    elif tag == "category":
                        categories.add(name)
                    launcher |= "android.intent.action.MAIN" in actions and bool(
                        categories.intersection(
                            (
                                "android.intent.category.LAUNCHER",
                                "android.intent.category.LEANBACK_LAUNCHER",
                            )
                        )
                    )
            if not launcher:
                raise ValueError(tr("This APK does not provide an application launcher."))
            version = root.get((ANDROID_NS, "versionName"), (0, ""))
            code = root.get((ANDROID_NS, "versionCode"), (0, 0))[1]
            if not isinstance(code, int) or code < 1:
                raise ValueError("Invalid Android version code")
            major = root.get((ANDROID_NS, "versionCodeMajor"), (0, 0))[1]
            if not isinstance(major, int) or major < 0:
                raise ValueError("Invalid Android version code")
            label = app.get((ANDROID_NS, "label"), (3, package_id))
            icon = app.get((ANDROID_NS, "icon"), (0, ""))
            refs = {value for kind, value in (version, label, icon) if kind == 1}
            resources = {}
            if refs and "resources.arsc" in archive.namelist():
                try:
                    resources = _resource_strings(
                        _read(archive, "resources.arsc", MAX_RESOURCES), refs
                    )
                except (ValueError, struct.error, UnicodeError):
                    pass  # Resource labels are optional; installation uses the manifest identity.

            def resolve(value, fallback):
                kind, text = value
                return text if kind == 3 else resources.get(text, fallback)

            name = resolve(label, package_id)
            version_name = resolve(version, str(code))
            icon_path = resolve(icon, "")
            icon_data = b""
            if (
                isinstance(icon_path, str)
                and icon_path.startswith("res/")
                and icon_path.endswith(".png")
            ):
                icon_data = _read(archive, icon_path, 2 * 1024 * 1024)
                if not icon_data.startswith(b"\x89PNG\r\n\x1a\n"):
                    icon_data = b""
            abis = sorted(
                {
                    item.filename.split("/")[1]
                    for item in archive.infolist()
                    if re.fullmatch(r"lib/[A-Za-z0-9_-]+/[^/]+\.so", item.filename)
                }
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
            if not isinstance(name, str) or not name.strip() or len(name) > 1024:
                name = package_id
            target_sdk = next(
                (
                    a.get((ANDROID_NS, "targetSdkVersion"), (0, 0))[1]
                    for parents, tag, a in elements
                    if parents == ("manifest",) and tag == "uses-sdk"
                ),
                0,
            )
            if not isinstance(target_sdk, int) or not 0 <= target_sdk < 2**31:
                raise ValueError("Invalid Android target SDK")
            return Package(
                Path(path),
                "apk",
                name,
                str(version_name),
                ",".join(abis) or "any",
                desktop_entry=package_id,
                icon_data=icon_data,
                icon_extension=".png" if icon_data else "",
                android_version_code=(major << 32) | code,
                android_min_sdk=sdk,
                android_target_sdk=target_sdk,
            )
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, struct.error, UnicodeError) as error:
        raise ValueError(tr("Could not read the Android APK: {0}").format(error)) from error

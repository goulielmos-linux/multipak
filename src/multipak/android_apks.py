"""Read bundletool variant metadata with bounded protobuf parsing.

Field numbers follow Google's bundletool commands.proto and targeting.proto:
https://github.com/google/bundletool/tree/master/src/main/proto
No Java runtime or execution of bundle contents is required.
"""

from .localization import tr

ABI = {
    1: "armeabi",
    2: "armeabi-v7a",
    3: "arm64-v8a",
    4: "x86",
    5: "x86_64",
    6: "mips",
    7: "mips64",
    8: "riscv64",
}
DPI = {1: 0, 2: 120, 3: 160, 4: 213, 5: 240, 6: 320, 7: 480, 8: 640}


def fields(data):
    if not isinstance(data, bytes) or len(data) > 8 * 1024 * 1024:
        raise ValueError("Invalid APKS metadata")
    position = 0

    def varint():
        nonlocal position
        result = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                break
            value = data[position]
            position += 1
            result |= (value & 127) << shift
            if value < 128:
                return result
        raise ValueError("Invalid protobuf integer")

    result = {}
    count = 0
    while position < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else 8 if wire == 1 else 4
            if position + length > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[position : position + length]
            position += length
        else:
            raise ValueError("Unsupported protobuf wire type")
        result.setdefault(number, []).append(value)
        count += 1
        if count > 100000:
            raise ValueError("Too many protobuf fields")
    return result


def message(record, key):
    return fields(record.get(key, [b""])[0])


def targeting(data, *, variant=False):
    record = fields(data)
    allowed = {1, 2, 3} if variant else {1, 3, 4, 5}
    if any(key not in allowed and any(value for value in values) for key, values in record.items()):
        raise ValueError(tr("This APKS bundle uses device targeting not supported yet."))
    result = {}
    for name, key in (
        ("sdk", 1 if variant else 5),
        ("abi", 2 if variant else 1),
        ("density", 3 if variant else 4),
    ):
        dimensions = message(record, key)
        parsed = []
        for kind in (1, 2):
            values = []
            for raw in dimensions.get(kind, []):
                value = fields(raw)
                if name == "sdk":
                    value = message(value, 1).get(1, [0])[0]
                elif name == "abi":
                    value = ABI.get(value.get(1, [0])[0])
                    if not value:
                        raise ValueError("Unknown APKS ABI")
                else:
                    value = value.get(2, [DPI.get(value.get(1, [0])[0], -1)])[0]
                    if value < 0:
                        raise ValueError("Unknown APKS density")
                values.append(value)
            parsed.append(tuple(values))
        result[name] = tuple(parsed)
    return result


def matches(target, sdk, abis, density):
    values, alternatives = target.get("sdk", ((), ()))
    minimum = min(values, default=0)
    upper = min((v for v in alternatives if v > minimum), default=10**9)
    if not minimum <= sdk < upper:
        return False
    values, alternatives = target.get("abi", ((), ()))
    choices = set(values + alternatives)
    best = next((abi for abi in abis if abi in choices), None)
    if choices and (best is None or bool(values) and best not in values):
        return False
    values, alternatives = target.get("density", ((), ()))
    choices = set(values + alternatives) - {0}
    best = min(choices, key=lambda value: (abs(value - density), -value), default=0)
    return not choices or not values or best in values


def groups(data):
    result = []
    root = fields(data)
    if root.get(3):
        raise ValueError(tr("APKS asset delivery bundles are not supported yet."))
    for index, raw in enumerate(root.get(1, [])):
        variant = fields(raw)
        target = targeting(variant.get(1, [b""])[0], variant=True)
        entries, standalone = [], []
        for raw_set in variant.get(2, []):
            apk_set = fields(raw_set)
            module = message(apk_set, 1)
            if module.get(3, [0])[0]:
                continue  # Instant apps are separate from persistent installations.
            module_target = message(module, 5)
            if any(key != 1 and any(values) for key, values in module_target.items()):
                raise ValueError(tr("This APKS bundle uses device targeting not supported yet."))
            module_target = targeting(module.get(5, [b""])[0], variant=True)
            for raw_apk in apk_set.get(2, []):
                apk = fields(raw_apk)
                if not (3 in apk or 4 in apk):
                    continue
                path = apk.get(2, [b""])[0].decode("utf-8")
                item = {
                    "member": path,
                    "target": targeting(apk.get(1, [b""])[0]),
                    "module_target": module_target,
                }
                (standalone if 4 in apk else entries).append(item)
        number = variant.get(3, [index])[0]
        if entries:
            result.append({"number": number, "target": target, "entries": entries})
        for item in standalone:
            result.append({"number": number, "target": target, "entries": [item]})
    if not result or len(result) > 512:
        raise ValueError("Missing or excessive APKS variants")
    return result

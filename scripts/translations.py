#!/usr/bin/env python3
"""Extract source messages and compile bundled gettext catalogs without extra dependencies."""

import argparse
import ast
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/multipak"


def source_messages():
    messages = set()
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("tr", "mark")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                messages.add(node.args[0].value)
    for path in (ROOT / "kirigami").glob("*.qml"):
        for match in re.finditer(r'qsTr\(\s*("(?:\\.|[^"\\])*")', path.read_text()):
            messages.add(json.loads(match[1]))
    return messages


def read_catalog(path):
    entries = {}
    source = translation = None
    field = None
    fuzzy = False

    def flush():
        if source is not None and translation is not None and not fuzzy:
            if source in entries:
                raise ValueError(f"Duplicate message in {path}: {source}")
            entries[source] = translation

    for raw in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = raw.strip()
        if not line:
            flush()
            source = translation = field = None
            fuzzy = False
        elif line.startswith("#, ") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("#"):
            continue
        elif line.startswith("msgid "):
            source, field = ast.literal_eval(line[6:]), "source"
        elif line.startswith("msgstr "):
            translation, field = ast.literal_eval(line[7:]), "translation"
        elif line.startswith('"'):
            if field == "source":
                source += ast.literal_eval(line)
            elif field == "translation":
                translation += ast.literal_eval(line)
            else:
                raise ValueError(f"Unexpected continuation in {path}")
        else:
            raise ValueError(f"Unsupported catalog entry in {path}: {line}")
    return entries


def placeholders(text):
    return sorted(re.findall(r"\{\d+(?:![ars])?\}|%\d+|%n", text))


def compile_catalog(path, messages):
    entries = read_catalog(path)
    missing = messages - entries.keys()
    if missing:
        raise ValueError(f"Missing translations in {path}: {sorted(missing)}")
    for source, translation in entries.items():
        if source and (not translation or placeholders(source) != placeholders(translation)):
            raise ValueError(f"Empty translation or changed placeholders in {path}: {source}")
    items = sorted((s.encode("utf-8"), t.encode("utf-8")) for s, t in entries.items())
    count = len(items)
    offset = 28 + count * 16
    ids, values, id_table, value_table = bytearray(), bytearray(), [], []
    for source, _ in items:
        id_table.extend((len(source), offset + len(ids)))
        ids.extend(source + b"\0")
    offset += len(ids)
    for _, translation in items:
        value_table.extend((len(translation), offset + len(values)))
        values.extend(translation + b"\0")
    header = struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
    tables = struct.pack(f"<{count * 4}I", *id_table, *value_table)
    path.with_suffix(".mo").write_bytes(header + tables + ids + values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template", action="store_true", help="Also update the POT source template"
    )
    args = parser.parse_args()
    messages = source_messages()
    if args.template:
        lines = [
            "# MultiPak translation template. English is the source language.",
            'msgid ""',
            'msgstr ""',
            '"Content-Type: text/plain; charset=UTF-8\\n"',
            "",
        ]
        for source in sorted(messages):
            lines += ["msgid " + json.dumps(source, ensure_ascii=False), 'msgstr ""', ""]
        (ROOT / "locales/multipak.pot").write_text("\n".join(lines), encoding="utf-8")
    for path in sorted((ROOT / "locales").glob("*/LC_MESSAGES/multipak.po")):
        compile_catalog(path, messages)
        print(f"Compiled {path.relative_to(ROOT)} ({len(messages)} source messages)")


if __name__ == "__main__":
    main()

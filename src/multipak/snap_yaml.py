"""A strict reader for the YAML subset used in meta/snap.yaml.

Snapcraft writes snap.yaml in block style: mappings, sequences, plain or quoted
scalars, block scalars (| and >) and small flow collections. Anything outside that
subset is rejected instead of guessed, so a malformed or hostile file can never be
read differently from how snapd would read it. Scalars are returned as strings;
callers convert the few numeric or boolean fields they use.
"""

import re

MAX_SIZE = 256 * 1024
MAX_DEPTH = 32


class YamlError(ValueError):
    pass


def _split_key(text):
    """(raw key, index after the colon) when text starts a "key: value" entry."""
    if text.startswith(("'", '"')):
        quote, index = text[0], 1
        while index < len(text):
            if text[index] == "\\" and quote == '"':
                index += 2
                continue
            if text[index] == quote:
                if quote == "'" and text[index + 1 : index + 2] == "'":
                    index += 2
                    continue
                break
            index += 1
        else:
            return None
        end = index + 1
        rest = text[end:]
        colon = len(rest) - len(rest.lstrip(" "))
        if rest[colon : colon + 1] != ":" or rest[colon + 1 : colon + 2] not in ("", " "):
            return None
        return text[:end], end + colon + 1
    if text.startswith(("- ", "[", "{", "#", "|", ">")) or text in ("-", ""):
        return None
    for index, char in enumerate(text):
        if char == "#" and index and text[index - 1] in " \t":
            return None
        if char == ":" and text[index + 1 : index + 2] in ("", " "):
            key = text[:index].rstrip()
            return (key, index + 1) if key else None
    return None


def _strip_comment(text):
    """Remove a trailing comment outside quotes."""
    quote, escaped = "", False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def _unescape(body):
    """The text of a double-quoted scalar, its escapes resolved."""
    result, index = [], 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0", " ": " "}
    while index < len(body):
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body):
                raise YamlError("Unfinished escape sequence")
            code = body[index + 1]
            if code in escapes:
                result.append(escapes[code])
                index += 2
                continue
            if code in "xuU":
                width = {"x": 2, "u": 4, "U": 8}[code]
                digits = body[index + 2 : index + 2 + width]
                if len(digits) != width or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise YamlError("Invalid escape sequence")
                result.append(chr(int(digits, 16)))
                index += 2 + width
                continue
            raise YamlError("Unsupported escape sequence")
        result.append(char)
        index += 1
    return "".join(result)


def _closing_quote(text):
    """Index of the quote that closes the quoted scalar text starts with, or None."""
    quote, index = text[0], 1
    while index < len(text):
        if text[index] == "\\" and quote == '"':
            index += 2
            continue
        if text[index] == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index
        index += 1
    return None


def _odd_backslashes(text):
    return (len(text) - len(text.rstrip("\\"))) % 2 == 1


def fold(lines, quote=""):
    """Join the lines of a flow scalar written over several lines, as YAML does: a line
    break becomes a space and each empty line a line break. In double quotes, a
    backslash at the end of a line joins the next one directly."""
    result, newlines, joined = lines[0].rstrip(" \t"), 0, False
    if quote == '"' and _odd_backslashes(result):
        result, joined = result[:-1], True
    for number, line in enumerate(lines[1:], 2):
        last = number == len(lines)
        text = line.lstrip(" \t") if last else line.strip(" \t")
        if not text and not last:
            newlines += 1
            continue
        result += "\n" * newlines if newlines else ("" if joined else " ")
        result += text
        newlines, joined = 0, False
        if quote == '"' and not last and _odd_backslashes(result):
            result, joined = result[:-1], True
    return result


def _unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'" and _closing_quote(text) == len(text) - 1:
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"' and _closing_quote(text) == len(text) - 1:
        return _unescape(text[1:-1])
    if text.startswith(("'", '"')):
        raise YamlError("Unfinished quoted value")
    if text.startswith(("&", "*", "!", "|", ">", "%", "@", "`")):
        raise YamlError("Unsupported YAML feature")
    return text


def _split_flow(text):
    """Split the inside of a flow collection on top-level commas."""
    items, depth, quote, start, escaped = [], 0, "", 0, False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise YamlError("Unbalanced flow collection")
        elif char == "," and depth == 0:
            items.append(text[start:index])
            start = index + 1
    if quote or depth:
        raise YamlError("Unbalanced flow collection")
    tail = text[start:]
    if tail.strip() or items:
        items.append(tail)
    return [item.strip() for item in items if item.strip()]


def _scalar(text, depth=0):
    if depth > MAX_DEPTH:
        raise YamlError("The metadata is nested too deeply")
    text = text.strip()
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError("Unfinished flow sequence")
        return [_scalar(item, depth + 1) for item in _split_flow(text[1:-1])]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise YamlError("Unfinished flow mapping")
        result = {}
        for item in _split_flow(text[1:-1]):
            split = _split_key(item)
            if not split:
                raise YamlError("Invalid flow mapping entry")
            key = _unquote(split[0])
            if key in result:
                raise YamlError(f"Duplicate key {key!r}")
            result[key] = _scalar(item[split[1] :], depth + 1)
        return result
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    return _unquote(text)


class _Parser:
    def __init__(self, text):
        self.lines = []
        for number, raw in enumerate(text.splitlines(), 1):
            if "\t" in raw[: len(raw) - len(raw.lstrip())]:
                raise YamlError(f"Tab indentation on line {number}")
            self.lines.append((number, raw))
        self.index = 0

    def _next_content(self):
        while self.index < len(self.lines):
            _number, raw = self.lines[self.index]
            stripped = raw.strip()
            if stripped and not stripped.startswith("#"):
                if stripped in ("---", "..."):
                    self.index += 1
                    continue
                return len(raw) - len(raw.lstrip(" ")), raw
            self.index += 1
        return None, None

    def parse(self):
        indent, _raw = self._next_content()
        if indent is None:
            return {}
        value = self._block(indent, 0)
        if self._next_content()[0] is not None:
            raise YamlError(f"Unexpected content on line {self.lines[self.index][0]}")
        return value

    def _block(self, indent, depth):
        if depth > MAX_DEPTH:
            raise YamlError("The metadata is nested too deeply")
        _current, raw = self._next_content()
        if raw.strip().startswith("- ") or raw.strip() == "-":
            return self._sequence(indent, depth)
        return self._mapping(indent, depth)

    def _value_after(self, rest, indent, depth):
        """The value of a key or item whose inline text is rest."""
        if rest.strip().startswith(("'", '"')) and _closing_quote(rest.strip()) is None:
            return self._continue_quoted(rest.strip(), indent)
        rest = _strip_comment(rest).strip()
        if rest.startswith(("|", ">")):
            return self._block_scalar(rest, indent)
        if rest:
            if rest.startswith(("[", "{")) and not rest.endswith(("]", "}")):
                rest = self._continue_flow(rest)
            elif not rest.startswith(("[", "{", "'", '"')):
                rest = self._continue_plain(rest, indent)
            return _scalar(rest, depth)
        child, raw = self._next_content()
        if child is not None and child > indent:
            return self._block(child, depth + 1)
        # A sequence may sit at the same indentation as its key.
        if child == indent and raw.strip().startswith("-"):
            return self._sequence(indent, depth + 1)
        return None

    def _continue_quoted(self, first, indent):
        """A quoted scalar that goes on over the next lines, as YAML writers produce for
        long values (PyYAML, used by older snapcraft, wraps them at 80 columns)."""
        quote, lines = first[0], [first[1:]]
        while self.index < len(self.lines):
            number, raw = self.lines[self.index]
            self.index += 1
            if raw.strip() and len(raw) - len(raw.lstrip(" ")) <= indent:
                raise YamlError(f"Unfinished quoted value before line {number}")
            closing = _closing_quote(quote + raw)
            if closing is None:
                lines.append(raw)
                continue
            if _strip_comment(raw[closing:]).strip():
                raise YamlError(f"Unexpected text after a quoted value on line {number}")
            lines.append(raw[: closing - 1])
            text = fold(lines, quote)
            return text.replace("''", "'") if quote == "'" else _unescape(text)
        raise YamlError("Unfinished quoted value")

    def _continue_plain(self, first, indent):
        """A plain scalar that goes on over the next, more indented lines."""
        lines, end = [first], self.index
        probe = self.index
        while probe < len(self.lines):
            _number, raw = self.lines[probe]
            if not raw.strip():
                probe += 1
                continue
            if len(raw) - len(raw.lstrip(" ")) <= indent or raw.strip().startswith("#"):
                break
            text = _strip_comment(raw).strip()
            if _split_key(text):
                raise YamlError(f"Unexpected indentation on line {_number}")
            # Empty lines in between count only when the value goes on after them.
            lines.extend("" for _ in range(probe - end))
            lines.append(text)
            probe += 1
            end = probe
        self.index = end
        return fold(lines) if len(lines) > 1 else first

    def _continue_flow(self, rest):
        parts = [rest]
        while self.index < len(self.lines):
            _number, raw = self.lines[self.index]
            self.index += 1
            parts.append(_strip_comment(raw).strip())
            joined = " ".join(parts)
            try:
                _scalar(joined)
            except YamlError:
                continue
            return joined
        raise YamlError("Unfinished flow collection")

    def _mapping(self, indent, depth):
        result = {}
        while True:
            current, raw = self._next_content()
            if current is None or current < indent:
                return result
            if current > indent:
                raise YamlError(f"Unexpected indentation on line {self.lines[self.index][0]}")
            text = raw[indent:]
            if text.startswith("-"):
                return result
            split = _split_key(text)
            if not split:
                raise YamlError(f"Expected a key on line {self.lines[self.index][0]}")
            key = _unquote(split[0])
            if key in result:
                raise YamlError(f"Duplicate key {key!r}")
            self.index += 1
            result[key] = self._value_after(text[split[1] :], indent, depth)

    def _sequence(self, indent, depth):
        result = []
        while True:
            current, raw = self._next_content()
            if current is None or current < indent:
                return result
            text = raw[indent:]
            if current != indent or not (text.startswith("- ") or text == "-"):
                return result
            rest = text[1:]
            stripped = rest.lstrip(" ")
            self.index += 1
            if stripped.startswith("- ") or stripped == "-":
                # "- - item" starts a nested sequence aligned after the first dash.
                item_indent = indent + 1 + (len(rest) - len(stripped))
                self.index -= 1
                number, _original = self.lines[self.index]
                self.lines[self.index] = (number, " " * item_indent + stripped)
                result.append(self._sequence(item_indent, depth + 1))
                continue
            if _split_key(stripped):
                # "- key: value" starts a mapping whose keys align after the dash.
                item_indent = indent + 1 + (len(rest) - len(stripped))
                self.index -= 1
                number, original = self.lines[self.index]
                self.lines[self.index] = (number, " " * item_indent + stripped)
                result.append(self._mapping(item_indent, depth + 1))
                continue
            result.append(self._value_after(rest, indent, depth))

    def _block_scalar(self, header, indent):
        match = re.fullmatch(r"([|>])([+-]?)([1-9]?)", header.strip())
        if not match:
            raise YamlError("Unsupported block scalar")
        style, chomp, explicit = match[1], match[2], match[3]
        lines, block_indent = [], int(explicit) + indent if explicit else None
        while self.index < len(self.lines):
            _number, raw = self.lines[self.index]
            if not raw.strip():
                lines.append("")
                self.index += 1
                continue
            current = len(raw) - len(raw.lstrip(" "))
            if block_indent is None:
                if current <= indent:
                    break
                block_indent = current
            if current < block_indent:
                break
            lines.append(raw[block_indent:])
            self.index += 1
        while lines and lines[-1] == "" and chomp != "+":
            lines.pop()
        if style == "|":
            text = "\n".join(lines)
        else:
            paragraphs, current_lines = [], []
            for line in lines:
                if line == "":
                    paragraphs.append(" ".join(current_lines))
                    current_lines = []
                else:
                    current_lines.append(line)
            paragraphs.append(" ".join(current_lines))
            text = "\n".join(paragraphs)
        return text if chomp == "-" or not text else text + "\n"


def loads(text):
    if len(text) > MAX_SIZE:
        raise YamlError("The snap metadata is too large")
    return _Parser(text).parse()

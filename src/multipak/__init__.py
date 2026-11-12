"""MultiPak package version and its display label."""

import re

__version__ = "1.0.1"
DISPLAY_VERSION = re.sub(
    r"(a|b|rc)([0-9]+)$",
    lambda match: " " + {"a": "Alpha", "b": "Beta", "rc": "RC"}[match[1]] + " " + match[2],
    __version__,
)

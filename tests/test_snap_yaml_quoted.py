"""Quoted scalars that close at the block's own indentation (seen in a Linux Mint VM)."""

import pytest

from multipak.snap_yaml import YamlError, loads

# meta/snap.yaml of gtk2-common-themes, a runtime of the leafpad snap.
GTK2_COMMON_THEMES = """name: gtk2-common-themes
version: '0.1'
summary: All the (common) GTK 2 engines
description: 'A snap that exports various GTK 2.x theme engines

'
architectures:
- amd64
confinement: strict
"""


def test_closing_quote_in_column_zero_is_accepted():
    data = loads(GTK2_COMMON_THEMES)
    assert data["description"] == "A snap that exports various GTK 2.x theme engines\n"
    assert data["architectures"] == ["amd64"]
    assert data["confinement"] == "strict"


def test_nested_value_may_continue_at_the_mapping_indentation():
    data = loads("apps:\n  app:\n    command: 'run\n    me'\n")
    assert data["apps"]["app"]["command"] == "run me"


def test_line_less_indented_than_the_block_still_fails():
    with pytest.raises(YamlError):
        loads("apps:\n  app:\n    command: 'run\n  me'\n")

"""Tests for the small-model JSON repair helper."""

import json

from agentforge.utils.json_repair import repair_json


def test_repair_json_removes_trailing_comma_in_object() -> None:
    """A trailing comma before a closing brace is stripped."""
    broken = '{"name": "write_file", "arguments": {"path": "a.txt",},}'
    assert json.loads(repair_json(broken)) == {
        "name": "write_file",
        "arguments": {"path": "a.txt"},
    }


def test_repair_json_removes_trailing_comma_in_array() -> None:
    """A trailing comma before a closing bracket is stripped."""
    broken = '{"items": [1, 2, 3,]}'
    assert json.loads(repair_json(broken)) == {"items": [1, 2, 3]}


def test_repair_json_closes_truncated_object() -> None:
    """Unterminated nested braces are auto-closed."""
    truncated = '{"name": "write_file", "arguments": {"path": "a.txt"'
    assert json.loads(repair_json(truncated)) == {
        "name": "write_file",
        "arguments": {"path": "a.txt"},
    }


def test_repair_json_ignores_braces_inside_strings() -> None:
    """Brace-like characters inside string values do not affect bracket balancing."""
    text = '{"note": "use { and } carefully"}'
    assert json.loads(repair_json(text)) == {"note": "use { and } carefully"}


def test_repair_json_leaves_valid_json_unchanged_in_effect() -> None:
    """Already-valid JSON still parses the same after repair."""
    valid = '{"a": 1, "b": [1, 2]}'
    assert json.loads(repair_json(valid)) == json.loads(valid)

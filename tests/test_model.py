"""Model-level unit tests: type normalization and Field equality."""

import pytest

from schemascope.model import Field, normalize_type


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("int", "integer"),
        ("  INTEGER ", "integer"),
        ("varchar", "string"),
        ("TEXT", "string"),
        ("double", "float"),
        ("timestamp", "datetime"),
        ("bool", "boolean"),
        ("mystery", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalize_type_aliases(raw, expected):
    assert normalize_type(raw) == expected


@pytest.mark.parametrize("raw", [123, True, ["a"], {"k": "v"}, 3.14])
def test_normalize_type_non_string_is_unknown_not_crash(raw):
    """BUG 1: a non-string ``type`` normalizes to ``"unknown"`` instead of
    raising ``AttributeError`` on ``raw.strip()``."""
    assert normalize_type(raw) == "unknown"


def test_field_equality_ignores_raw_type():
    a = Field(name="id", type="integer", raw_type="int")
    b = Field(name="id", type="integer", raw_type="INTEGER")
    assert a == b


def test_field_equality_respects_canonical_type():
    a = Field(name="id", type="integer")
    b = Field(name="id", type="string")
    assert a != b

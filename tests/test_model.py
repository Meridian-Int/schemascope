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


@pytest.mark.parametrize(
    "raw, expected",
    [
        # parameterized types: the (size[,scale]) / (max) is stripped
        ("varchar(255)", "string"),
        ("VARCHAR(255)", "string"),
        ("numeric(10,2)", "float"),
        ("numeric(10, 2)", "float"),
        ("char (1)", "string"),
        ("nvarchar(max)", "string"),
        ("decimal(18,4)", "float"),
        # multi-word vendor types (whitespace collapsed)
        ("double precision", "float"),
        ("character varying", "string"),
        ("timestamp with time zone", "datetime"),
        ("timestamp without time zone", "datetime"),
        # dialect integer/float/string/bool/datetime spellings
        ("int4", "integer"), ("int8", "integer"), ("serial", "integer"),
        ("bigserial", "integer"), ("tinyint", "integer"),
        ("money", "float"), ("smallmoney", "float"), ("float8", "float"),
        ("clob", "string"), ("jsonb", "string"), ("json", "string"),
        ("uniqueidentifier", "string"), ("citext", "string"),
        ("bit", "boolean"),
        ("datetime2", "datetime"), ("smalldatetime", "datetime"),
        ("timestamptz", "datetime"),
        # exotic types export as serialized/hex/base64 text -> infer string, so map to string
        ("bytea", "string"), ("blob", "string"), ("varbinary(50)", "string"),
        ("geometry", "string"), ("array", "string"), ("json", "string"),
        # array *notation* (int[]) and truly unrecognized spellings stay unknown
        ("integer[]", "unknown"),
    ],
)
def test_normalize_type_vendor_alignment(raw, expected):
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

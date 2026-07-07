"""Type-inference predicates, ordering, and declared/inferred compatibility."""

import pytest

from schemascope.typeinfer import (
    infer_type,
    is_bool,
    is_integer,
    type_compatible,
)


@pytest.mark.parametrize(
    "values, expected",
    [
        (["1", "2", "-3"], "integer"),
        (["1.5", "2.0"], "float"),
        (["true", "false"], "boolean"),
        (["2021-01-02", "2020-12-31"], "date"),
        (["2021-01-02 10:30:00", "2020-12-31T00:00:00"], "datetime"),
        (["alice", "bob"], "string"),
        ([], "unknown"),
    ],
)
def test_infer_type_basic(values, expected):
    assert infer_type(values) == expected


def test_bug6_zero_one_integer_column_is_compatible():
    """BUG 6: an all-0/1 column reads as ``boolean`` (most specific), but that is
    valid integer data — it must NOT be flagged against a declared ``integer``."""
    inferred = infer_type(["0", "1", "1", "0"])
    assert inferred == "boolean"
    assert type_compatible("integer", inferred) is True


def test_bug6_boolean_column_with_real_integers_still_flags():
    """The compatibility widening is one-directional: a declared ``boolean`` whose
    data infers ``integer`` (values outside 0/1) is still a genuine mismatch."""
    inferred = infer_type(["3", "7", "12"])
    assert inferred == "integer"
    assert type_compatible("boolean", inferred) is False


@pytest.mark.parametrize("value", ["²", "³", "⁵"])
def test_bug7_unicode_superscripts_are_not_integers(value):
    """BUG 7: ``str.isdigit()`` is True for superscripts but ``int()`` rejects
    them; ``is_integer`` must restrict to ASCII digits."""
    assert is_integer(value) is False
    # And ``int()`` really would have crashed, confirming the hazard.
    with pytest.raises(ValueError):
        int(value)


def test_bug7_superscript_column_infers_string():
    assert infer_type(["²", "³"]) == "string"


def test_is_integer_rejects_bool():
    assert is_integer(True) is False
    assert is_bool(True) is True


def test_type_compatible_rules():
    assert type_compatible("string", "integer") is True
    assert type_compatible("float", "integer") is True
    assert type_compatible("integer", "string") is False
    assert type_compatible("unknown", "integer") is True
    assert type_compatible("date", "date") is True


def test_late_drift_is_detected_over_all_values():
    """Inference scans EVERY value, not an early sample: a column that reads as one
    type for thousands of rows then drifts must widen to the broader type."""
    assert infer_type(["1"] * 5000) == "boolean"          # clean: all 0/1-ish
    assert infer_type(["1"] * 5000 + ["not_an_int"]) == "string"   # late drift caught


def test_type_inferer_incremental():
    """The incremental accumulator tracks the same result as a full re-scan."""
    from schemascope.typeinfer import TypeInferer

    inf = TypeInferer()
    assert inf.result() == "unknown"          # nothing seen yet
    for v in ["1", "2", "3"]:
        inf.add(v)
    assert inf.result() == "integer"
    inf.add("3.5")                            # drift integer -> float
    assert inf.result() == "float"
    inf.add("nope")                           # drift float -> string
    assert inf.result() == "string"

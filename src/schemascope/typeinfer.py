"""Type inference over observed data values.

Values reach us either as strings (CSV) or native Python (SQLite). Every
predicate accepts ``Any`` and stringifies when it needs to, so inference is
uniform across sources. :func:`infer_type` picks the *most specific* canonical
type that **all** observed values satisfy — strict on purpose, so any mixed
column degrades to ``"string"`` rather than guessing.
"""

from __future__ import annotations

from typing import Any, Iterable

# Case-insensitive tokens accepted as booleans.
_BOOL_TOKENS = {"true", "false", "1", "0", "yes", "no", "t", "f", "y", "n"}


def is_bool(value: Any) -> bool:
    """True if ``value`` reads as a boolean token (or is a real ``bool``)."""
    if isinstance(value, bool):
        return True
    return str(value).strip().lower() in _BOOL_TOKENS


def is_integer(value: Any) -> bool:
    """True if ``value`` is an integer (real ``int``, or a string like ``-12``).

    Real ``bool`` is rejected here so booleans are classified as boolean, not int.
    Floats such as ``"1.5"`` or ``3.0`` are rejected — only whole, dot-free ints.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    s = str(value).strip()
    if not s:
        return False
    if s[0] in "+-":
        s = s[1:]
    # ``str.isdigit`` is True for non-ASCII digits (superscripts like "²",
    # fullwidth numerals, ...) that ``int()`` then rejects. Restrict to ASCII so
    # what we call an integer is always ``int()``-convertible.
    return bool(s) and s.isascii() and s.isdigit()


def is_float(value: Any) -> bool:
    """True if ``value`` parses as a float (``int`` values count as floats too).

    Rejects ``bool`` and non-finite spellings (``nan``/``inf``) so a stray "nan"
    string doesn't silently turn a text column numeric.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    s = str(value).strip()
    if not s:
        return False
    if s.lower().lstrip("+-") in {"nan", "inf", "infinity"}:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def is_date(value: Any) -> bool:
    """True for a strict ``YYYY-MM-DD`` calendar date (and nothing else)."""
    s = str(value).strip()
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    y, m, d = s[:4], s[5:7], s[8:10]
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return False
    from datetime import date as _date

    try:
        _date(int(y), int(m), int(d))
        return True
    except ValueError:
        return False


def is_datetime(value: Any) -> bool:
    """True for ``YYYY-MM-DD`` followed by a time, e.g. ``2001-05-01 10:30:00``.

    Accepts either a space or ``T`` between the date and time (ISO-ish). The date
    part must itself be a valid calendar date; the time part is checked loosely.
    """
    s = str(value).strip()
    if len(s) < 12 or s[10] not in (" ", "T"):
        return False
    if not is_date(s[:10]):
        return False
    time_part = s[11:]
    # Allow a trailing "Z" and fractional seconds; validate H:M(:S) numerically.
    time_part = time_part.rstrip("Z")
    bits = time_part.split(":")
    if len(bits) < 2 or len(bits) > 3:
        return False
    try:
        hh = int(bits[0])
        mm = int(bits[1])
        ss = float(bits[2]) if len(bits) == 3 else 0.0
    except ValueError:
        return False
    return 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60


# Ordered most-specific-first. A type wins only if every value matches.
_INFER_ORDER = (
    ("boolean", is_bool),
    ("integer", is_integer),
    ("float", is_float),
    ("date", is_date),
    ("datetime", is_datetime),
)


class TypeInferer:
    """Infer a column's type incrementally over **all** its non-null values.

    A candidate type survives only while every value seen so far satisfies it, so
    a column that drifts to a violating value after any number of rows is caught —
    unlike sampling the first N values, which silently misses late drift in a large
    file. One pass, O(1) memory, and it short-circuits to ``string`` the moment no
    candidate survives. Candidates stay most-specific-first, so the winner is what
    a full re-scan would pick.
    """

    __slots__ = ("_candidates", "_seen")

    def __init__(self) -> None:
        self._candidates = list(_INFER_ORDER)   # (name, predicate), most-specific first
        self._seen = False

    def add(self, value: Any) -> None:
        """Fold one non-null value into the running inference."""
        self._seen = True
        if self._candidates:
            self._candidates = [(n, p) for n, p in self._candidates if p(value)]

    def result(self) -> str:
        """Inferred canonical type: the most-specific surviving candidate, else
        ``"string"`` if values were seen but none matched, else ``"unknown"``."""
        if not self._seen:
            return "unknown"
        return self._candidates[0][0] if self._candidates else "string"


def infer_type(values: Iterable[Any]) -> str:
    """Infer a canonical type from a set of **non-null** values.

    Scans *every* value: the most specific type that all values satisfy wins
    (boolean -> integer -> float -> date -> datetime); if none do, the column is
    ``"string"``; an empty input infers ``"unknown"``.
    """
    inf = TypeInferer()
    for v in values:
        inf.add(v)
    return inf.result()


def type_compatible(declared: str, inferred: str) -> bool:
    """Is data ``inferred`` type consistent with the ``declared`` schema type?

    Lenient by design — we flag genuine disagreement, not harmless variation:

    * equal types are compatible;
    * a ``string`` column accepts anything (strings hold any representation);
    * a ``float`` column accepts ``integer`` data (numeric widening);
    * an ``integer`` column accepts ``boolean``-inferred data: an all-0/1 flag
      column reads as ``boolean`` (the most specific type) but 0 and 1 are
      perfectly valid integers, so this must not be flagged as a mismatch;
    * ``unknown`` on either side is treated as compatible (nothing to disagree).

    Everything else is incompatible — e.g. declared ``integer`` but the data
    infers ``string`` yields ``False``, the "your data disagrees with your
    schema" signal. (Note the asymmetry: a ``boolean`` column whose data infers
    ``integer`` — i.e. values outside 0/1 — is still flagged.)
    """
    if declared == inferred:
        return True
    if declared == "unknown" or inferred == "unknown":
        return True
    if declared == "string":
        return True
    if declared == "float" and inferred == "integer":
        return True
    if declared == "integer" and inferred == "boolean":
        return True
    return False

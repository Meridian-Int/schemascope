"""Cross-engine known-answer for the hardened dialect SQL.

The scope aggregates are computed in dialect-specific SQL (year extraction, the
case-folded patient merge, numeric/blank casts). Bugs there *silently miscount*.
This test runs the SAME synthetic clinical corpus through the profiler on two
genuinely different engines — SQLite and DuckDB (columnar, strict casts, its own
regexp/collation) — and asserts every scope metric matches. If a DuckDB dialect
branch is wrong, a number moves and this fails.

Skipped when duckdb-engine isn't installed.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine

pytest.importorskip("duckdb_engine")

import synth  # noqa: E402  (tests dir on sys.path via pytest)
from schemascope.io import Db  # noqa: E402
from schemascope.profile import build_profile  # noqa: E402

# The dataset-scope sections that come from SQL aggregates — the parts a broken
# dialect branch would corrupt. (The one worked patient is excluded: tie-broken
# record selection may legitimately differ across engines.)
_SCOPE_KEYS = (
    "scale", "stream_inventory", "record_depth", "longitudinal", "geography",
    "demographics_scope", "diagnoses_scope", "laboratory_scope", "vitals_scope",
    "examination_scope", "medications_scope", "specialties_scope",
)


def _profile(url: str) -> dict:
    eng = create_engine(url)
    mapping = synth.build(eng)          # deterministic synthetic tables + rows
    prof = build_profile(Db(eng), mapping)
    eng.dispose()
    scope = {k: prof.get(k) for k in _SCOPE_KEYS}
    # Drop the token-count fields from `scale`: those come from the Python token
    # pass reading raw driver values, so they legitimately differ across engines by
    # value *representation* (DuckDB's REAL is float32 → `13.2` renders as
    # `13.199999…`, more tokens) — a data nuance, not dialect SQL. The SQL count
    # fields (patients/encounters/rows) stay in and must match.
    scope["scale"] = {k: v for k, v in (scope.get("scale") or {}).items()
                      if "token" not in k and k != "clinical_content_pct"}
    return scope


def _norm(o):
    """Order-insensitive: GROUP BY result order isn't guaranteed across engines,
    so sort lists recursively — value differences still surface."""
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items()}
    if isinstance(o, list):
        return sorted((_norm(x) for x in o), key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return o


def test_duckdb_scope_matches_sqlite(tmp_path):
    sqlite_scope = _profile(f"sqlite:///{tmp_path/'s.db'}")
    duckdb_scope = _profile(f"duckdb:///{tmp_path/'d.duckdb'}")
    assert _norm(duckdb_scope) == _norm(sqlite_scope)

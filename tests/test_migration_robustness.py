"""Robustness suite: schemascope must NEVER FAIL across many database structures
and schema migrations.

The point of schemascope is to be pointed at *someone's* database — any shape,
any dialect — and profile it against a declared schema without ever crashing,
flagging drift instead. This suite proves that: a deterministic generator (in the
spirit of corpusscope's ``tests/synth.py``, but generalized) fabricates hundreds
of schemas and, for each, a SQLite database that has been **migrated away** from
that schema:

* tables dropped, renamed, or case-shifted;
* columns dropped, renamed, retyped to an incompatible type, or nulled out;
* extra tables and columns the schema never mentions;
* reserved-word and whitespace identifiers that force dialect quoting;
* canonical types spelled with real vendor aliases (``bigint``, ``varchar(255)``,
  ``timestamptz``, ``jsonb``, ``numeric(10,2)``, …).

Every scenario is seeded, so its expected outcome is known exactly. For each one
we assert schemascope (a) never raises, (b) returns a structurally valid report,
and (c) reports presence/type drift correctly.

Runs against SQLite through the *SQLAlchemy* connector — the same engine /
inspector / streaming path that drives PostgreSQL, MySQL, SQL Server and Oracle —
so passing here is what runs against a real server.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pytest

from schemascope.connector import SqlConnector
from schemascope.model import CANONICAL_TYPES, Entity, Field, Schema, normalize_type
from schemascope.profile import profile

# How many migrated-schema scenarios to generate (each is a separate test case).
NUM_SCENARIOS = 300

# canonical kind -> real vendor/dialect spellings that all normalize to that kind.
# Exercises the type-alias table end to end (each must resolve to its kind).
_DECLARED: Dict[str, List[str]] = {
    "integer": ["integer", "int", "bigint", "int4", "serial", "smallint"],
    "float": ["float", "double precision", "numeric(10,2)", "real", "decimal"],
    "string": ["string", "varchar(255)", "text", "jsonb", "uuid", "char(3)"],
    "boolean": ["boolean", "bool", "bit"],
    "date": ["date"],
    "datetime": ["datetime", "timestamp", "timestamptz"],
}
_KINDS = list(_DECLARED)

_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "x@y.com", "note-text", "zeta"]
# reserved words / awkward names that only survive if identifiers are quoted.
_TRICKY = ["select", "order", "group", "from", "table", "weird name", "Mixed Case"]
_FIELD_BASES = ["id", "user", "email", "age", "amount", "status", "created",
                "name", "code", "score", "flag", "dt", "qty", "label"]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vals(kind: str, rnd: random.Random, n: int) -> Tuple[str, List[Any]]:
    """Return (sqlite column type, n values) whose *inferred* type is ``kind``."""
    if kind == "integer":
        # magnitude > 1 and mixed sign, so it never infers as boolean (all 0/1).
        return "INTEGER", [rnd.randint(2, 9999) * rnd.choice((1, -1)) for _ in range(n)]
    if kind == "float":
        return "REAL", [round(rnd.uniform(1.5, 9999.5), 2) for _ in range(n)]
    if kind == "boolean":
        return "TEXT", [rnd.choice(("true", "false")) for _ in range(n)]
    if kind == "date":
        return "TEXT", [
            f"20{rnd.randint(10, 23):02d}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            for _ in range(n)
        ]
    if kind == "datetime":
        return "TEXT", [
            f"20{rnd.randint(10, 23):02d}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d} "
            f"{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}"
            for _ in range(n)
        ]
    # string: always contains a letter, so it never infers numeric/bool/date.
    return "TEXT", [rnd.choice(_WORDS) + str(rnd.randint(0, 999)) for _ in range(n)]


def _field_name(rnd: random.Random, used: set) -> str:
    base = rnd.choice(_TRICKY) if rnd.random() < 0.12 else rnd.choice(_FIELD_BASES)
    name, i = base, 1
    while name.lower() in used:
        i += 1
        name = f"{base}{i}"
    used.add(name.lower())
    return name


@dataclass
class Scenario:
    schema: Schema
    url: str
    expect: Dict[str, Dict[str, Any]]  # entity name -> expectations


def make_scenario(seed: int, tmp_path) -> Scenario:
    """Fabricate one (schema, migrated database, expectations) triple for ``seed``."""
    rnd = random.Random(seed)
    db_path = tmp_path / "scn.db"
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    used_tables: set = set()

    def _create(table: str, columns: List[Tuple[str, str, List[Any]]], n_rows: int) -> None:
        coldefs = ", ".join(f"{_q(c)} {t}" for c, t, _ in columns)
        cur.execute(f"CREATE TABLE {_q(table)} ({coldefs})")
        rows = list(zip(*[vals for _, _, vals in columns])) if columns else []
        if rows:
            ph = ",".join(["?"] * len(columns))
            cur.executemany(f"INSERT INTO {_q(table)} VALUES ({ph})", rows)

    entities: List[Entity] = []
    expect: Dict[str, Dict[str, Any]] = {}

    for ei in range(rnd.randint(1, 4)):
        ename = f"ent{ei}"
        used_fields: set = set()
        specs = []
        fields: List[Field] = []
        for fi in range(rnd.randint(2, 7)):
            fname = _field_name(rnd, used_fields)
            kind = "integer" if fi == 0 else rnd.choice(_KINDS)
            pk = fi == 0
            declared = rnd.choice(_DECLARED[kind])
            # Build the Field the way the real schema loaders do: canonical `type`,
            # original spelling kept on `raw_type`. (Constructing with the raw
            # spelling would leave `type` un-normalized and mis-compute type_ok.)
            fields.append(Field(name=fname, type=normalize_type(declared),
                                raw_type=declared, primary_key=pk, nullable=not pk))
            specs.append((fname, kind, pk))

        # sometimes bind the entity to a differently-named / reserved table, kept
        # unique (case-insensitively) so every entity reads its OWN table.
        base_table = ename
        if rnd.random() < 0.30:
            cand = rnd.choice(_TRICKY) if rnd.random() < 0.5 else ename + "_tbl"
            if cand.lower() not in used_tables:
                base_table = cand
        used_tables.add(base_table.lower())  # reserve even if the table is dropped
        source = None if base_table == ename else base_table
        entities.append(Entity(name=ename, fields=fields, source=source))

        n_rows = rnd.randint(3, 9)
        fexpect: Dict[str, Dict[str, Any]] = {}

        # entity migration: the whole table may have been dropped.
        if rnd.random() < 0.15:
            for fname, _, _ in specs:
                fexpect[fname] = {"present": False}
            expect[ename] = {"present": False, "row_count": 0, "fields": fexpect}
            continue

        columns: List[Tuple[str, str, List[Any]]] = []
        for fname, kind, pk in specs:
            fate = "keep" if pk else rnd.choices(
                ("keep", "drop", "rename", "retype", "null"),
                weights=(50, 12, 12, 14, 12),
            )[0]
            # a declared string accepts any inferred type, so it can't be forced
            # into a type mismatch — keep it instead.
            if fate == "retype" and kind == "string":
                fate = "keep"

            if fate == "drop":
                fexpect[fname] = {"present": False}
                continue
            if fate == "rename":
                st, vals = _vals(kind, rnd, n_rows)
                columns.append((fname + "_renamed", st, vals))
                fexpect[fname] = {"present": False}
                continue

            col_name = fname
            if fate == "keep" and rnd.random() < 0.30:
                col_name = fname.swapcase()  # still resolves case-insensitively

            if fate == "retype":
                st, vals = "TEXT", [rnd.choice(_WORDS) for _ in range(n_rows)]  # -> string
                fexpect[fname] = {"present": True, "type_ok": False}
            elif fate == "null":
                st, _ = _vals(kind, rnd, n_rows)
                vals = [None] * n_rows
                fexpect[fname] = {"present": True, "null_count": n_rows,
                                  "inferred_type": "unknown", "type_ok": True}
            else:  # keep
                st, vals = _vals(kind, rnd, n_rows)
                fexpect[fname] = {"present": True, "type_ok": True}
            columns.append((col_name, st, vals))

        # extra columns the schema never mentions — must be ignored, not crash.
        for _ in range(rnd.randint(0, 2)):
            st, vals = _vals(rnd.choice(_KINDS), rnd, n_rows)
            columns.append((f"extra_{rnd.randint(1000, 9999)}", st, vals))

        # the real table may be case-shifted; case-insensitive resolution still
        # binds it to `source` (its lower-cased name is already reserved).
        actual_table = base_table.swapcase() if rnd.random() < 0.3 else base_table
        _create(actual_table, columns, n_rows)
        expect[ename] = {"present": True, "row_count": n_rows, "fields": fexpect}

    # a stray table nobody declared — the profiler must not trip over it.
    if rnd.random() < 0.4:
        jname = f"junk_{rnd.randint(1000, 9999)}"
        if jname.lower() not in used_tables:
            used_tables.add(jname.lower())
            st, vals = _vals("string", rnd, 3)
            _create(jname, [("x", st, vals)], 3)

    con.commit()
    con.close()
    return Scenario(Schema(entities=entities), f"sqlite:///{db_path}", expect)


def _check(scn: Scenario, report) -> None:
    schema = scn.schema
    assert [e.name for e in report.entities] == [e.name for e in schema.entities]

    for ent, er in zip(schema.entities, report.entities):
        ee = scn.expect[ent.name]
        assert er.name == ent.name
        assert isinstance(er.present, bool)
        assert er.present == ee["present"]
        assert er.row_count == ee["row_count"]
        assert len(er.fields) == len(ent.fields)

        for f, fr in zip(ent.fields, er.fields):
            fe = ee["fields"][f.name]
            assert fr.name == f.name
            # --- universal structural invariants (hold for EVERY field) ---
            assert fr.inferred_type in CANONICAL_TYPES
            assert isinstance(fr.type_ok, bool)
            assert fr.null_count >= 0
            assert fr.distinct_count >= 0
            assert fr.null_count <= fr.row_count
            assert fr.present == fe["present"]

            if not fr.present:
                assert fr.column is None
                assert fr.row_count == 0
                continue

            assert fr.row_count == ee["row_count"]
            if "type_ok" in fe:
                assert fr.type_ok == fe["type_ok"]
            if "null_count" in fe:
                assert fr.null_count == fe["null_count"]
            if "inferred_type" in fe:
                assert fr.inferred_type == fe["inferred_type"]


@pytest.mark.parametrize("seed", range(NUM_SCENARIOS))
def test_never_fails_across_migrated_schemas(seed, tmp_path):
    scn = make_scenario(seed, tmp_path)
    conn = SqlConnector(scn.url)
    try:
        report = profile(scn.schema, conn)  # must never raise
    finally:
        conn.close()
    _check(scn, report)


def test_generator_actually_exercises_every_drift(tmp_path):
    """Meta-check: across the scenario set we really do hit present/absent tables,
    absent columns, type mismatches, and all-null columns — otherwise the suite
    could be green while testing nothing interesting."""
    saw = {"entity_absent": False, "field_absent": False,
           "type_mismatch": False, "all_null": False, "entity_present": False}
    for seed in range(NUM_SCENARIOS):
        d = tmp_path / f"s{seed}"
        d.mkdir()
        scn = make_scenario(seed, d)
        for ee in scn.expect.values():
            saw["entity_present"] |= ee["present"]
            saw["entity_absent"] |= not ee["present"]
            for fe in ee["fields"].values():
                saw["field_absent"] |= not fe["present"]
                saw["type_mismatch"] |= fe.get("type_ok") is False
                saw["all_null"] |= "null_count" in fe
    assert all(saw.values()), saw


def test_empty_table_profiles_cleanly(tmp_path):
    """A migrated-but-emptied table: columns exist, zero rows -> everything
    'unknown'/compatible, no division-by-zero, no crash."""
    db = tmp_path / "empty.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER, email TEXT, age INTEGER)")
    con.commit()
    con.close()

    schema = Schema(entities=[Entity(name="users", fields=[
        Field(name="id", type="integer", primary_key=True, nullable=False),
        Field(name="email", type="varchar(255)"),
        Field(name="age", type="bigint", nullable=True),
    ])])
    conn = SqlConnector(f"sqlite:///{db}")
    try:
        report = profile(schema, conn)
    finally:
        conn.close()

    (users,) = report.entities
    assert users.present is True
    assert users.row_count == 0
    for fr in users.fields:
        assert fr.present is True
        assert fr.row_count == 0
        assert fr.null_count == 0
        assert fr.null_fraction == 0.0
        assert fr.inferred_type == "unknown"
        assert fr.type_ok is True

"""Cross-engine genericness: SqlConnector must work against ANY SQLAlchemy
dialect, not just SQLite.

schemascope's whole point is to be generic — point it at *any* database engine.
The connector only ever uses dialect-agnostic SQLAlchemy surface (``create_engine``,
``inspect().get_table_names`` / ``get_columns``, the dialect's own identifier
preparer, and a streamed ``SELECT *``), so what works on one engine works on all.

We prove it here on two genuinely different in-process engines:

* **SQLite** — a row store, dynamic typing;
* **DuckDB** — a column store with a static, different type system and its own
  reflection (skipped if ``duckdb-engine`` is not installed).

The same schema profiled against both must give the same answers. Server engines
(PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, BigQuery, Redshift, …) expose
the identical SQLAlchemy surface, so passing here is what runs against them.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, text

from schemascope.connector import SqlConnector
from schemascope.model import Entity, Field, Schema, normalize_type
from schemascope.profile import profile


def _f(name, decl, pk=False, nullable=True):
    return Field(name=name, type=normalize_type(decl), raw_type=decl,
                 primary_key=pk, nullable=nullable)


def _schema() -> Schema:
    return Schema(entities=[
        Entity(name="users", fields=[
            _f("id", "bigint", pk=True, nullable=False),
            _f("email", "varchar(255)"),
            _f("age", "integer", nullable=True),
            _f("active", "boolean"),
            _f("signup", "date"),
        ]),
        # a table the schema declares but the database does NOT have.
        Entity(name="orders", fields=[_f("order_id", "integer", pk=True, nullable=False)]),
    ])


def _build_sqlite(tmp_path) -> str:
    db = tmp_path / "g.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER, email TEXT, age INTEGER, active TEXT, signup TEXT)")
    con.executemany("INSERT INTO users VALUES (?,?,?,?,?)", [
        (1, "a@x.com", 31, "true", "2021-03-05"),
        (2, "b@x.com", None, "false", "2021-07-19"),
        (3, "c@x.com", 27, "true", "2022-01-02"),
    ])
    con.commit()
    con.close()
    return f"sqlite:///{db}"


def _build_duckdb(tmp_path) -> str:
    pytest.importorskip("duckdb_engine")
    db = tmp_path / "g.duckdb"
    eng = create_engine(f"duckdb:///{db}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE users "
                       "(id BIGINT, email VARCHAR, age INTEGER, active BOOLEAN, signup DATE)"))
        c.execute(text(
            "INSERT INTO users VALUES "
            "(1,'a@x.com',31,true,DATE '2021-03-05'),"
            "(2,'b@x.com',NULL,false,DATE '2021-07-19'),"
            "(3,'c@x.com',27,true,DATE '2022-01-02')"))
    eng.dispose()
    return f"duckdb:///{db}"


_BUILDERS = {"sqlite": _build_sqlite, "duckdb": _build_duckdb}


@pytest.mark.parametrize("engine", list(_BUILDERS))
def test_connector_is_engine_generic(engine, tmp_path):
    url = _BUILDERS[engine](tmp_path)
    conn = SqlConnector(url)
    try:
        report = profile(_schema(), conn)  # identical call for every engine
    finally:
        conn.close()

    ents = {e.name: e for e in report.entities}

    users = ents["users"]
    assert users.present is True
    assert users.row_count == 3
    f = {x.name: x for x in users.fields}
    assert f["id"].present and f["id"].type_ok            # bigint -> integer
    assert f["email"].present and f["email"].inferred_type == "string" and f["email"].type_ok
    assert f["age"].present and f["age"].null_count == 1 and f["age"].type_ok
    assert f["active"].present and f["active"].type_ok    # real/text boolean -> boolean
    assert f["signup"].present and f["signup"].type_ok    # date value/string -> date

    # the declared-but-absent table is drift, not a crash — on every engine.
    assert ents["orders"].present is False
    assert all(fp.present is False for fp in ents["orders"].fields)

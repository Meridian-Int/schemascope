"""SqlConnector tests — profiling a live database via a SQLAlchemy URL.

These use SQLite (bundled with Python, no server needed) through the *SQLAlchemy*
path, exercising the same engine/inspector/streaming code that drives Postgres,
MySQL, SQL Server, Oracle, etc. The connector is written to be dialect-agnostic,
so what passes here is what runs against a real server.
"""

import sqlite3

import pytest

from schemascope.connector import SqlConnector, open_connector
from schemascope.model import Entity, Field, Schema
from schemascope.profile import profile


def _sqlite_url(tmp_path):
    """Build a small database with sqlite3 and return its SQLAlchemy URL."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE users (id INTEGER, email TEXT, age INTEGER)")
    conn.executemany(
        "INSERT INTO users VALUES (?, ?, ?)",
        [(1, "a@example.com", 31), (2, "b@example.com", None), (3, "c@example.com", 27)],
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{db}"  # absolute path -> four-slash sqlite URL


def test_open_connector_picks_sql_for_url(tmp_path):
    url = _sqlite_url(tmp_path)
    c = open_connector(url)
    try:
        assert isinstance(c, SqlConnector)
    finally:
        c.close()


def test_sql_connector_reads_rows(tmp_path):
    c = SqlConnector(_sqlite_url(tmp_path))
    try:
        assert c.has_entity("users") is True
        assert c.has_entity("missing") is False
        assert c.columns("users") == ["id", "email", "age"]
        assert list(c.rows("users")) == [
            {"id": 1, "email": "a@example.com", "age": 31},
            {"id": 2, "email": "b@example.com", "age": None},
            {"id": 3, "email": "c@example.com", "age": 27},
        ]
    finally:
        c.close()


def test_sql_connector_missing_table_is_absent_not_error(tmp_path):
    """'Never fails': a table the schema names but the DB lacks is reported absent
    (has_entity False, no columns, no rows) rather than raising."""
    c = SqlConnector(_sqlite_url(tmp_path))
    try:
        assert c.has_entity("orders") is False
        assert c.columns("orders") == []
        assert list(c.rows("orders")) == []
    finally:
        c.close()


def test_sql_connector_matches_table_case_insensitively(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE Customers (id INTEGER)")
    conn.execute("INSERT INTO Customers VALUES (1)")
    conn.commit()
    conn.close()

    c = SqlConnector(f"sqlite:///{db}")
    try:
        # schema says "customers"; the real table is "Customers" — resolve it.
        assert c.has_entity("customers") is True
        assert c.columns("customers") == ["id"]
        assert list(c.rows("customers")) == [{"id": 1}]
    finally:
        c.close()


def test_sql_connector_quotes_reserved_identifier(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE "select" (id INTEGER)')
    conn.execute('INSERT INTO "select" VALUES (1)')
    conn.commit()
    conn.close()

    c = SqlConnector(f"sqlite:///{db}")
    try:
        assert c.has_entity("select") is True
        assert list(c.rows("select")) == [{"id": 1}]
    finally:
        c.close()


def test_profile_end_to_end_over_database(tmp_path):
    """The real point: profile a schema against a live database URL."""
    schema = Schema(
        entities=[
            Entity(
                name="users",
                fields=[
                    Field(name="id", type="integer", primary_key=True, nullable=False),
                    Field(name="email", type="string"),
                    Field(name="age", type="integer", nullable=True),
                ],
            )
        ]
    )
    c = SqlConnector(_sqlite_url(tmp_path))
    try:
        report = profile(schema, c).to_dict()
    finally:
        c.close()

    (users,) = report["entities"]
    assert users["present"] is True
    assert users["row_count"] == 3
    fields = {f["name"]: f for f in users["fields"]}
    assert fields["id"]["inferred_type"] == "integer"
    assert fields["id"]["type_ok"] is True
    assert fields["email"]["inferred_type"] == "string"
    assert fields["age"]["null_count"] == 1
    assert fields["age"]["inferred_type"] == "integer"
    assert fields["age"]["type_ok"] is True

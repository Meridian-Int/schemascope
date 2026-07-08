"""Connector tests: SQLite open validation + open_connector routing."""

import sqlite3

import pytest

from schemascope.connector import SqliteConnector, open_connector
from schemascope.model import ConnectorError


def test_non_database_file_raises_connector_error(tmp_path):
    """Opening a non-database ``.db`` file must raise ``ConnectorError``, not leak
    ``sqlite3.DatabaseError`` on the first query."""
    bad = tmp_path / "bad.db"
    bad.write_text("not a database\n", encoding="utf-8")
    with pytest.raises(ConnectorError):
        open_connector(bad)


def test_sqlite_connector_reads_rows(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "alice"), (2, None)])
    conn.commit()
    conn.close()

    c = SqliteConnector(db)
    try:
        assert c.has_entity("users") is True
        assert c.has_entity("missing") is False
        assert c.columns("users") == ["id", "name"]
        assert list(c.rows("users")) == [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": None},
        ]
    finally:
        c.close()


def test_sqlite_connector_quotes_identifier_names(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE "select" (id INTEGER)')
    conn.execute('INSERT INTO "select" VALUES (1)')
    conn.execute('CREATE TABLE "weird""name" ("odd""col" TEXT)')
    conn.execute('INSERT INTO "weird""name" VALUES (?)', ("ok",))
    conn.commit()
    conn.close()

    c = SqliteConnector(db)
    try:
        assert c.columns("select") == ["id"]
        assert list(c.rows("select")) == [{"id": 1}]
        assert c.columns('weird"name') == ['odd"col']
        assert list(c.rows('weird"name')) == [{'odd"col': "ok"}]
    finally:
        c.close()


def test_sqlite_missing_table_rows_raise_connector_error(tmp_path):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE users (id INTEGER)")
    conn.commit()
    conn.close()

    c = SqliteConnector(db)
    try:
        with pytest.raises(ConnectorError):
            list(c.rows("missing"))
    finally:
        c.close()


def test_open_connector_rejects_directory(tmp_path):
    """CSV directories are no longer a data source — a directory must error."""
    with pytest.raises(ConnectorError):
        open_connector(tmp_path)


def test_open_connector_rejects_unknown_file(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ConnectorError):
        open_connector(f)

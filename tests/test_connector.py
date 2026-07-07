"""Connector tests: CSV reading edge cases and SQLite open validation."""

import sqlite3

import pytest

from schemascope.connector import (
    CsvConnector,
    SqliteConnector,
    _resolve_column,
    open_connector,
)
from schemascope.model import ConnectorError


def _write(dir_path, name, text):
    p = dir_path / f"{name}.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_basic_csv_read(tmp_path):
    _write(tmp_path, "users", "id,name\n1,alice\n2,bob\n")
    conn = CsvConnector(tmp_path)
    assert conn.has_entity("users")
    assert conn.columns("users") == ["id", "name"]
    assert list(conn.rows("users")) == [
        {"id": "1", "name": "alice"},
        {"id": "2", "name": "bob"},
    ]


def test_empty_cell_becomes_none(tmp_path):
    _write(tmp_path, "users", "id,age\n1,\n")
    assert list(CsvConnector(tmp_path).rows("users")) == [{"id": "1", "age": None}]


def test_bug2_ragged_row_with_extra_columns_does_not_crash(tmp_path):
    """BUG 2: cells beyond the header land under the ``None`` restkey; they must
    be dropped, not fed to ``str.strip`` (which crashed on the list)."""
    _write(tmp_path, "r", "a,b\n1,2,3\n")
    assert list(CsvConnector(tmp_path).rows("r")) == [{"a": "1", "b": "2"}]


def test_fewer_columns_row_fills_none(tmp_path):
    _write(tmp_path, "r", "a,b,c\n1,2\n")
    assert list(CsvConnector(tmp_path).rows("r")) == [
        {"a": "1", "b": "2", "c": None}
    ]


def test_bug4_utf8_bom_header_not_mangled(tmp_path):
    """BUG 4: a UTF-8 BOM must be stripped so the first column resolves."""
    p = tmp_path / "u.csv"
    p.write_bytes(b"\xef\xbb\xbfid,name\n1,alice\n")
    conn = CsvConnector(tmp_path)
    cols = conn.columns("u")
    assert cols == ["id", "name"]
    assert _resolve_column("id", cols) == "id"
    assert list(conn.rows("u")) == [{"id": "1", "name": "alice"}]


def test_bug5_duplicate_header_columns_error_consistently(tmp_path):
    """BUG 5: duplicate header names silently dropped a column via DictReader.
    Now both ``columns()`` and ``rows()`` raise consistently."""
    _write(tmp_path, "dup", "id,id\n100,200\n")
    conn = CsvConnector(tmp_path)
    with pytest.raises(ConnectorError):
        conn.columns("dup")
    with pytest.raises(ConnectorError):
        list(conn.rows("dup"))


def test_bug3_non_database_file_raises_connector_error(tmp_path):
    """BUG 3: opening a non-database ``.db`` file must raise ``ConnectorError``,
    not leak ``sqlite3.DatabaseError`` on the first query."""
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


def test_open_connector_picks_csv_for_directory(tmp_path):
    assert isinstance(open_connector(tmp_path), CsvConnector)


def test_open_connector_rejects_unknown(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ConnectorError):
        open_connector(f)

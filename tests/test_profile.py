"""End-to-end profiler metrics over a database source (SQLite)."""

import sqlite3
from pathlib import Path

from schemascope.connector import SqliteConnector, open_connector
from schemascope.model import Entity, Field, Schema
from schemascope.profile import profile
from schemascope.schema_loader import load_schema

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _schema():
    return Schema(
        entities=[
            Entity(
                name="users",
                fields=[
                    Field(name="id", type="integer", primary_key=True, nullable=False),
                    Field(name="email", type="string"),
                    Field(name="age", type="integer", nullable=True),
                    Field(name="deleted", type="integer", nullable=False),
                    Field(name="ghost", type="string"),  # column absent from data
                ],
            ),
            Entity(name="orders", fields=[Field(name="id", type="integer")]),  # no table
        ]
    )


def _users_db(tmp_path):
    db = tmp_path / "app.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER, email TEXT, age INTEGER, deleted INTEGER)")
    con.executemany(
        "INSERT INTO users VALUES (?,?,?,?)",
        [(1, "alice@x.com", 31, 0), (2, "bob@x.com", None, 0), (3, "carol@x.com", 27, 1)],
    )
    con.commit()
    con.close()
    return SqliteConnector(db)


def _profile_users(tmp_path):
    c = _users_db(tmp_path)
    try:
        return profile(_schema(), c)
    finally:
        c.close()


def test_profile_metrics(tmp_path):
    report = _profile_users(tmp_path)
    users = report.entity("users")
    assert users.present is True
    assert users.row_count == 3

    fields = {f.name: f for f in users.fields}
    idf = fields["id"]
    assert idf.null_count == 0 and idf.distinct_count == 3
    assert idf.inferred_type == "integer" and idf.type_ok is True

    age = fields["age"]
    assert age.null_count == 1
    assert age.null_fraction == 1 / 3
    assert age.distinct_count == 2  # 31, 27 (NULL is null)
    assert age.inferred_type == "integer" and age.type_ok is True


def test_profile_zero_one_integer_column_is_type_ok(tmp_path):
    """``deleted`` is declared ``integer`` and stored as 0/1; it must profile as
    ``type_ok`` even though the data reads as boolean."""
    deleted = {f.name: f for f in _profile_users(tmp_path).entity("users").fields}["deleted"]
    assert deleted.inferred_type == "boolean"
    assert deleted.type_ok is True


def test_profile_missing_column_reported_not_dropped(tmp_path):
    ghost = {f.name: f for f in _profile_users(tmp_path).entity("users").fields}["ghost"]
    assert ghost.present is False
    assert ghost.column is None
    assert ghost.type_ok is True  # nothing to disagree with


def test_profile_missing_entity_reported(tmp_path):
    orders = _profile_users(tmp_path).entity("orders")
    assert orders.present is False
    assert orders.row_count == 0
    assert all(f.present is False for f in orders.fields)


def test_profile_late_drift_is_flagged(tmp_path):
    """A column declared ``integer`` that stays integer for well past any early
    sample window and only then drifts to text must be flagged: inferred
    ``string`` and ``type_ok`` False."""
    db = tmp_path / "events.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE events (code TEXT)")
    con.executemany(
        "INSERT INTO events VALUES (?)",
        [(str(i),) for i in range(1500)] + [("OOPS",)],
    )
    con.commit()
    con.close()

    schema = Schema(entities=[Entity(
        name="events", fields=[Field(name="code", type="integer", nullable=True)])])
    c = SqliteConnector(db)
    try:
        report = profile(schema, c)
    finally:
        c.close()
    code = {f.name: f for f in report.entity("events").fields}["code"]
    assert code.row_count == 1501
    assert code.inferred_type == "string"
    assert code.type_ok is False


def _example_users_db(tmp_path):
    """The 5-row ``users`` table the shipped example schemas describe."""
    db = tmp_path / "example.sqlite"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE users (id INTEGER, email TEXT, age INTEGER, "
        "active TEXT, deleted INTEGER, signup_date TEXT)"
    )
    con.executemany(
        "INSERT INTO users VALUES (?,?,?,?,?,?)",
        [
            (1, "alice@example.com", 31, "true", 0, "2021-03-05"),
            (2, "bob@example.com", None, "false", 0, "2021-07-19"),
            (3, "carol@example.com", 27, "true", 1, "2022-01-02"),
            (4, "dave@example.com", 44, "true", 0, "2022-11-30"),
            (5, "erin@example.com", None, "false", 0, "2023-05-14"),
        ],
    )
    con.commit()
    con.close()
    return db


def test_profile_over_example_schema(tmp_path):
    """The shipped example schema profiles a matching database cleanly."""
    schema = load_schema(EXAMPLES / "schema.json")
    connector = open_connector(str(_example_users_db(tmp_path)))
    try:
        report = profile(schema, connector)
    finally:
        connector.close()

    users = report.entity("users")
    assert users.present is True and users.row_count == 5
    assert all(f.type_ok for f in users.fields)
    assert {f.name: f for f in users.fields}["age"].null_count == 2


def test_example_schema_formats_produce_one_model(tmp_path):
    """The shipped JSON/YAML/XML/TXT schemas promise one canonical model."""
    db = _example_users_db(tmp_path)
    reports = []
    for fmt in ("json", "yaml", "xml", "txt"):
        schema = load_schema(EXAMPLES / f"schema.{fmt}")
        connector = open_connector(str(db))
        try:
            reports.append(profile(schema, connector).to_dict())
        finally:
            connector.close()

    assert reports == [reports[0]] * len(reports)

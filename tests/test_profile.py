"""End-to-end profiler metrics over a CSV source."""

from pathlib import Path

from schemascope.connector import CsvConnector, open_connector
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
            Entity(name="orders", fields=[Field(name="id", type="integer")]),  # no store
        ]
    )


def _write_users(tmp_path):
    (tmp_path / "users.csv").write_text(
        "id,email,age,deleted\n"
        "1,alice@x.com,31,0\n"
        "2,bob@x.com,,0\n"
        "3,carol@x.com,27,1\n",
        encoding="utf-8",
    )


def test_profile_metrics(tmp_path):
    _write_users(tmp_path)
    report = profile(_schema(), CsvConnector(tmp_path))

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
    assert age.distinct_count == 2  # 31, 27 (blank is null)
    assert age.inferred_type == "integer" and age.type_ok is True


def test_profile_bug6_zero_one_integer_column_is_type_ok(tmp_path):
    """BUG 6 end-to-end: ``deleted`` is declared ``integer`` and stored as 0/1;
    it must profile as ``type_ok`` even though the data reads as boolean."""
    _write_users(tmp_path)
    report = profile(_schema(), CsvConnector(tmp_path))
    deleted = {f.name: f for f in report.entity("users").fields}["deleted"]
    assert deleted.inferred_type == "boolean"
    assert deleted.type_ok is True


def test_profile_missing_column_reported_not_dropped(tmp_path):
    _write_users(tmp_path)
    report = profile(_schema(), CsvConnector(tmp_path))
    ghost = {f.name: f for f in report.entity("users").fields}["ghost"]
    assert ghost.present is False
    assert ghost.column is None
    assert ghost.type_ok is True  # nothing to disagree with


def test_profile_missing_entity_reported(tmp_path):
    _write_users(tmp_path)
    report = profile(_schema(), CsvConnector(tmp_path))
    orders = report.entity("orders")
    assert orders.present is False
    assert orders.row_count == 0
    assert all(f.present is False for f in orders.fields)


def test_profile_over_bundled_examples():
    """README claim: profiling the shipped example works and reports clean."""
    schema = load_schema(EXAMPLES / "schema.json")
    connector = open_connector(EXAMPLES / "data")
    try:
        report = profile(schema, connector)
    finally:
        connector.close()

    users = report.entity("users")
    assert users.present is True and users.row_count == 5
    assert all(f.type_ok for f in users.fields)
    age = {f.name: f for f in users.fields}["age"]
    assert age.null_count == 2


def test_profile_late_drift_is_flagged(tmp_path):
    """A column declared ``integer`` that stays integer for well past any early
    sample window and only then drifts to text must be flagged: inferred
    ``string`` and ``type_ok`` False. This is the drift-detection guarantee for
    large files."""
    lines = ["code"] + [str(i) for i in range(1500)] + ["OOPS"]  # 1500 ints, then text
    (tmp_path / "events.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    schema = Schema(entities=[Entity(
        name="events", fields=[Field(name="code", type="integer", nullable=True)])])
    report = profile(schema, CsvConnector(tmp_path))
    code = {f.name: f for f in report.entity("events").fields}["code"]
    assert code.row_count == 1501
    assert code.inferred_type == "string"
    assert code.type_ok is False


def test_profile_bundled_examples_match_across_schema_formats():
    """The shipped JSON/YAML/XML/TXT schemas promise one canonical model."""
    reports = []
    for fmt in ("json", "yaml", "xml", "txt"):
        schema = load_schema(EXAMPLES / f"schema.{fmt}")
        connector = open_connector(EXAMPLES / "data")
        try:
            reports.append(profile(schema, connector).to_dict())
        finally:
            connector.close()

    assert reports == [reports[0]] * len(reports)

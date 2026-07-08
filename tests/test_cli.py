"""CLI end-to-end: valid JSON and YAML output, and error exit codes."""

import json
import sqlite3

import pytest

from schemascope.cli import main


def _setup(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(
        '{"entities": [{"name": "users", "fields": ['
        '{"name": "id", "type": "integer", "primary_key": true},'
        '{"name": "email", "type": "string"}]}]}',
        encoding="utf-8",
    )
    db = tmp_path / "app.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER, email TEXT)")
    con.executemany("INSERT INTO users VALUES (?,?)", [(1, "alice@x.com"), (2, "bob@x.com")])
    con.commit()
    con.close()
    return schema, db


def test_cli_emits_valid_json(tmp_path, capsys):
    schema, db = _setup(tmp_path)
    rc = main([str(schema), str(db)])
    assert rc == 0

    report = json.loads(capsys.readouterr().out)  # must be valid JSON
    users = report["entities"][0]
    assert users["name"] == "users" and users["row_count"] == 2
    idf = {f["name"]: f for f in users["fields"]}["id"]
    assert idf["inferred_type"] == "integer" and idf["type_ok"] is True


def test_cli_emits_valid_yaml(tmp_path, capsys):
    yaml = pytest.importorskip("yaml")
    schema, db = _setup(tmp_path)
    rc = main([str(schema), str(db), "--output", "yaml"])
    assert rc == 0
    report = yaml.safe_load(capsys.readouterr().out)  # must be valid YAML
    assert report["entities"][0]["row_count"] == 2


def test_cli_json_and_yaml_describe_same_report(tmp_path, capsys):
    yaml = pytest.importorskip("yaml")
    schema, db = _setup(tmp_path)

    assert main([str(schema), str(db)]) == 0
    as_json = json.loads(capsys.readouterr().out)
    assert main([str(schema), str(db), "-o", "yaml"]) == 0
    as_yaml = yaml.safe_load(capsys.readouterr().out)
    assert as_json == as_yaml


def test_cli_bad_schema_returns_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x"}', encoding="utf-8")  # missing 'entities'
    _, db = _setup(tmp_path)
    rc = main([str(bad), str(db)])
    assert rc == 2
    assert "schema error" in capsys.readouterr().err


def test_cli_bad_data_source_returns_2(tmp_path, capsys):
    schema, _ = _setup(tmp_path)
    missing = tmp_path / "nope.txt"
    missing.write_text("x", encoding="utf-8")
    rc = main([str(schema), str(missing)])
    assert rc == 2
    assert "data source error" in capsys.readouterr().err

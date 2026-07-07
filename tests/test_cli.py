"""CLI end-to-end: valid JSON and YAML output, and error exit codes."""

import json

import pytest

from schemascope.cli import main


def _setup(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(
        '{"entities": [{"name": "users", "source": "users", "fields": ['
        '{"name": "id", "type": "integer", "primary_key": true},'
        '{"name": "email", "type": "string"}]}]}',
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "users.csv").write_text(
        "id,email\n1,alice@x.com\n2,bob@x.com\n", encoding="utf-8"
    )
    return schema, data


def test_cli_emits_valid_json(tmp_path, capsys):
    schema, data = _setup(tmp_path)
    rc = main([str(schema), str(data)])
    assert rc == 0

    out = capsys.readouterr().out
    report = json.loads(out)  # must be valid JSON
    users = report["entities"][0]
    assert users["name"] == "users" and users["row_count"] == 2
    idf = {f["name"]: f for f in users["fields"]}["id"]
    assert idf["inferred_type"] == "integer" and idf["type_ok"] is True


def test_cli_emits_valid_yaml(tmp_path, capsys):
    yaml = pytest.importorskip("yaml")
    schema, data = _setup(tmp_path)
    rc = main([str(schema), str(data), "--output", "yaml"])
    assert rc == 0

    out = capsys.readouterr().out
    report = yaml.safe_load(out)  # must be valid YAML
    assert report["entities"][0]["row_count"] == 2


def test_cli_json_and_yaml_describe_same_report(tmp_path, capsys):
    yaml = pytest.importorskip("yaml")
    schema, data = _setup(tmp_path)

    assert main([str(schema), str(data)]) == 0
    as_json = json.loads(capsys.readouterr().out)
    assert main([str(schema), str(data), "-o", "yaml"]) == 0
    as_yaml = yaml.safe_load(capsys.readouterr().out)
    assert as_json == as_yaml


def test_cli_bad_schema_returns_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x"}', encoding="utf-8")  # missing 'entities'
    data = tmp_path / "data"
    data.mkdir()
    rc = main([str(bad), str(data)])
    assert rc == 2
    assert "schema error" in capsys.readouterr().err


def test_cli_bad_data_source_returns_2(tmp_path, capsys):
    schema, _ = _setup(tmp_path)
    missing = tmp_path / "nope.txt"
    missing.write_text("x", encoding="utf-8")
    rc = main([str(schema), str(missing)])
    assert rc == 2
    assert "data source error" in capsys.readouterr().err

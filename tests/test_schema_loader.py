"""Schema loading: the cross-format equivalence keystone, format detection,
XML namespaces, and error handling."""

import pytest

from schemascope.model import SchemaError
from schemascope.schema_loader import detect_format, load_schema

# The SAME schema in all four formats, deliberately using different raw type
# spellings (int / integer / INT, varchar / string / TEXT) to prove
# normalization. No schema-level name/source is set anywhere, so all four whole
# Schema objects must compare equal with a single ``==``.
JSON = """
{"entities": [{"name": "users", "fields": [
  {"name": "id", "type": "int", "primary_key": true},
  {"name": "email", "type": "varchar"},
  {"name": "age", "type": "integer", "nullable": true}
]}]}
"""

YAML = """
entities:
  - name: users
    fields:
      - {name: id, type: integer, primary_key: true}
      - {name: email, type: string}
      - {name: age, type: int, nullable: true}
"""

XML = """
<schema>
  <entity name="users">
    <field name="id" type="INT" primary_key="true"/>
    <field name="email" type="TEXT"/>
    <field name="age" type="integer" nullable="true"/>
  </entity>
</schema>
"""

TXT = """
entity users
  id: int pk
  email: string
  age: integer null
"""


def _load(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return load_schema(p)


def test_keystone_cross_format_equivalence(tmp_path):
    j = _load(tmp_path, "s.json", JSON)
    y = _load(tmp_path, "s.yaml", YAML)
    x = _load(tmp_path, "s.xml", XML)
    t = _load(tmp_path, "s.txt", TXT)
    assert j == y == x == t

    # Spot-check the normalized model the equality rests on.
    users = j.entity("users")
    assert users is not None
    idf = users.field_by_name("id")
    assert idf.type == "integer" and idf.primary_key and idf.nullable is False
    assert users.field_by_name("email").type == "string"
    age = users.field_by_name("age")
    assert age.type == "integer" and age.nullable is True


@pytest.mark.parametrize(
    "path, content, expected",
    [
        ("s.json", '{"entities": []}', "json"),
        ("s.yaml", "entities: []", "yaml"),
        ("s.xml", "<schema/>", "xml"),
        ("s.txt", "entity u", "txt"),
        # extensionless: content sniffing
        ("s", '{"entities": []}', "json"),
        ("s", "<schema/>", "xml"),
        ("s", "entities:\n  - name: u\n    fields: []\n", "yaml"),
    ],
)
def test_detect_format(path, content, expected):
    assert detect_format(path, content) == expected


@pytest.mark.parametrize(
    "content",
    [
        "id: int\n",  # QA minor edge: single DSL line is valid YAML
        "entity: users\nid: int pk\n",  # BUG 9: colon-form DSL is valid YAML
    ],
)
def test_bug9_dsl_content_not_missniffed_as_yaml(content):
    """BUG 9: DSL that happens to be valid YAML but lacks an ``entities`` key
    must sniff as ``txt``, not ``yaml`` (which then failed misleadingly)."""
    assert detect_format("noext", content) == "txt"


def test_bug8_namespaced_xml_parses(tmp_path):
    """BUG 8: a default ``xmlns`` turned tags into ``{uri}schema`` and broke
    parsing; namespaces must be ignored."""
    xml = (
        '<schema xmlns="http://ex.com/s">'
        '<entity name="u"><field name="id" type="int"/></entity></schema>'
    )
    schema = _load(tmp_path, "s.xml", xml)
    assert schema.entity("u").field_by_name("id").type == "integer"


def test_missing_entities_key_is_schema_error(tmp_path):
    with pytest.raises(SchemaError):
        _load(tmp_path, "s.json", '{"name": "x"}')


def test_non_string_type_loads_as_unknown(tmp_path):
    """BUG 1 at the loader boundary: a non-string ``type`` does not crash."""
    schema = _load(
        tmp_path,
        "s.json",
        '{"entities": [{"name": "u", "fields": [{"name": "id", "type": 123}]}]}',
    )
    assert schema.entity("u").field_by_name("id").type == "unknown"


def test_empty_file_is_schema_error(tmp_path):
    with pytest.raises(SchemaError):
        _load(tmp_path, "s.json", "")


def test_duplicate_field_name_is_schema_error(tmp_path):
    with pytest.raises(SchemaError):
        _load(
            tmp_path,
            "s.json",
            '{"entities": [{"name": "u", "fields": ['
            '{"name": "a"}, {"name": "a"}]}]}',
        )

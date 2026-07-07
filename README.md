# schemascope

Profile a data source against a schema — across four schema formats.

`schemascope` loads a schema written as **JSON, YAML, XML, or a small TXT DSL**
into one canonical model, then profiles a data source (a directory of CSV files
or a SQLite database) against it: per-field null counts, distinct counts, the
type inferred from the actual values, and whether that data agrees with the
declared schema type.

Equivalent schemas in any of the four formats normalize to an *identical* model,
so the same tooling works no matter how the schema was written.

## Install

```bash
pip install -e .
```

Requires Python 3.8+ and PyYAML (installed automatically).

## Command line

```bash
schemascope SCHEMA DATA [--output json|yaml] [--schema-format json|yaml|xml|txt]
```

* `SCHEMA` — a schema file. The format is auto-detected from the extension, or
  sniffed from the content; override with `--schema-format`.
* `DATA` — a directory of `<entity>.csv` files, or a `.db`/`.sqlite` file with
  one table per entity.

Run the bundled example:

```bash
schemascope examples/schema.json examples/data
```

Abbreviated output:

```json
{
  "entities": [
    {
      "name": "users",
      "source": "users",
      "present": true,
      "row_count": 5,
      "fields": [
        {
          "name": "age",
          "declared_type": "integer",
          "column": "age",
          "present": true,
          "row_count": 5,
          "null_count": 2,
          "null_fraction": 0.4,
          "distinct_count": 3,
          "inferred_type": "integer",
          "type_ok": true
        }
      ]
    }
  ]
}
```

`python -m schemascope ...` works identically to the `schemascope` command.

## Python API

```python
import schemascope

schema = schemascope.load_schema("examples/schema.json")
connector = schemascope.open_connector("examples/data")
try:
    report = schemascope.profile(schema, connector)
finally:
    connector.close()

for entity in report.entities:
    for f in entity.fields:
        print(f.name, f.inferred_type, f.null_fraction, f.type_ok)

print(schemascope.__version__)
```

## Schema formats

All four of these describe the *same* schema:

**JSON / YAML**

```yaml
name: demo
entities:
  - name: users
    fields:
      - {name: id, type: integer, primary_key: true}
      - {name: email, type: string}
      - {name: age, type: integer, nullable: true}
```

**XML** (attribute-based; a default `xmlns` is fine)

```xml
<schema name="demo">
  <entity name="users">
    <field name="id" type="integer" primary_key="true"/>
    <field name="email" type="string"/>
    <field name="age" type="integer" nullable="true"/>
  </entity>
</schema>
```

**TXT DSL**

```text
entity users
  id: integer pk
  email: string
  age: integer null
```

Type names are normalized (`int`/`integer`/`INTEGER` → `integer`,
`varchar`/`text`/`string` → `string`, …). A primary key is treated as not-null
unless nullability is stated explicitly.

## What the profile reports

Per field: `row_count`, `null_count`, `null_fraction`, `distinct_count`, the
`inferred_type` (from the data), and `type_ok` — whether the inferred type is
compatible with the declared one. `type_ok` is intentionally lenient: a `string`
column accepts anything, a `float` column accepts integer data, and an
`integer` column accepts an all-0/1 column that reads as boolean. A field whose
column is missing from the source, or an entity whose backing store is missing,
is reported with `present: false` rather than dropped.

## Development

```bash
python3 -m pytest -q
```

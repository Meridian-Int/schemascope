# schemascope

`schemascope` profiles tabular data against a lightweight schema.

Point it at a schema written in JSON, YAML, XML, or a small TXT DSL, then point
it at either a directory of CSV files or a SQLite database. It reports which
entities and columns are present, row counts, null counts, distinct counts, the
type inferred from the observed values, and whether that inferred type is
compatible with the declared schema type.

Use it when you want a quick, scriptable check for schema drift:

- Did every expected table or CSV file arrive?
- Did every expected column arrive?
- Are nulls showing up where you did not expect them?
- Does the data still look like the declared type?
- Do equivalent schemas in different formats behave the same way?

`schemascope` is a profiler and drift detector. It does not modify data, create
tables, enforce constraints, or validate every row against a rich schema
language.

## Install

For local development:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Requires Python 3.8+ and PyYAML. PyYAML is installed automatically from the
package metadata. A current pip is recommended because older pip versions may
not support editable installs for `pyproject.toml` projects.

## Quick Start

Run the bundled example:

```bash
schemascope examples/schema.json examples/data
```

The example schema declares one `users` entity. The data source is a directory
containing `users.csv`.

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

The same example schema is included in all supported formats:

```bash
schemascope examples/schema.json examples/data
schemascope examples/schema.yaml examples/data
schemascope examples/schema.xml examples/data
schemascope examples/schema.txt examples/data
```

Those four files normalize to the same model and produce the same profile.

## Command Line

```bash
schemascope SCHEMA DATA [--output json|yaml] [--schema-format json|yaml|xml|txt]
```

Arguments:

- `SCHEMA`: path to a JSON, YAML, XML, or TXT schema file.
- `DATA`: a directory of CSV files, or a SQLite database file.

Options:

- `-o, --output json|yaml`: choose report format. Defaults to `json`.
- `--schema-format json|yaml|xml|txt`: override schema format detection.
- `--version`: print the package version.
- `--help`: print CLI help.

`python -m schemascope ...` works the same way as the `schemascope` console
script:

```bash
python -m schemascope examples/schema.json examples/data --output yaml
```

Exit codes:

- `0`: success.
- `2`: bad arguments, schema errors, or data-source errors. Schema and
  data-source errors are printed to stderr.

## Schema Model

Every schema format is normalized into the same model:

- A schema has optional `name` and `version` metadata.
- A schema contains one or more `entities`.
- Each entity has a `name`, optional `source`, optional `description`, and one
  or more fields.
- Each field has a `name`, a canonical `type`, `nullable`, `primary_key`, and
  optional `description`.

The profiler reads data from `entity.source` when it is set; otherwise it uses
`entity.name`. For CSV data, that means `<source>.csv`. For SQLite data, that
means a table named `<source>`.

Entity names must be unique. Field names must be unique within each entity.
Every schema must define at least one entity, and every entity must define at
least one field.

## Schema Formats

These schemas describe the same common model.

### JSON

```json
{
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id", "type": "integer", "primary_key": true},
        {"name": "email", "type": "string"},
        {"name": "age", "type": "integer", "nullable": true}
      ]
    }
  ]
}
```

### YAML

```yaml
entities:
  - name: users
    fields:
      - {name: id, type: integer, primary_key: true}
      - {name: email, type: string}
      - {name: age, type: integer, nullable: true}
```

### XML

XML is attribute-based. A default XML namespace is allowed and ignored during
parsing.

```xml
<schema>
  <entity name="users">
    <field name="id" type="integer" primary_key="true"/>
    <field name="email" type="string"/>
    <field name="age" type="integer" nullable="true"/>
  </entity>
</schema>
```

### TXT DSL

The TXT format is intentionally small:

```text
entity users
  id: integer pk
  email: string
  age: integer null
```

TXT rules:

- Blank lines and `#` comments are ignored.
- Entity lines are `entity <name>` or `entity <name>:`.
- Field lines are `<field>: <type> [flags...]`.
- Supported flags are `pk`, `primary_key`, `primary key`, `null`, `nullable`,
  `not null`, `notnull`, and `required`.
- `unique` is accepted in the field text but is currently ignored.
- Indentation is cosmetic.

TXT does not currently represent schema-level `name` or `version`, entity
`source`, or descriptions. For strict whole-model equality across JSON, YAML,
XML, and TXT, use only the subset of metadata the TXT DSL can express.

### Richer JSON/YAML/XML Metadata

JSON and YAML support this shape:

```yaml
name: customer_exports
version: "2026-07"
entities:
  - name: users
    source: app_users
    description: User account export
    fields:
      - name: id
        type: integer
        primary_key: true
        description: Internal user id
      - name: email
        type: varchar
      - name: created_at
        type: timestamp
        nullable: false
```

XML supports the same metadata as attributes:

```xml
<schema name="customer_exports" version="2026-07">
  <entity name="users" source="app_users" description="User account export">
    <field name="id" type="integer" primary_key="true" description="Internal user id"/>
    <field name="email" type="varchar"/>
    <field name="created_at" type="timestamp" nullable="false"/>
  </entity>
</schema>
```

## Type Names

Declared type names are normalized before profiling.

Canonical type | Accepted aliases
--- | ---
`string` | `str`, `string`, `text`, `varchar`, `char`, `uuid`, `enum`
`integer` | `int`, `integer`, `bigint`, `smallint`, `long`
`float` | `float`, `double`, `decimal`, `numeric`, `real`, `number`
`boolean` | `bool`, `boolean`
`date` | `date`
`datetime` | `datetime`, `timestamp`
`unknown` | empty, missing, non-string, or unrecognized type names

Type matching is case-insensitive and ignores surrounding whitespace.

A primary key is treated as not nullable unless `nullable` is explicitly set.
For example, `{"name": "id", "type": "int", "primary_key": true}` normalizes to
an integer field with `nullable: false`.

## Data Sources

### CSV Directory

Pass a directory that contains one CSV file per entity:

```text
data/
  users.csv
  orders.csv
```

If the schema entity is named `users`, `schemascope` looks for `users.csv`. If
the entity has `source: app_users`, it looks for `app_users.csv`.

CSV behavior:

- The first row is the header.
- Files are read as `utf-8-sig`, so a UTF-8 BOM is handled.
- Duplicate header names are rejected.
- Empty cells count as nulls.
- Whitespace-only cells also count as nulls.
- Extra cells beyond the header are ignored.
- Short rows fill missing cells as nulls.

The CLI uses only the default CSV null token: an empty string after stripping.
From Python, you can opt into more null spellings:

```python
from schemascope import CsvConnector, load_schema, profile

schema = load_schema("schema.json")
connector = CsvConnector("data", null_tokens={"", "NULL", "NA", "N/A"})
try:
    report = profile(schema, connector)
finally:
    connector.close()
```

### SQLite Database

Pass a `.db`, `.sqlite`, or `.sqlite3` file:

```bash
schemascope schema.yaml warehouse.sqlite
```

Each entity maps to a table named by `entity.source` or `entity.name`.
SQLite values are read with their native Python types where SQLite provides
them.

### Column Matching

Fields are matched to source columns by name:

1. Exact column name match.
2. Case-insensitive fallback.
3. If no column matches, the field is reported with `present: false`.

Entity/table/file matching uses the entity source or name. Missing entities are
reported with `present: false` rather than silently dropped.

## Output Reference

The top-level report is:

```json
{
  "entities": []
}
```

Each entity report contains:

Field | Meaning
--- | ---
`name` | Schema entity name
`source` | CSV file stem or SQLite table name used for this entity
`present` | Whether the backing CSV file or SQLite table exists
`row_count` | Number of rows scanned for this entity
`fields` | Per-field profile objects

Each field report contains:

Field | Meaning
--- | ---
`name` | Schema field name
`declared_type` | Canonical schema type after normalization
`column` | Actual matched source column, or `null` if absent
`present` | Whether the column was found
`row_count` | Number of rows scanned for this field when present
`null_count` | Number of null values
`null_fraction` | `null_count / row_count`, rounded to 6 decimals in serialized output
`distinct_count` | Count of distinct non-null values
`inferred_type` | Type inferred from observed non-null values
`type_ok` | Whether `inferred_type` is compatible with `declared_type`

Missing entities and missing columns stay in the report with `present: false`.
That makes drift visible instead of dropping absent objects from the output.

## Type Inference

`schemascope` infers one type per field from observed non-null values.

Inference checks the first 1000 non-null values for each field. A type is chosen
only when every sampled value matches that type. If no specific type matches,
the inferred type is `string`. If there are no non-null values, the inferred
type is `unknown`.

Inference order:

1. `boolean`
2. `integer`
3. `float`
4. `date`
5. `datetime`
6. `string` fallback

Recognized values:

- Boolean: real booleans or `true`, `false`, `1`, `0`, `yes`, `no`, `t`, `f`,
  `y`, `n` case-insensitively.
- Integer: real integers or ASCII integer strings such as `1`, `0`, `-12`,
  `+42`. Real booleans are not integers.
- Float: real integers/floats or strings that parse as finite floats. `nan`,
  `inf`, and `infinity` are rejected.
- Date: strict `YYYY-MM-DD` calendar dates.
- Datetime: `YYYY-MM-DD` followed by a space or `T` and an `HH:MM` or
  `HH:MM:SS` time. Fractional seconds and a trailing `Z` are accepted.

Compatibility is intentionally lenient:

- Equal declared and inferred types are compatible.
- Declared `string` accepts any inferred type.
- Declared `float` accepts inferred `integer`.
- Declared `integer` accepts inferred `boolean`, because all-0/1 columns often
  infer as boolean but are still valid integers.
- `unknown` on either side is treated as compatible.

Everything else is considered a type mismatch.

## Python API

The main API is available from the top-level package:

```python
import schemascope

schema = schemascope.load_schema("examples/schema.json")
connector = schemascope.open_connector("examples/data")

try:
    report = schemascope.profile(schema, connector)
finally:
    connector.close()

for entity in report.entities:
    print(entity.name, entity.present, entity.row_count)
    for field in entity.fields:
        print(
            field.name,
            field.present,
            field.inferred_type,
            field.null_fraction,
            field.type_ok,
        )

print(report.to_dict())
print(schemascope.__version__)
```

Common imports:

```python
from schemascope import (
    CsvConnector,
    SqliteConnector,
    load_schema,
    open_connector,
    profile,
)
```

`open_connector(path)` chooses a connector automatically:

- Directory -> `CsvConnector`
- `.db`, `.sqlite`, `.sqlite3` file -> `SqliteConnector`

The caller owns connector lifecycle. Close connectors when finished.

## Format Detection

Known file extensions are authoritative:

Extension | Format
--- | ---
`.json` | JSON
`.yaml`, `.yml` | YAML
`.xml` | XML
`.txt`, `.dsl`, `.schema` | TXT DSL

For unknown extensions, content is sniffed:

- Leading `<` -> XML
- Leading `{` or `[` -> JSON
- YAML mapping with an `entities` key -> YAML
- Anything else -> TXT DSL

Use `--schema-format` when a file extension is misleading or absent:

```bash
schemascope schemafile data/ --schema-format yaml
```

## Limitations

- This is not a full data validation engine. It profiles presence, nulls,
  distinct counts, inferred types, and type compatibility.
- It does not enforce foreign keys, uniqueness, ranges, regexes, or custom
  constraints.
- Type inference samples the first 1000 non-null values per field.
- `distinct_count` tracks all distinct non-null values for each profiled field,
  which is simple and exact but not approximate-memory analytics.
- TXT schemas do not support metadata such as schema name, version, source, or
  descriptions.
- The CLI exposes the default CSV null handling only. Use the Python API for
  custom CSV null tokens.

## Development

Run tests:

```bash
python3 -m pytest -q
```

Build local artifacts:

```bash
python3 -m build --sdist --wheel --outdir dist
```

The source distribution includes the examples used in this README.

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

`schemascope` is a profiler and drift detector. It does **not** modify data,
create tables, enforce constraints, or validate every row against a rich schema
language.

---

## Table of Contents

- [Who this is for and what you'll need](#who-this-is-for-and-what-youll-need)
- [Concepts and glossary](#concepts-and-glossary)
- [Install](#install)
- [Your first run: a guided walkthrough](#your-first-run-a-guided-walkthrough)
- [How to write your own schema, step by step](#how-to-write-your-own-schema-step-by-step)
- [Run it on your own data (the bridge)](#run-it-on-your-own-data-the-bridge)
- [Command Line](#command-line)
- [Schema Model](#schema-model)
- [Schema Formats](#schema-formats)
- [Type Names](#type-names)
- [Data Sources](#data-sources)
- [Output Reference](#output-reference)
- [Type Inference](#type-inference)
- [Python API](#python-api)
- [Format Detection](#format-detection)
- [Limitations](#limitations)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Appendix A: Generating a schemascope schema from your database](#appendix-a-generating-a-schemascope-schema-from-your-database)
- [Appendix B: Type-mapping cheat sheet](#appendix-b-type-mapping-cheat-sheet)

---

## Who this is for and what you'll need

This manual is written for a junior engineer who has never seen `schemascope`
before. If you can run a command in a terminal and edit a text file, you can use
this tool. You do not need to read the source code.

You will need:

- **Python 3.8 or newer** (this is the version floor declared in
  `pyproject.toml`). PyYAML is installed automatically; there are no other
  runtime dependencies.
- **A data source** that is one of exactly two things:
  1. a **directory of CSV files** (one `.csv` file per table), or
  2. a single **SQLite database file** (`.db`, `.sqlite`, or `.sqlite3`).

That second point is the single most important fact in this manual:
**`schemascope` cannot connect to a live database.** It cannot open a Postgres,
MySQL, SQL Server, Oracle, MongoDB, BigQuery, Snowflake, or Redshift connection
string. If your data lives in one of those systems, you first export it to CSV
files or into a SQLite file, and *then* point `schemascope` at that. The whole
of [Appendix A](#appendix-a-generating-a-schemascope-schema-from-your-database)
is step-by-step recipes for doing exactly that, per platform.

---

## Concepts and glossary

A few terms are used throughout this manual. Each is one or two sentences.

- **Entity** — one table. In a CSV data source, one entity maps to one CSV file
  (`users` entity -> `users.csv`). In a SQLite data source, one entity maps to
  one table.
- **Field** — one column of an entity (for example, `email` or `age`).
- **Source** — the *backing store* name schemascope actually reads for an entity:
  the CSV file stem or the SQLite table name. It defaults to the entity's `name`
  but can be overridden with a `source` value (see [`source` vs
  `name`](#source-vs-name)).
- **Declared type** — the type you *wrote* in your schema for a field (for
  example, `integer`). schemascope normalizes it to one of seven canonical types.
- **Inferred type** — the type schemascope *deduces from the actual data values*
  it scans in the CSV/SQLite source. Declared and inferred can differ; comparing
  them is the point of the tool.
- **Null** — a missing value. In CSV, an empty cell (or a whitespace-only cell)
  counts as null. In SQLite, a real `NULL` counts as null.
- **null_fraction** — `null_count / row_count` for a field: the share of rows
  where that field was null. `0.4` means 40% of rows were null.
- **Distinct count** — the number of *different* non-null values seen in a field.
  A column of `0,0,1,0,0` has a distinct count of 2 (the values `0` and `1`).
- **Schema drift** — when the data no longer matches what the schema expects: a
  table went missing, a column disappeared, nulls appeared where they should
  not, or a column's values stopped looking like the declared type. Detecting
  drift is what schemascope is for.
- **Connector** — schemascope's internal reader for a data source. There are two:
  a CSV-directory connector and a SQLite connector. You never construct these by
  hand from the CLI; the tool picks one for you based on what you point it at.
- **Present** — whether a thing was actually found. An entity is `present: true`
  if its backing file/table exists; a field is `present: true` if a matching
  column exists. When something is missing, schemascope keeps it in the report
  with `present: false` instead of dropping it silently — that is how drift stays
  visible.

---

## Install

```bash
pip install schemascope
```

Then confirm it's on your `PATH`:

```bash
schemascope --version
```

You should see a line like `schemascope 0.1.0`.

Requires Python 3.8 or newer; PyYAML installs automatically with the package.
There are no other dependencies.

To work on schemascope from a source checkout instead:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

(A current pip is recommended — older versions may not support editable installs
for `pyproject.toml` projects.)

Everywhere this manual writes `schemascope ...`, you can equally write
`python -m schemascope ...`. They are the same program; use whichever is
convenient (the `python -m` form is handy if the console script is not on your
`PATH`).

---

## Your first run: a guided walkthrough

The repository ships a complete, tiny example. Running it is the fastest way to
understand what schemascope does. From the root of a source checkout:

```bash
schemascope examples/schema.json examples/data
```

Here, `examples/schema.json` is the **schema** and `examples/data` is the
**data source** (a directory). The schema declares one entity, `users`, and the
directory contains one file, `users.csv`.

The data file, `examples/data/users.csv`, is:

```text
id,email,age,active,deleted,signup_date
1,alice@example.com,31,true,0,2021-03-05
2,bob@example.com,,false,0,2021-07-19
3,carol@example.com,27,true,1,2022-01-02
4,dave@example.com,44,true,0,2022-11-30
5,erin@example.com,,false,0,2023-05-14
```

The schema, `examples/schema.json`, is:

```json
{
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id", "type": "integer", "primary_key": true},
        {"name": "email", "type": "string"},
        {"name": "age", "type": "integer", "nullable": true},
        {"name": "active", "type": "boolean"},
        {"name": "deleted", "type": "integer", "nullable": false},
        {"name": "signup_date", "type": "date"}
      ]
    }
  ]
}
```

### The real output

Running the command above prints this exact JSON to standard output:

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
          "name": "id",
          "declared_type": "integer",
          "column": "id",
          "present": true,
          "row_count": 5,
          "null_count": 0,
          "null_fraction": 0.0,
          "distinct_count": 5,
          "inferred_type": "integer",
          "type_ok": true
        },
        {
          "name": "email",
          "declared_type": "string",
          "column": "email",
          "present": true,
          "row_count": 5,
          "null_count": 0,
          "null_fraction": 0.0,
          "distinct_count": 5,
          "inferred_type": "string",
          "type_ok": true
        },
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
        },
        {
          "name": "active",
          "declared_type": "boolean",
          "column": "active",
          "present": true,
          "row_count": 5,
          "null_count": 0,
          "null_fraction": 0.0,
          "distinct_count": 2,
          "inferred_type": "boolean",
          "type_ok": true
        },
        {
          "name": "deleted",
          "declared_type": "integer",
          "column": "deleted",
          "present": true,
          "row_count": 5,
          "null_count": 0,
          "null_fraction": 0.0,
          "distinct_count": 2,
          "inferred_type": "boolean",
          "type_ok": true
        },
        {
          "name": "signup_date",
          "declared_type": "date",
          "column": "signup_date",
          "present": true,
          "row_count": 5,
          "null_count": 0,
          "null_fraction": 0.0,
          "distinct_count": 5,
          "inferred_type": "date",
          "type_ok": true
        }
      ]
    }
  ]
}
```

### Reading it field by field

At the entity level: `"present": true` means `users.csv` was found, and
`"row_count": 5` means it has 5 data rows (the header row does not count).
`"source": "users"` is the file stem schemascope read (`users` -> `users.csv`).

Now walk each field against the five rows above.

- **`id`** — Values `1,2,3,4,5`. All five are whole numbers, none empty, all
  different. So `null_count` is 0, `distinct_count` is 5, and the inferred type
  is `integer`. You declared `integer`, so `type_ok` is `true`. (It is not
  inferred as `boolean` because the values are not all 0/1.)

- **`email`** — Five different addresses, none empty. `null_count` 0,
  `distinct_count` 5. The values are not numbers, dates, or booleans, so
  inference falls back to `string`. Declared `string`, so `type_ok` is `true`.

- **`age`** — Values `31, (empty), 27, 44, (empty)`. Two rows (bob and erin) have
  an empty cell, which counts as null: `null_count` is 2 and
  `null_fraction` is `2 / 5 = 0.4`. The three remaining non-null values (`31`,
  `27`, `44`) are all different, so `distinct_count` is 3. schemascope only ever
  infers a type from **non-null** values, and all three are whole numbers, so the
  inferred type is `integer`. Declared `integer`; `type_ok` is `true`. This is a
  healthy result: nulls are fine here because the field is declared
  `nullable: true`.

- **`active`** — Values `true, false, true, true, false`. Only two distinct
  values, so `distinct_count` is 2. `true`/`false` are recognized boolean tokens,
  so the inferred type is `boolean`, matching the declared `boolean`.

- **`deleted`** — Values `0, 0, 1, 0, 0`. Here is the interesting one. You
  **declared** it `integer`, but schemascope **infers** `boolean` — because every
  value is either `0` or `1`, and boolean is the most specific type that fits an
  all-0/1 column. `distinct_count` is 2 (the values `0` and `1`). Even though
  declared and inferred differ, `type_ok` is still `true`. That is deliberate:
  schemascope knows an all-0/1 column often reads as boolean but is a perfectly
  valid integer, so a declared `integer` accepts an inferred `boolean` without
  complaint. This is *not* drift.

- **`signup_date`** — Five different `YYYY-MM-DD` dates. `distinct_count` is 5.
  They match schemascope's strict date format, so the inferred type is `date`,
  matching the declared `date`.

Every `type_ok` is `true` and nothing is missing, so this data is clean against
this schema. If, say, `users.csv` disappeared, the `users` entity would come back
`"present": false`; if the `age` column were dropped, its field would come back
`"present": false`; if someone put the word `"unknown"` in the `age` column, its
inferred type would flip to `string` and `type_ok` would become `false`. That is
what drift looks like in the output.

### The same schema in four formats

The example schema ships in all four supported formats. They normalize to the
same model and produce the same profile:

```bash
schemascope examples/schema.json examples/data
schemascope examples/schema.yaml examples/data
schemascope examples/schema.xml  examples/data
schemascope examples/schema.txt  examples/data
```

### Getting YAML output instead of JSON

Add `-o yaml` (or `--output yaml`) to get the report as YAML:

```bash
schemascope examples/schema.json examples/data -o yaml
```

```yaml
entities:
- name: users
  source: users
  present: true
  row_count: 5
  fields:
  - name: id
    declared_type: integer
    column: id
    present: true
    row_count: 5
    null_count: 0
    null_fraction: 0.0
    distinct_count: 5
    inferred_type: integer
    type_ok: true
  # ... one block per field, same numbers as the JSON above
```

---

## How to write your own schema, step by step

A schema is just a small text file that lists your entities and their fields. You
can hand-write it in any of four formats. This section builds one up from the
smallest possible valid schema.

### Step 1: the smallest valid schema

The minimum schema is **one entity with one field**. In JSON:

```json
{
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id"}
      ]
    }
  ]
}
```

That is valid. A field does not even require a `type` — if you omit it, the
declared type normalizes to `unknown`, which is compatible with anything (so it
never triggers a mismatch). But you usually want to declare a type so drift is
caught.

### Step 2: add fields and pick a type

Add one object to `fields` per column, and give each a `type`. The seven
**canonical** type names are `string`, `integer`, `float`, `boolean`, `date`,
`datetime` (and `unknown`) — but a wide range of database spellings are accepted
as aliases too (`varchar(255)`, `int4`, `serial`, `timestamptz`, `jsonb`, …), so
you can usually paste your database's own type verbatim. The full list is in
[Type Names](#type-names); when in doubt, `string` is always safe.

```json
{
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id",    "type": "integer"},
        {"name": "email", "type": "string"},
        {"name": "age",   "type": "integer"}
      ]
    }
  ]
}
```

### Step 3: mark a primary key

Set `"primary_key": true` on the field that identifies each row. A primary key is
treated as **not nullable** unless you say otherwise. So this:

```json
{"name": "id", "type": "integer", "primary_key": true}
```

normalizes to an integer field with `nullable: false`.

### Step 4: mark nullable fields

Set `"nullable": true` on any field that is allowed to be empty. `age` is a
classic example — plenty of users never fill it in:

```json
{"name": "age", "type": "integer", "nullable": true}
```

Note: schemascope reports nulls no matter what; the `nullable` flag documents your
*intent*. It records what you meant so a human reading the schema knows an empty
`age` is expected and an empty `email` is not. (In this MVP the profiler does not
turn `nullable: false` into a `type_ok: false` failure by itself; it surfaces the
`null_count`/`null_fraction` so you can decide.)

### The same tiny schema in JSON and in the TXT DSL

For quick hand-authoring, the TXT DSL is the least fiddly format — no braces, no
quotes. This TXT file:

```text
entity users
  id: integer pk
  email: string
  age: integer null
```

is equivalent to this JSON file:

```json
{
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id",    "type": "integer", "primary_key": true},
        {"name": "email", "type": "string"},
        {"name": "age",   "type": "integer", "nullable": true}
      ]
    }
  ]
}
```

In the DSL, `pk` marks the primary key and `null` marks a nullable field. The
full DSL rules are in [Schema Formats](#schema-formats).

### `source` vs `name`

Every entity has a `name`. It may *also* have a `source`. The difference:

- `name` is what the entity is called in your schema and in the report.
- `source` is the **backing store** schemascope actually reads: the CSV file stem
  or the SQLite table name.

If you set no `source`, schemascope uses the `name`. So an entity named `users`
reads `users.csv` (or a SQLite table named `users`). But if your export produced
a file named `app_users.csv` while you want the entity called `users`, set a
`source`:

```json
{
  "name": "users",
  "source": "app_users",
  "fields": [ {"name": "id", "type": "integer", "primary_key": true} ]
}
```

Now the entity is reported as `users` but the data is read from `app_users.csv`
(or a SQLite table `app_users`). Note: the TXT DSL cannot express `source`; use
JSON, YAML, or XML if you need it.

---

## Run it on your own data (the bridge)

Here is the rule again, stated plainly, because it is the thing newcomers trip
over:

> **schemascope reads exactly two kinds of data source: a directory of CSV files,
> or a single SQLite file. Nothing else.** There is no database-connection
> option, no host/port/URL flag, no driver. If your data lives in Postgres,
> MySQL, SQL Server, Oracle, MongoDB, BigQuery, Snowflake, Redshift, or anywhere
> else, you must first get it out.

To profile data that lives in a real database, you do **two** things:

1. **Create a schema file.** Read the real table structure from your database
   (its DDL, its `information_schema`, or an introspection command) and translate
   each column's type into one of schemascope's seven canonical types. You write
   this file by hand (or generate it) in JSON/YAML/XML/TXT.

2. **Export the data** into a form schemascope can open:
   - one `<table>.csv` file per entity, each with a header row, all in one
     directory; **or**
   - a single SQLite `.db`/`.sqlite`/`.sqlite3` file with one table per entity.

   Exporting into a SQLite file is often the simplest bridge, because SQLite
   preserves real types and column names and schemascope can open the file
   directly with no further conversion.

Then run schemascope against the exported directory or SQLite file exactly as in
the walkthrough above.

[Appendix A](#appendix-a-generating-a-schemascope-schema-from-your-database)
gives copy-paste recipes for both halves — read the schema and export the data —
for every major platform. If you just want a quick reference for "what canonical
type should I write for this database type?", jump to
[Appendix B](#appendix-b-type-mapping-cheat-sheet).

---

## Command Line

```bash
schemascope SCHEMA DATA [--output json|yaml] [--schema-format json|yaml|xml|txt]
```

Arguments:

- `SCHEMA`: path to a JSON, YAML, XML, or TXT schema file.
- `DATA`: a directory of CSV files, **or** a SQLite database file
  (`.db`/`.sqlite`/`.sqlite3`). Nothing else.

Options:

- `-o, --output json|yaml`: choose report format. Defaults to `json`.
- `--schema-format json|yaml|xml|txt`: override schema format auto-detection.
- `--version`: print the package version.
- `--help`: print CLI help.

`python -m schemascope ...` works the same way as the `schemascope` console
script:

```bash
python -m schemascope examples/schema.json examples/data --output yaml
```

Exit codes:

- `0`: success.
- `2`: bad arguments, schema errors, or data-source errors. Schema errors are
  printed to stderr as `schema error: ...`; data-source errors as
  `data source error: ...`. (argparse also uses exit code `2` for its own
  usage errors, such as a missing argument.)

---

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

Validation rules (violating any of these is a schema error, exit code `2`):

- The schema must define **at least one entity**.
- Each entity must define **at least one field**.
- **Entity names must be unique.**
- **Field names must be unique within each entity.**
- Empty entity names and empty field names are rejected.

A primary key is treated as `nullable: false` unless `nullable` is stated
explicitly.

---

## Schema Formats

These schemas all describe the same common model.

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
parsing. The root element must be `<schema>`.

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
- Supported flags are `pk`, `primary_key`, `primary key` (mark a primary key);
  `null`, `nullable` (mark nullable); and `not null`, `notnull`, `required`
  (mark not-nullable).
- `unique` is accepted in the field text but is currently ignored.
- Indentation is cosmetic.
- Flags are case-insensitive and order-free.

TXT does **not** represent schema-level `name` or `version`, entity `source`, or
descriptions. For strict whole-model equality across JSON, YAML, XML, and TXT,
use only the subset of metadata the TXT DSL can express.

### Richer JSON/YAML/XML Metadata

JSON and YAML support this fuller shape:

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

---

## Type Names

Declared type names are normalized before profiling. Matching is
**case-insensitive**, ignores surrounding whitespace, and **strips any trailing
`(size[,scale])` / `(max)` parameter** — so `VARCHAR(255)`, `numeric(10, 2)`, and
`double precision` all resolve. A wide range of vendor/dialect spellings is
recognized, so in most cases you can paste your database's own type names verbatim.

Canonical type | Accepted aliases (case-insensitive; `(…)` parameters stripped)
--- | ---
`string` | `str`, `string`, `char`, `character`, `nchar`, `varchar`, `varchar2`, `nvarchar`, `character varying`, `text`, `ntext`, `tinytext`/`mediumtext`/`longtext`, `clob`, `citext`, `uuid`, `guid`, `uniqueidentifier`, `enum`, `set`, `json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `struct`, `map`, `bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image`, `time`, `interval`, `year`, `inet`, `cidr`, `geometry`/`geography`, `objectid`
`integer` | `int`, `integer`, `int2`/`int4`/`int8`, `int64`, `bigint`, `smallint`, `tinyint`, `mediumint`, `serial`/`bigserial`/`smallserial`, `long`, `varint`, `counter`
`float` | `float`, `float4`/`float8`/`float64`, `double`, `double precision`, `real`, `decimal`, `numeric`, `number`, `money`, `smallmoney`, `bignumeric`, `decimal128`
`boolean` | `bool`, `boolean`, `bit`
`date` | `date`
`datetime` | `datetime`, `datetime2`, `smalldatetime`, `timestamp`, `timestamptz`, `timestamp with`/`without time zone`, `datetimeoffset`, `timestamp_ntz`/`ltz`/`tz`
`unknown` | empty, missing, non-string, array *notation* (`int[]`), or any spelling not covered above

Exotic types (`json`, `jsonb`, `blob`, `bytea`, `geometry`, `array`, …) map to
`string` on purpose: exported to CSV/JSONL/SQLite their values arrive as
serialized/hex/base64 text and infer as `string`, so a declared `string` accepts
them. Only a spelling nothing above covers falls through to `unknown` — not a
crash, but you lose the drift check for that one field (a declared `unknown` is
compatible with any inferred type). The full per-database reference is [Appendix
B](#appendix-b-type-mapping-cheat-sheet).

A primary key is treated as not nullable unless `nullable` is explicitly set. For
example, `{"name": "id", "type": "int", "primary_key": true}` normalizes to an
integer field with `nullable: false`.

---

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
- Files are read as `utf-8-sig`, so a UTF-8 BOM (common in Excel/Windows exports)
  is handled automatically.
- **Duplicate header names are rejected** (a data-source error).
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

> **Important:** the `DATA` argument must be the **directory**, not a `.csv`
> file. Pointing schemascope at `data/users.csv` fails; point it at `data/`.

### SQLite Database

Pass a `.db`, `.sqlite`, or `.sqlite3` file:

```bash
schemascope schema.yaml warehouse.sqlite
```

Each entity maps to a table named by `entity.source` or `entity.name`. SQLite
values are read with their native Python types where SQLite provides them. A file
that is not actually a SQLite database fails cleanly as a data-source error.

### Column Matching

Fields are matched to source columns by name:

1. Exact column name match.
2. Case-insensitive fallback (so `Email` in the schema matches `email` in the
   data, and vice versa).
3. If no column matches, the field is reported with `present: false`.

Entity/table/file matching uses the entity source or name. Missing entities are
reported with `present: false` rather than silently dropped.

---

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

Missing entities and missing columns stay in the report with `present: false`
(and their numeric fields at `0`, `inferred_type: unknown`, `type_ok: true`).
That makes drift visible instead of dropping absent objects from the output.

---

## Type Inference

`schemascope` infers one type per field from observed non-null values.

Inference checks *every* non-null value for each field, in a single streaming
pass. A type is chosen only when **every** value matches that type, so a column
that drifts to a non-conforming value anywhere in the file — not just in the first
few rows — is reported as a mismatch. If no specific type matches, the inferred
type is `string`. If there are no non-null values at all, the inferred type is
`unknown`.

Inference order (most specific first):

1. `boolean`
2. `integer`
3. `float`
4. `date`
5. `datetime`
6. `string` fallback

Recognized values:

- **Boolean:** real booleans, or `true`, `false`, `1`, `0`, `yes`, `no`, `t`,
  `f`, `y`, `n` case-insensitively.
- **Integer:** real integers or ASCII integer strings such as `1`, `0`, `-12`,
  `+42`. Real booleans are not integers. Values with a decimal point (`3.0`) are
  not integers.
- **Float:** real integers/floats or strings that parse as finite floats. `nan`,
  `inf`, and `infinity` are rejected.
- **Date:** strict `YYYY-MM-DD` calendar dates.
- **Datetime:** `YYYY-MM-DD` followed by a space or `T` and an `HH:MM` or
  `HH:MM:SS` time. Fractional seconds and a trailing `Z` are accepted.

> Because inference is strict, watch out for values that look like a type but do
> not match the exact format. A timestamp with a numeric zone offset such as
> `2021-03-05 10:00:00+00` does **not** match the datetime pattern (only a
> trailing `Z` is stripped), so a column of such values infers as `string`. A
> time-of-day like `10:30:00` is not a recognized type and also infers as
> `string`. If you know a column will hold these, either declare it `string`, or
> reformat on export (see Appendix A).

Compatibility is intentionally lenient. `type_ok` is `true` when:

- Declared and inferred types are **equal**.
- Declared `string` accepts **any** inferred type.
- Declared `float` accepts inferred `integer`.
- Declared `integer` accepts inferred `boolean` (an all-0/1 column often infers
  as boolean but is still valid integer data — this is the `deleted` field in the
  walkthrough).
- `unknown` on either side is treated as compatible.

Everything else is a type mismatch (`type_ok: false`). Note the asymmetry: a
declared `boolean` whose data infers `integer` (values outside 0/1) *is* flagged.

---

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

The caller owns connector lifecycle — close connectors when finished, ideally in
a `try/finally`.

Also exported from the top-level package: `Schema`, `Entity`, `Field`,
`SchemaError`, `ConnectorError`, `normalize_type`, `infer_type`,
`type_compatible`, `detect_format`, `store_name`, `__version__`, and
`CANONICAL_TYPES`.

---

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
- An empty file is an error.

Use `--schema-format` when a file extension is misleading or absent:

```bash
schemascope schemafile data/ --schema-format yaml
```

---

## Limitations

- This is not a full data validation engine. It profiles presence, nulls,
  distinct counts, inferred types, and type compatibility. It does **not** flag a
  `nullable: false` field just because nulls appear — it reports the counts and
  leaves the judgment to you.
- It does not enforce foreign keys, uniqueness, ranges, regexes, or custom
  constraints.
- Type inference scans every non-null value in one pass (O(1) memory per field),
  so drift anywhere in the file is caught — at the cost of running the type
  predicates over the full column rather than a sample.
- `distinct_count` tracks all distinct non-null values for each profiled field,
  which is simple and exact but not approximate-memory analytics.
- TXT schemas do not support metadata such as schema name, version, source, or
  descriptions.
- The CLI exposes the default CSV null handling only. Use the Python API for
  custom CSV null tokens.
- It cannot connect to a live database. Export to CSV or SQLite first (see
  [Appendix A](#appendix-a-generating-a-schemascope-schema-from-your-database)).

---

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

---

## Troubleshooting

Almost every failure exits with code `2` and prints a one-line message to
**stderr**. The report itself (when the run succeeds) goes to **stdout**, so you
can redirect them separately. Here are the real failure modes and their fixes.

Symptom (stderr message) | What it means | Fix
--- | --- | ---
`data source error: cannot determine a connector for ... (expected a CSV directory or a .db/.sqlite/.sqlite3 file)` | You pointed `DATA` at something that is neither a directory nor a SQLite file — most commonly a single `.csv` file. | Point at the **directory** that contains your CSVs (`data/`, not `data/users.csv`), or rename/verify your SQLite file ends in `.db`/`.sqlite`/`.sqlite3`.
`data source error: CSV source is not a directory: ...` | (Python API only, via `CsvConnector`) you passed a file path where a directory was expected. | Pass the CSV **directory**.
`data source error: CSV file for 'users' has a duplicate column: 'id'` | A CSV header lists the same column name twice. schemascope refuses to guess which one you meant. | De-duplicate the header row in that CSV; each column name must be unique.
`data source error: cannot open SQLite database ...: file is not a database` | The `.db`/`.sqlite` file you passed is not actually a SQLite database (wrong file, corrupt, or a text file renamed). | Rebuild the SQLite file, or point at the correct one. Verify with `sqlite3 file.db ".tables"`.
`data source error: SQLite database not found: ...` | The SQLite path does not exist. | Check the path.
`schema error: schema is missing the 'entities' key` | Your JSON/YAML parsed fine but has no top-level `entities`. | Add an `entities:` list; every schema needs at least one entity.
`schema error: schema defines no entities` | The `entities` list is present but empty. | Add at least one entity with at least one field.
`schema error: <path>: empty schema file` | The schema file is empty (for a file whose format had to be sniffed). | Put a real schema in the file. Note: an **empty `.json`** file instead reports `invalid JSON schema: Expecting value...` because the `.json` extension forces the JSON parser.
`schema error: invalid JSON schema: ...` / `invalid YAML schema: ...` / `invalid XML schema: ...` | The file is malformed for its format. | Fix the syntax. If the *format* was auto-detected wrongly, force it with `--schema-format`.
`schema error: duplicate entity name: 'users'` | Two entities share a name. | Rename one; entity names must be unique.
`schema error: entity 'users': duplicate field name: 'id'` | Two fields in one entity share a name. | Rename one; field names must be unique within an entity.
Wrong format auto-detected (e.g. a DSL file read as YAML) | The file has no recognized extension and the content sniffer guessed wrong. | Pass `--schema-format json|yaml|xml|txt`, or give the file a recognized extension.

### Reading the report itself (not errors)

These are **not** crashes — they are the profile telling you something.

- **`"present": false` on an entity** — the backing CSV file or SQLite table was
  not found. Check the file name matches `<source>.csv` (or the table name), and
  that the file is in the directory you pointed at. This is drift, not an error;
  exit code is still `0`.
- **`"present": false` on a field** — no column matched that field name (after
  the case-insensitive fallback). The column may have been renamed or dropped in
  the export. Also drift, not an error.
- **A field's `declared_type` is `unknown`** — the `type` you wrote matched no
  recognized alias. Most vendor spellings *are* recognized (`jsonb`,
  `timestamptz`, `serial`, `int4`, `varchar(255)`, …); the usual culprits are
  array *notation* (`int[]`), a bespoke domain type, or a typo. It will not fail
  (an `unknown` declared type is compatible with anything), but you lose the
  drift check. Replace it with a canonical name (`string`, `integer`, `datetime`,
  ...); see [Appendix B](#appendix-b-type-mapping-cheat-sheet).
- **`"type_ok": false`** — the type inferred from the data is not compatible with
  the declared type. Example: you declared `age` as `integer` but a row contains
  `"unknown"`, so the whole column infers as `string`, and `integer` does not
  accept `string`. Either fix the data, or reconsider the declared type. This is
  the core drift signal.

---

## Appendix A: Generating a schemascope schema from your database

schemascope opens exactly two things: a **directory of CSV files** or a **single
SQLite file**. So for every database platform below, you do the same two steps:

1. **Get the schema.** Read the real table structure — from the platform's DDL,
   its `information_schema` catalog, or an introspection command — and translate
   each column type into one of schemascope's seven canonical types (`string`,
   `integer`, `float`, `boolean`, `date`, `datetime`, `unknown`). You then write
   a small JSON/YAML/XML/TXT schema by hand from that.

2. **Get the data out.** Export each table to a `<table>.csv` file **with a header
   row** (all CSVs in one directory), **or** load the tables into a single SQLite
   file. Exporting to a **SQLite file is often the simplest bridge**: it keeps
   real column names and types, and schemascope opens it directly with no
   conversion.

Both halves are required. A schema with no data, or data with no schema, leaves
you stuck.

### A universal starting point: `information_schema.columns`

Most SQL engines implement the ANSI `information_schema`. This query lists a
table's columns and types and works, with minor variation, on PostgreSQL, MySQL/
MariaDB, SQL Server, Snowflake, Redshift, BigQuery, CockroachDB, Databricks, and
others:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

Take each `data_type` it returns and map it to a canonical schemascope type using
the master table below (and the per-platform notes that follow).

### Master type-mapping table

Write the **canonical schemascope type** in the middle column into your schema.
The left column groups the database types you are likely to see.

Database column type (any platform) | Write this schemascope type | Notes
--- | --- | ---
`char`, `varchar`, `nvarchar`, `text`, `clob`, `string`, `character varying` | `string` | Plain text.
`uuid`, `guid`, `uniqueidentifier` | `string` | `uuid` is a recognized alias, but writing `string` is clearer. Exported as text it infers `string`.
`enum`, `set` | `string` | `enum` is a recognized alias; values export as text.
`json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `geometry`, `geography` | `string` | Recognized — all map to `string`. Exported as serialized text they also infer `string`, so `type_ok` holds. (Array *notation* like `int[]` is not covered → `unknown`.)
`bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image` | `string` | Binary. Exports as hex/base64 text -> infers `string`. (Consider excluding huge binary columns from the export.)
`smallint`, `int`, `integer`, `bigint`, `int2`, `int4`, `int8`, `serial`, `bigserial`, `tinyint`, `mediumint`, `long` | `integer` | All recognized, including `int2`/`int4`/`int8`, `serial`/`bigserial`, `tinyint`/`mediumint`. (Oracle `NUMBER(p,0)` resolves via `number` → `float`, not `integer` — the scale is stripped with the parameter, and float safely accepts integer data.)
`decimal`, `numeric`, `float`, `double`, `double precision`, `real`, `money`, `number(p,s)` | `float` | `money`/`number` may export with currency symbols or thousands separators; if so it infers `string` — strip formatting on export or declare `string`.
`boolean`, `bool`, `bit` | `boolean` | A single-bit or `tinyint(1)` flag column of 0/1 infers `boolean`; declaring `integer` also passes (integer accepts boolean).
`date` | `date` | Must export as `YYYY-MM-DD`.
`timestamp`, `datetime`, `datetime2`, `smalldatetime`, `timestamptz`, `timestamp with time zone` | `datetime` | Only `timestamp`/`datetime` are aliases. Export as `YYYY-MM-DD HH:MM:SS`. A trailing numeric zone offset (`+00`) makes it infer `string`; strip the zone or store UTC without offset.
`time`, `time with time zone`, `interval`, `year` | `string` | Recognized — all map to `string` (they export as text and infer `string`).

> Rule of thumb: if you are unsure, declare `string`. A declared `string` accepts
> any inferred type, so it never produces a false `type_ok: false`. Use the more
> specific types when you actually want drift detection on that column.

---

### PostgreSQL

**Read the schema.** In `psql`, `\d users` prints the column list and types.
For a machine-readable version, use the catalog query:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users' AND table_schema = 'public'
ORDER BY ordinal_position;
```

Or dump DDL only (no data) for all tables:

```bash
pg_dump --schema-only --no-owner mydb > schema.sql
```

**Type mapping.** `text/varchar/char` -> `string`; `uuid` -> `string`;
`smallint/integer/bigint/serial/bigserial/int2/int4/int8` -> `integer`; `numeric/decimal/real/double precision/money` -> `float`;
`boolean` -> `boolean`; `date` -> `date`; `timestamp`/`timestamptz` -> `datetime`
(export without a `+00` offset — see note above); `json`/`jsonb`/`bytea`/
`ARRAY`/`interval` -> `string`.

**Export the data to CSV** with `\copy` (runs client-side, no server file
permissions needed), one file per table:

```bash
psql -d mydb -c "\copy (SELECT * FROM users)  TO 'data/users.csv'  WITH (FORMAT csv, HEADER)"
psql -d mydb -c "\copy (SELECT * FROM orders) TO 'data/orders.csv' WITH (FORMAT csv, HEADER)"
```

Then run `schemascope schema.json data/`.

**Alternative: export into SQLite.** `pgloader` can copy a whole Postgres
database into a SQLite file in one command:

```bash
pgloader postgresql://user@localhost/mydb sqlite://./warehouse.sqlite
# then: schemascope schema.json warehouse.sqlite
```

---

### MySQL / MariaDB

**Read the schema.** `SHOW CREATE TABLE users;` prints the full DDL. Or use the
catalog:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'mydb' AND table_name = 'users'
ORDER BY ordinal_position;
```

Schema-only dump of all tables:

```bash
mysqldump --no-data mydb > schema.sql
```

**Type mapping.** `char/varchar/text` -> `string`; `tinyint/smallint/mediumint/
int/bigint` -> `integer`; `tinyint(1)` is MySQL's boolean and infers `boolean` (declare `boolean`
or `integer`); `decimal/float/double` -> `float`; `date` -> `date`;
`datetime/timestamp` -> `datetime`; `time`/`year` -> `string`; `json`/`blob`/
`enum` -> `string` (`enum` is a recognized alias, but the data exports as text
either way).

**Export the data to CSV.** The most portable way is the batch client, which
emits **tab-separated** output; convert tabs to commas. A common one-liner:

```bash
mysql --batch --raw -e "SELECT * FROM users" mydb \
  | sed 's/\t/,/g' > data/users.csv
```

`--batch` gives one header row plus tab-separated rows; the `sed` turns tabs into
commas. (This is fine when your text fields contain no commas or tabs; for messy
text with embedded delimiters, prefer a GUI export such as MySQL Workbench's
"Export a Result Set" wizard, which writes a proper quoted CSV.)

The server-side `SELECT ... INTO OUTFILE` writes a file **on the database
server** and is restricted by the `secure_file_priv` setting:

```sql
-- Check where the server is allowed to write:
SHOW VARIABLES LIKE 'secure_file_priv';

SELECT * FROM users
INTO OUTFILE '/var/lib/mysql-files/users.csv'
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n';
```

`INTO OUTFILE` does **not** write a header row, and the file lands on the server —
so `--batch` above is usually easier for schemascope. If you use `INTO OUTFILE`,
add the header line yourself.

---

### Microsoft SQL Server / Azure SQL

**Read the schema.** `EXEC sp_help 'dbo.users';` or the catalog:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

**Type mapping.** `char/varchar/nvarchar/text/ntext` -> `string`; `uniqueidentifier` -> `string`; `tinyint/
smallint/int/bigint` -> `integer`; `decimal/numeric/float/real/money/smallmoney`
-> `float`; `bit` -> `boolean`; `date` -> `date`; `datetime/datetime2/
smalldatetime/datetimeoffset` -> `datetime` (write `datetime`; `datetime2` is not
an alias); `time` -> `string`; `varbinary`/`image`/`xml` -> `string`.

**Export the data to CSV** with `sqlcmd` (which can emit a header) — `bcp` is
faster but does not easily produce headers:

```bash
sqlcmd -S server -d mydb -Q "SET NOCOUNT ON; SELECT * FROM dbo.users" \
  -s "," -W -o data/users.csv
```

`-s ","` sets the comma separator and `-W` trims trailing spaces. You will get a
dashed separator line under the header that you should delete. For large tables,
`bcp` is the workhorse (no header, so add one):

```bash
bcp "SELECT * FROM mydb.dbo.users" queryout data/users.csv -c -t, -S server -T
```

The SSMS **Import and Export Wizard** (right-click the database ->
Tasks -> Export Data) is the GUI route and lets you write a flat file with column
headers.

---

### Oracle Database

**Read the schema.** Query the data dictionary:

```sql
SELECT column_name, data_type, nullable
FROM user_tab_columns          -- or all_tab_columns for another schema
WHERE table_name = 'USERS'
ORDER BY column_id;
```

Full DDL for a table:

```sql
SELECT DBMS_METADATA.GET_DDL('TABLE', 'USERS') FROM dual;
```

**Type mapping.** `VARCHAR2`/`CHAR`/`NVARCHAR2`/`CLOB` -> `string`; `NUMBER(p,0)`
-> `integer`, `NUMBER(p,s)`/`FLOAT`/`BINARY_FLOAT`/`BINARY_DOUBLE` -> `float`;
`TIMESTAMP` -> `datetime`; `RAW`/`BLOB` -> `string`. Note that Oracle's `DATE`
actually carries a time component, so it commonly exports as a full timestamp —
declare it `datetime` (or `date` if you export just the date part). There is no
native boolean in table columns; a 0/1 `NUMBER(1)` flag infers `boolean`.

**Export the data to CSV with SQLcl** (the modern command-line client), which has
a built-in CSV format:

```sql
-- in sqlcl, connected to your DB:
SET SQLFORMAT csv
SPOOL data/users.csv
SELECT * FROM users;
SPOOL OFF
```

Or with **SQL\*Plus 12.2+**, which added CSV markup:

```sql
SET MARKUP CSV ON
SET HEADING ON
SET FEEDBACK OFF
SPOOL data/users.csv
SELECT * FROM users;
SPOOL OFF
```

Both write a header row by default. (Older SQL\*Plus without `MARKUP CSV` requires
concatenating columns by hand — prefer SQLcl.)

---

### SQLite

SQLite is the easy case: **schemascope opens a `.db`/`.sqlite`/`.sqlite3` file
directly, so you do not need to export data at all.**

**Read the schema.** In the `sqlite3` shell:

```bash
sqlite3 warehouse.sqlite ".schema users"
```

**Type mapping.** SQLite uses type *affinities*: `INTEGER` -> `integer`;
`REAL`/`FLOAT`/`DOUBLE` -> `float`; `TEXT`/`VARCHAR`/`CHAR` -> `string`;
`NUMERIC`/`DECIMAL` -> `float`; `BLOB` -> `string`; `DATE`/`DATETIME` are stored
as text or numbers, so declare `date`/`datetime` and confirm the stored format is
`YYYY-MM-DD`(`T`/space time). SQLite has no dedicated boolean; 0/1 columns infer
`boolean`.

**Run it directly:**

```bash
schemascope schema.json warehouse.sqlite
```

If you still want CSVs (for example, to hand to a different tool), the shell can
produce them:

```bash
sqlite3 warehouse.sqlite <<'EOF'
.headers on
.mode csv
.output data/users.csv
SELECT * FROM users;
.output data/orders.csv
SELECT * FROM orders;
EOF
```

---

### IBM Db2

**Read the schema.** Query the catalog:

```sql
SELECT colname, typename, nulls
FROM syscat.columns
WHERE tabname = 'USERS'
ORDER BY colno;
```

Or capture DDL with the `db2look` tool:

```bash
db2look -d MYDB -e -t USERS > schema.sql
```

**Type mapping.** `CHAR/VARCHAR/CLOB/GRAPHIC` -> `string`; `SMALLINT/INTEGER/
BIGINT` -> `integer`; `DECIMAL/DECFLOAT/REAL/DOUBLE` -> `float`; `BOOLEAN` ->
`boolean`; `DATE` -> `date`; `TIMESTAMP` -> `datetime`; `TIME` -> `string`;
`BLOB`/`XML` -> `string`.

**Export the data to CSV** with the `EXPORT` command (delimited format):

```sql
db2 "EXPORT TO data/users.csv OF DEL MODIFIED BY NOCHARDEL SELECT * FROM users"
```

`OF DEL` produces comma-delimited output. **Db2 `EXPORT` does not write a header
row**, so add one yourself (for example, `sed -i '1i id,email,age,active,deleted,signup_date' data/users.csv`
with your real column names), or list columns explicitly in the `SELECT` so you
know the order.

---

### CockroachDB

CockroachDB speaks the PostgreSQL wire protocol, so its introspection is
Postgres-compatible.

**Read the schema.** `SHOW CREATE TABLE users;` prints the DDL, or use
`information_schema.columns` as in the [universal
query](#a-universal-starting-point-information_schemacolumns). Type mapping is the
same as [PostgreSQL](#postgresql).

**Export the data to CSV.** The simplest client-side route is `COPY ... TO
STDOUT` via the `cockroach sql` shell, redirected to a file:

```bash
cockroach sql --url "$CONN" \
  -e "COPY (SELECT * FROM users) TO STDOUT WITH CSV HEADER" > data/users.csv
```

For large tables, `EXPORT` writes CSV files to cloud/nodelocal storage in
parallel:

```sql
EXPORT INTO CSV 'nodelocal://1/users' WITH nullas = '' FROM TABLE users;
```

(Then collect the files from the storage location. For schemascope, the `COPY ...
TO STDOUT` form is usually simplest because it gives you one local CSV with a
header.)

---

### Google BigQuery

**Read the schema.** Print a table's schema with the `bq` CLI:

```bash
bq show --schema --format=prettyjson mydataset.users
```

Or query the catalog:

```sql
SELECT column_name, data_type, is_nullable
FROM `myproject.mydataset.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'users';
```

**Type mapping.** `STRING` -> `string`; `INT64`/`INTEGER` -> `integer`;
`NUMERIC`/`BIGNUMERIC`/`FLOAT64` -> `float`; `BOOL` -> `boolean`; `DATE` ->
`date`; `DATETIME`/`TIMESTAMP` -> `datetime` (export in `YYYY-MM-DD HH:MM:SS`
form); `TIME` -> `string`; `BYTES`/`JSON`/`GEOGRAPHY`/`ARRAY`/`STRUCT` ->
`string`.

**Export the data to CSV.** `EXPORT DATA` writes CSV directly to Cloud Storage
with a header:

```sql
EXPORT DATA OPTIONS(
  uri = 'gs://my-bucket/users-*.csv',
  format = 'CSV',
  overwrite = true,
  header = true
) AS SELECT * FROM mydataset.users;
```

Or the `bq extract` CLI (header on by default via `--print_header`):

```bash
bq extract --destination_format=CSV --print_header=true \
  mydataset.users gs://my-bucket/users.csv
gsutil cp gs://my-bucket/users.csv data/users.csv
```

For small results you can skip GCS entirely:

```bash
bq query --use_legacy_sql=false --format=csv \
  'SELECT * FROM mydataset.users' > data/users.csv
```

---

### Snowflake

**Read the schema.** `DESCRIBE TABLE users;` lists columns and types, or:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'USERS';
```

**Type mapping.** `VARCHAR/STRING/TEXT/CHAR` -> `string`; `NUMBER(p,0)`/`INT`/
`INTEGER`/`BIGINT` -> `integer`, `NUMBER(p,s)`/`FLOAT`/`DOUBLE`/`REAL` -> `float`;
`BOOLEAN` -> `boolean`; `DATE` -> `date`; `DATETIME`/`TIMESTAMP_NTZ`/`TIMESTAMP_
LTZ`/`TIMESTAMP_TZ` -> `datetime` (strip the zone offset if present); `TIME` ->
`string`; `VARIANT`/`OBJECT`/`ARRAY`/`BINARY`/`GEOGRAPHY` -> `string`.

**Export the data to CSV.** The simplest CLI route uses `snowsql` output options:

```bash
snowsql -o output_format=csv -o header=true -o friendly=false -o timing=false \
  -q "SELECT * FROM users" -o output_file=data/users.csv
```

The scalable route unloads to a stage with `COPY INTO`, then downloads with `GET`:

```sql
COPY INTO @~/users
FROM users
FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '"' COMPRESSION = NONE)
HEADER = TRUE
OVERWRITE = TRUE
SINGLE = TRUE;
```

```bash
snowsql -q "GET @~/users file://./data/"
```

(`HEADER = TRUE` writes the column names as the first row.)

---

### Amazon Redshift

**Read the schema.** Redshift exposes `SVV_COLUMNS` and the legacy `PG_TABLE_DEF`
(remember to set `search_path`):

```sql
SELECT column_name, data_type, is_nullable
FROM svv_columns
WHERE table_name = 'users';
```

**Type mapping.** `CHAR/VARCHAR/TEXT` -> `string`; `SMALLINT/INT2/INTEGER/INT4/
BIGINT/INT8` -> `integer`; `DECIMAL/NUMERIC/REAL/FLOAT4/DOUBLE
PRECISION/FLOAT8` -> `float`; `BOOLEAN` -> `boolean`; `DATE` -> `date`;
`TIMESTAMP`/`TIMESTAMPTZ` -> `datetime` (strip the zone); `TIME`/`TIMETZ`/
`SUPER`/`VARBYTE` -> `string`.

**Export the data to CSV** with `UNLOAD` (writes to S3, with a header):

```sql
UNLOAD ('SELECT * FROM users')
TO 's3://my-bucket/users/'
IAM_ROLE 'arn:aws:iam::123456789012:role/MyRedshiftRole'
FORMAT AS CSV
HEADER
PARALLEL OFF
ALLOWOVERWRITE;
```

`PARALLEL OFF` makes a single file; `HEADER` adds the column names. Then copy the
object down from S3 and rename it to `data/users.csv`:

```bash
aws s3 cp s3://my-bucket/users/000 data/users.csv
```

---

### Databricks / Spark SQL

**Read the schema.** `DESCRIBE TABLE users;` (or `DESCRIBE TABLE EXTENDED users`)
lists columns and types; `information_schema.columns` is available in Unity
Catalog.

**Type mapping.** `STRING` -> `string`; `TINYINT/SMALLINT/INT/BIGINT` ->
`integer`; `FLOAT/DOUBLE/DECIMAL` -> `float`; `BOOLEAN` -> `boolean`; `DATE` ->
`date`; `TIMESTAMP`/`TIMESTAMP_NTZ` -> `datetime`; `BINARY`/`ARRAY`/`MAP`/
`STRUCT` -> `string`.

**Export the data to CSV** with the DataFrame writer (write a single file with a
header):

```python
(spark.table("users")
      .coalesce(1)                       # one output file
      .write.option("header", "true")
      .mode("overwrite")
      .csv("/tmp/users_csv"))
```

Spark writes a *directory* of part files; grab the single `part-*.csv` inside and
rename it to `data/users.csv`. (On Databricks you can then `dbutils.fs.cp` it to
where you need it, or download via the workspace.)

---

### MongoDB

MongoDB is **schemaless** — documents in a collection need not share the same
fields or types. So *you* decide which fields to profile, then sample the data to
learn their real types.

**Discover fields and types.** In `mongosh`, sample documents:

```javascript
db.orders.aggregate([{ $sample: { size: 100 } }])
```

MongoDB Compass has a built-in **Schema** tab that analyzes a collection and
reports each field's observed types and how often they appear. The community
`variety.js` script does the same from the shell. Use whichever to pick your
fields and their dominant types.

**Type mapping.** BSON `String` -> `string`; `Int32`/`Int64`/`Long` -> `integer`;
`Double`/`Decimal128` -> `float`; `Boolean` -> `boolean`; `Date` -> `datetime`
(Mongo dates carry a time; `mongoexport` writes ISO-8601 like
`2021-03-05T10:00:00.000Z` — the trailing `Z` is fine for schemascope's datetime
inference); `ObjectId`/`UUID` -> `string`; embedded documents/arrays -> `string`.

**Export the data to CSV** with `mongoexport`, which **requires** you to list the
fields for CSV:

```bash
mongoexport --uri "mongodb://localhost:27017" --db mydb --collection orders \
  --type=csv --fields "orderId,customerId,status,total,createdAt" \
  --out data/orders.csv
```

By default the listed field names become the header row (use `--noHeaderLine` to
omit it — but schemascope needs the header, so keep it). Nested fields use dot
notation, e.g. `--fields "orderId,customer.name"`.

---

### Cassandra / ScyllaDB

**Read the schema.** In `cqlsh`, `DESCRIBE TABLE users;` prints the DDL, or query
the catalog:

```sql
SELECT column_name, type FROM system_schema.columns
WHERE keyspace_name = 'myks' AND table_name = 'users';
```

**Type mapping.** `text/varchar/ascii` -> `string`; `tinyint/smallint/int/
bigint/varint/counter` -> `integer`; `decimal/float/double` -> `float`;
`boolean` -> `boolean`; `date` -> `date`; `timestamp` -> `datetime`; `time` ->
`string`; `uuid`/`timeuuid`/`inet` -> `string`; `blob`/`list`/`set`/`map` ->
`string`.

**Export the data to CSV** with `cqlsh COPY ... TO`, which supports a header:

```sql
COPY myks.users TO 'data/users.csv' WITH HEADER = TRUE;
```

You can restrict/order columns: `COPY myks.users (id, email, age) TO
'data/users.csv' WITH HEADER = TRUE;`. (`COPY` is fine for moderate tables; for
very large ones use a bulk unloader such as DSBulk.)

---

### Amazon DynamoDB

DynamoDB is **schemaless** apart from its key schema. `describe-table` tells you
only the partition/sort keys and their types — not the other attributes — so you
must sample items to learn the rest.

**Read the (key) schema and sample attributes:**

```bash
aws dynamodb describe-table --table-name Orders \
  --query "Table.{Keys:KeySchema, Attrs:AttributeDefinitions}"

# sample some items to see the other attributes:
aws dynamodb scan --table-name Orders --max-items 25
```

**Type mapping.** DynamoDB attribute types: `S` (string) -> `string`; `N`
(number) -> `integer` or `float` depending on the values; `BOOL` -> `boolean`;
`B` (binary) -> `string`; `M`/`L` (map/list) -> `string`; `SS`/`NS`/`BS` (sets)
-> `string`. Because attributes are per-item, pick the fields you care about and
declare them from what the sample shows.

**Export the data.** The native **export to S3** (point-in-time) writes DynamoDB
JSON / Ion / Parquet — **not CSV** — so it is not directly usable by schemascope
without a conversion step:

```bash
aws dynamodb export-table-to-point-in-time \
  --table-arn arn:aws:dynamodb:us-east-1:123456789012:table/Orders \
  --s3-bucket my-bucket --s3-prefix orders/ --export-format DYNAMODB_JSON
```

For a small/medium table, the pragmatic route to CSV is to `scan` and flatten
with `jq`:

```bash
# header row:
echo "orderId,customerId,status,total" > data/orders.csv
# rows (adjust the attribute names to your table):
aws dynamodb scan --table-name Orders --output json \
  | jq -r '.Items[] | [.orderId.S, .customerId.S, .status.S, .total.N] | @csv' \
  >> data/orders.csv
```

(For large tables use AWS Glue or a proper export-then-transform pipeline; the
`scan` + `jq` approach is best for modest volumes.)

---

### Elasticsearch

Elasticsearch is document-oriented; each index has a **mapping** that plays the
role of a schema.

**Read the mapping:**

```bash
curl -s "http://localhost:9200/orders/_mapping?pretty"
```

**Type mapping.** `text`/`keyword` -> `string`; `integer`/`long`/`short`/`byte`
-> `integer`; `float`/`double`/`half_float`/`scaled_float` -> `float`;
`boolean` -> `boolean`; `date` -> `datetime` (Elasticsearch dates are usually
full timestamps); `ip`/`geo_point`/`object`/`nested` -> `string`.

**Export the data to CSV** with the community `elasticdump` tool (or a Logstash
`csv` output):

```bash
elasticdump --input=http://localhost:9200/orders \
  --output=data/orders.csv --type=data --csvConfigs='{"headers":true}'
```

(Keep this one simple: `elasticdump` and Logstash both flatten documents to CSV;
you choose which fields become columns.)

---

### Schema from code and tooling

Sometimes the truest schema lives in your application, not the database. You can
read the field types there and translate them the same way. You still export the
*data* to CSV/SQLite as above — these only give you the schema half.

- **Django** — `python manage.py inspectdb > models.py` reverse-engineers models
  from an existing database; or read your existing model fields.
  `CharField/TextField/UUIDField/SlugField/EmailField` -> `string`;
  `IntegerField/BigIntegerField/SmallIntegerField/AutoField` -> `integer`;
  `FloatField/DecimalField` -> `float`; `BooleanField` -> `boolean`;
  `DateField` -> `date`; `DateTimeField` -> `datetime`; `JSONField`/`BinaryField`
  -> `string`.
- **SQLAlchemy** — reflect an existing table (`Table('users', metadata,
  autoload_with=engine)`) or read your models. `String/Text/Unicode` -> `string`;
  `Integer/BigInteger/SmallInteger` -> `integer`; `Float/Numeric` -> `float`;
  `Boolean` -> `boolean`; `Date` -> `date`; `DateTime` -> `datetime`; `JSON`/
  `LargeBinary` -> `string`.
- **Ruby on Rails** — `db/schema.rb` lists every column. `t.string/t.text` ->
  `string`; `t.integer/t.bigint` -> `integer`; `t.float/t.decimal` -> `float`;
  `t.boolean` -> `boolean`; `t.date` -> `date`; `t.datetime/t.timestamp` ->
  `datetime`; `t.json/t.jsonb/t.binary` -> `string`.
- **Prisma** — `schema.prisma` model fields. `String` -> `string`; `Int/BigInt`
  -> `integer`; `Float/Decimal` -> `float`; `Boolean` -> `boolean`;
  `DateTime` -> `datetime`; `Json/Bytes` -> `string`. (Prisma has no bare `date`
  type; a date-only column is still `DateTime`.)
- **dbt** — column types live in each model's `schema.yml` (and, if you run
  `dbt docs generate`, in `target/catalog.json`, which carries the warehouse's
  real types). Map those warehouse types with the platform tables above.

### Schema from flat files

If your data is already in files, you can infer both the schema and get CSV in
one place.

- **CSV** — `csvkit`'s `csvstat data/users.csv` reports each column's inferred
  type, null count, and distinct count (a nice cross-check against schemascope).
  Its guesses map cleanly: Number -> `integer`/`float`, Boolean -> `boolean`,
  Date -> `date`, DateTime -> `datetime`, Text -> `string`. Or in pandas,
  `pandas.read_csv('users.csv').dtypes`: `int64` -> `integer`, `float64` ->
  `float`, `bool` -> `boolean`, `datetime64` -> `datetime`, `object` ->
  `string`.
- **Parquet / Arrow** — `pyarrow.parquet.read_schema('users.parquet')` prints the
  column types. `string/large_string` -> `string`; `int8/16/32/64` -> `integer`;
  `float/double/decimal` -> `float`; `bool` -> `boolean`; `date32/date64` ->
  `date`; `timestamp` -> `datetime`; `binary`/`list`/`struct` -> `string`. Then
  convert to CSV: `pyarrow.csv.write_csv(pyarrow.parquet.read_table('users.parquet'),
  'data/users.csv')` (it writes a header row).
- **JSON** — inspect the object keys to choose fields, and map each value's JSON
  type: string -> `string`; whole-number -> `integer`; fractional number ->
  `float`; `true`/`false` -> `boolean`; date-looking strings -> `date`/`datetime`
  if they match the strict formats, else `string`. Flatten to CSV with `jq -r`
  (see the DynamoDB example) or pandas `json_normalize`.

---

### Worked end-to-end example: from a PostgreSQL `users` table to a schemascope report

Suppose you have this table in PostgreSQL:

```sql
CREATE TABLE users (
    id          bigserial PRIMARY KEY,
    email       varchar(255) NOT NULL,
    age         integer,
    active      boolean NOT NULL,
    deleted     integer NOT NULL DEFAULT 0,
    signup_date date NOT NULL
);
```

**Step 1 — read the structure.** In `psql`:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users' AND table_schema = 'public'
ORDER BY ordinal_position;
```

which returns:

```text
 column_name | data_type         | is_nullable
-------------+-------------------+-------------
 id          | bigint            | NO
 email       | character varying | NO
 age         | integer           | YES
 active      | boolean           | NO
 deleted     | integer           | NO
 signup_date | date              | NO
```

**Step 2 — translate to canonical types and hand-write the schema.** Using the
[master table](#master-type-mapping-table): `bigint` -> `integer`,
`character varying` -> `string`, `integer` -> `integer`, `boolean` -> `boolean`,
`date` -> `date`. The primary key becomes `primary_key: true` (not nullable);
`age` is nullable. Save this as `schema.json`:

```json
{
  "name": "customer_exports",
  "version": "2026-07",
  "entities": [
    {
      "name": "users",
      "fields": [
        {"name": "id", "type": "integer", "primary_key": true},
        {"name": "email", "type": "string"},
        {"name": "age", "type": "integer", "nullable": true},
        {"name": "active", "type": "boolean"},
        {"name": "deleted", "type": "integer", "nullable": false},
        {"name": "signup_date", "type": "date"}
      ]
    }
  ]
}
```

(This is exactly the shape of the bundled `examples/schema.json`.)

**Step 3 — export the data.** One CSV, header row, into a `data/` directory:

```bash
mkdir -p data
psql -d mydb -c "\copy (SELECT * FROM users) TO 'data/users.csv' WITH (FORMAT csv, HEADER)"
```

Your `data/users.csv` now looks like:

```text
id,email,age,active,deleted,signup_date
1,alice@example.com,31,t,0,2021-03-05
...
```

> Note: Postgres exports booleans as `t`/`f`, which schemascope recognizes as
> boolean tokens — so `active` still infers `boolean`.

**Alternative Step 3 — export into SQLite instead.** If you would rather bridge
through SQLite, load the same rows into a file (for example with `pgloader
postgresql://user@localhost/mydb sqlite://./warehouse.sqlite`, or by piping the
CSV into `sqlite3`), and point schemascope at `warehouse.sqlite`.

**Step 4 — run schemascope:**

```bash
schemascope schema.json data
```

**Step 5 — read the result.** You get the same report structure as the
[walkthrough](#your-first-run-a-guided-walkthrough): `users` is `present: true`,
each field is `present: true`, `age` shows whatever `null_fraction` your real data
has, `deleted` infers `boolean` but `type_ok` stays `true` (integer accepts
boolean), and every other field's `type_ok` is `true` if the data matches. Any
`present: false` or `type_ok: false` in that output is drift worth investigating.

---

## Appendix B: Type-mapping cheat sheet

A consolidated reference: given a database column type, the schemascope type in the
right-hand column is what it normalizes to. **All the spellings below are recognized
aliases** — including parameterized (`varchar(255)`) and multi-word (`double
precision`) forms — so you can usually paste your database's own type verbatim.
Full rules are in [Type Names](#type-names); only a spelling that appears **nowhere
below** falls through to `unknown`.

Canonical schemascope type | Database types that map to it
--- | ---
`string` | `char`, `varchar`, `nvarchar`, `text`, `clob`, `character varying`, `uuid`, `guid`, `uniqueidentifier`, `enum`, `set`, `json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `struct`, `map`, `bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image`, `time`, `interval`, `year`, `inet`, `geometry`/`geography`, `ip`, `ObjectId`
`integer` | `int`, `integer`, `bigint`, `smallint`, `tinyint`, `mediumint`, `int2`/`int4`/`int8`, `serial`/`bigserial`, `long`, `int64`, `varint`, `counter`
`float` | `float`, `double`, `double precision`, `real`, `decimal`, `numeric`, `number` (any `NUMBER(p,s)` **or** `NUMBER(p,0)` — the scale is stripped, so Oracle integers land here too; harmless, since float accepts integer data), `money`, `smallmoney`, `float4`/`float8`, `float64`, `decimal128`, `BIGNUMERIC`
`boolean` | `boolean`, `bool`, `bit`, `tinyint(1)` (a 0/1 flag)
`date` | `date`
`datetime` | `datetime`, `datetime2`, `smalldatetime`, `timestamp`, `timestamptz`, `timestamp with/without time zone`, `TIMESTAMP_NTZ/LTZ/TZ`, `datetimeoffset` (strip any zone offset so it exports as `YYYY-MM-DD HH:MM:SS`)
`unknown` | Only a spelling that appears nowhere above (e.g. array *notation* `int[]`, a bespoke domain type, or a genuine typo), a non-string, or an empty/missing type. A declared `unknown` is compatible with any inferred type, so its `type_ok` is always `true` — you simply get no drift check on that field.

Reminders that catch people out:

- Vendor spellings **are** recognized now — `json`, `jsonb`, `blob`, `bytea`,
  `array`, `money`, `interval`, `time`, `year`, `serial`, `int4`, `nvarchar`,
  `datetime2`, `timestamptz`, `varchar(255)`, `double precision`, and the rest of
  the table above all resolve. You generally don't need to hand-translate types.
- A native UUID column is fine once exported: it comes out as text, infers
  `string`, and a declared `string` (or `uuid`) accepts it.
- When unsure, declare `string` — it accepts any inferred type, so it never
  produces a false mismatch; use specific types where you want real drift
  detection.

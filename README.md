# schemascope

**schemascope profiles a database against a schema you already have, so you can catch schema drift before it bites you.**

In plainer words: you give it the *shape* your data is supposed to have, and a *database* to look at, and it tells you — per table and per column — what actually showed up. Is every expected table and column present? Where are the nulls? How many distinct values? What type do the real values look like, and does that still match the type you declared? That is the whole job. schemascope is a **read-only profiler and drift detector**: it does **not** modify data, create tables, enforce constraints, or validate every row against a rich rule language. It only ever **reads**.

To do that, schemascope needs two things from you: a **database**, and a **schema**.

This manual is written to be read top to bottom with nobody to ask; every command can be copied and pasted as-is. If a term is unfamiliar (entity, field, drift, inferred type), the [Concepts and glossary](#concepts-and-glossary) section defines each one in a sentence — but you can also just start reading and pick them up as you go.

---

## Table of Contents

- [The two things schemascope needs](#the-two-things-schemascope-needs)
- [Install](#install)
- [Getting started — profile your database](#getting-started--profile-your-database)
- [The core idea](#the-core-idea)
- [Step 1 — Give it your schema](#step-1--give-it-your-schema)
- [Step 2 — Point it at your database](#step-2--point-it-at-your-database)
- [Concepts and glossary](#concepts-and-glossary)
- [Schema Model](#schema-model)
- [Schema Formats](#schema-formats)
- [Type Names](#type-names)
- [Data Sources](#data-sources)
- [Output Reference](#output-reference)
- [Type Inference](#type-inference)
- [Python API](#python-api)
- [Format Detection](#format-detection)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Requirements](#requirements)
- [Development](#development)
- [License](#license)
- [Appendix A: Reading your schema and connecting, engine by engine](#appendix-a-reading-your-schema-and-connecting-engine-by-engine)
- [Appendix B: Type-mapping cheat sheet](#appendix-b-type-mapping-cheat-sheet)

---

## The two things schemascope needs

schemascope only ever looks at two inputs. Get these two things right and everything else follows.

### 1. Your database — the rows

This is the actual content: the customers, the orders, the users. A tiny example, five rows of a `users` table:

| id | email | age | active | deleted | signup_date |
| --- | --- | --- | --- | --- | --- |
| 1 | alice@example.com | 31 | true | 0 | 2021-03-05 |
| 2 | bob@example.com | | false | 0 | 2021-07-19 |
| 3 | carol@example.com | 27 | true | 1 | 2022-01-02 |
| 4 | dave@example.com | 44 | true | 0 | 2022-11-30 |
| 5 | erin@example.com | | false | 0 | 2023-05-14 |

> **The single most important fact in this manual:** schemascope reads two kinds of data source — a **live database** reached through a **SQLAlchemy URL** (PostgreSQL, MySQL/MariaDB, SQL Server/Azure/Fabric, Oracle, Snowflake, BigQuery, and any other engine SQLAlchemy speaks), or a single local **SQLite file** (`.db`, `.sqlite`, `.sqlite3`). Running it straight against your database — `schemascope schema.json "postgresql+psycopg://user@host/db"` — is the main way to use it; you install the small driver for your engine (see [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine)) and pass the URL. schemascope connects, reads, and reports — it **never writes**. You always give it a **schema** too; the easiest way to get one is straight out of the same database (also in Appendix A).

### 2. A schema — the shape, not the rows

A schema is a small plain-text file that describes what your data is *supposed* to look like: which entities (tables) exist, which fields (columns) each has, and what type each field should be. It is **not** SQL. schemascope reads schemas written as **JSON, YAML, XML, or a small TXT DSL** — never a `CREATE TABLE` statement.

Here is a schema for the table above. In JSON:

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

The exact same shape in the TXT DSL, which is the least fiddly to hand-write (no braces, no quotes):

```text
entity users
  id: integer pk
  email: string
  age: integer null
  active: boolean
  deleted: integer not null
  signup_date: date
```

That file — in any of the four formats — **is** the schema. You hand it to schemascope directly.

### Where does the schema come from?

You are in one of three situations. Find yours.

| Your situation | What you do |
| --- | --- |
| **Your data is in a database** | The database already knows its own structure. Read it (its DDL, its `information_schema`, or an introspection command) and translate the column types into a small schemascope schema. [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine) has the exact commands per engine. |
| **Someone handed you a schema file** (`.json` / `.yaml` / `.xml` / `.txt`) | You are ready. Go to [Install](#install). |
| **You have only a database and no schema** | Write one by hand — it is tiny (one entity, a few fields). schemascope does **not** infer a schema from your data, so this step is yours. See [Step 1 — Give it your schema](#step-1--give-it-your-schema). |

> **Short version:** if your data lives in a real database, you do two things — read its structure into a small schemascope schema, and point schemascope at the live database with its SQLAlchemy URL. Both halves are spelled out per platform in [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine).

---

## Install

**First, check your Python version.** schemascope needs **Python 3.8 or newer** (this is the floor declared in `pyproject.toml`):

```bash
python3 --version
```

If that prints `Python 3.8.x` or higher, you are good. Then install:

```bash
pip install schemascope
```

Confirm it landed on your `PATH`:

```bash
schemascope --version
```

You should see a version like `schemascope 0.2.0` (0.2.0 or later). If instead you get "command not found", jump to [Troubleshooting](#troubleshooting) — the usual fix is to run it as `python -m schemascope` instead.

**PyYAML** and **SQLAlchemy** are installed automatically with the package. The only thing you may add is a **database driver**, and only for the engine you connect to — install it as an extra, e.g. `pip install "schemascope[postgres]"`. SQLite needs no driver at all.

To work on schemascope from a **source checkout** instead:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

(A current pip is recommended — older versions may not support editable installs for `pyproject.toml` projects.)

> **Note:** everywhere this manual writes `schemascope ...`, you can equally write `python -m schemascope ...`. They are the same program; use whichever is convenient. The `python -m` form is handy when the console script is not on your `PATH`.

---

## Getting started — profile your database

Three steps: install schemascope with the driver for your engine, describe your data with a schema, then point schemascope at your database.

### 1. Install schemascope and your database driver

```bash
pip install schemascope
```

schemascope reaches your database through SQLAlchemy, so add the driver for your engine (SQLite needs none):

```bash
pip install "schemascope[postgres]"    # or [mysql], [mssql], [oracle], [snowflake], [bigquery], …
```

The full engine list — with the URL prefix for each — is in [Point it at your database → SQL database](#sql-database-any-sqlalchemy-url).

### 2. Give it a schema

A **schema** is a small file that lists the tables you expect (schemascope calls each an *entity*), the columns in each (its *fields*), and the type every column should hold. You normally **generate it from your database's own structure** rather than writing it by hand — [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine) has the exact command for your engine. However you obtain it, a schema for a `users` table reads like this (JSON; YAML, XML, and a terse TXT DSL are also supported):

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
        {"name": "signup_date", "type": "date"}
      ]
    }
  ]
}
```

Save it as, e.g., `users-schema.json`. Each entity's `name` is the table schemascope looks for; if your table is named differently, set a `source` (see [`source` vs `name`](#source-vs-name)).

### 3. Point schemascope at your database

Pass the schema file and your database's SQLAlchemy URL:

```bash
schemascope users-schema.json "postgresql+psycopg://user:pw@host:5432/app"
```

Swap in the URL for your engine — `mysql+pymysql://…`, `mssql+pyodbc://…?driver=ODBC+Driver+18+for+SQL+Server`, `oracle+oracledb://…`, `snowflake://…`, and so on ([full list](#sql-database-any-sqlalchemy-url)). schemascope connects, finds each table your schema names, scans every row, and prints a report to standard output (add `-o yaml` for YAML). It **only reads** — it never changes your database.

### 4. Read the report

The report is one block per entity, each holding one block per field. Three things tell you whether the database still matches your schema:

- **`present`** — `false` on an entity means that table is missing; `false` on a field means that column is missing. schemascope keeps missing things in the report rather than dropping them, so drift stays visible.
- **`type_ok`** — `false` means the column's values no longer look like the type you declared (for example, a column declared `integer` now holds text). This is the core drift signal.
- **`null_count` / `null_fraction` / `distinct_count`** — how many values were null, what share of the rows that is, and how many distinct non-null values the column holds.

A single field in the report looks like this:

```json
{
  "name": "age",
  "declared_type": "integer",
  "column": "age",
  "present": true,
  "row_count": 4820,
  "null_count": 613,
  "null_fraction": 0.127178,
  "distinct_count": 88,
  "inferred_type": "integer",
  "type_ok": true
}
```

Here schemascope scanned 4,820 rows of `age`, found 613 nulls (~12.7%) and 88 distinct values, and every non-null value looked like an integer — matching the declared type, so `type_ok` is `true`. If someone later loads the word `"unknown"` into that column, `inferred_type` flips to `string` and `type_ok` becomes `false`: that is drift. Every field's full meaning is in [Output Reference](#output-reference); the type rules are in [Type Inference](#type-inference).

Everything below is detail and reference.

---

## The core idea

Hold on to one mental model:

> **schema + database → report.** You supply the schema (what the data *should* look like) and the database (what it *actually* looks like). schemascope compares them and prints a report. There is one command, no subcommands, no state left behind on disk, and nothing written back to your database.

The command is always:

```bash
schemascope SCHEMA DATA [--output json|yaml] [--schema-format json|yaml|xml|txt]
```

### Arguments and options at a glance

| Token | Kind | Meaning | Default |
| --- | --- | --- | --- |
| `SCHEMA` | argument (required) | Path to a JSON / YAML / XML / TXT schema file. | — |
| `DATA` | argument (required) | A **SQLAlchemy database URL** (`postgresql+psycopg://…`, `mysql+pymysql://…`, `mssql+pyodbc://…`, `oracle+oracledb://…`, `snowflake://…`, `sqlite:////…`, …) or a local **SQLite file** (`.db`/`.sqlite`/`.sqlite3`). | — |
| `-o`, `--output` | option | Report format: `json` or `yaml`. | `json` |
| `--schema-format` | option | Force the schema format (`json`/`yaml`/`xml`/`txt`) instead of auto-detecting. | auto-detect |
| `--db-schema` | option | Database schema/namespace to read tables from when `DATA` is a URL (Postgres `public`, SQL Server `dbo`, …). | (engine default) |
| `--version` | flag | Print the package version and exit. | — |
| `--help` | flag | Print CLI help and exit. | — |

`python -m schemascope ...` behaves identically to the `schemascope` console script:

```bash
python -m schemascope users-schema.json "postgresql+psycopg://user:pw@host/app" --output yaml
```

### Exit codes

Keep these two in mind when scripting schemascope into a pipeline:

- **`0`** — success. The report was printed to **stdout**.
- **`2`** — something was wrong with your inputs: a **schema error**, a **data-source error**, or **bad CLI arguments**. The one-line message goes to **stderr** (`schema error: ...` or `data source error: ...`). argparse also uses exit code `2` for its own usage errors, such as a missing argument.

> **Rule to remember:** the report goes to **stdout**, error messages go to **stderr**. You can redirect them separately (`schemascope schema.json "postgresql+psycopg://user@host/app" > report.json 2> errors.log`). A `present: false` or `type_ok: false` inside a successful report is **not** an error — it is drift, and the exit code is still `0`.

---

## Step 1 — Give it your schema

A schema is a small text file listing your entities and their fields. You write it (or generate it) yourself — schemascope never invents one from your data.

### Decision guide: which situation are you in?

| Your situation | What to do |
| --- | --- |
| Someone gave you a schema file already | Skip ahead — you have Step 1 done. Go to [Step 2](#step-2--point-it-at-your-database). |
| Your data is in a database | Read its structure and translate the types into a small schema. [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine) has the per-engine commands. |
| You have only a database, no schema | Hand-write one. Start from the smallest valid schema below and add fields. |

### The smallest valid schema, then build up

The minimum schema is **one entity with one field**:

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

That is valid. A field does not even require a `type` — omit it and the declared type normalizes to `unknown`, which is compatible with anything (so it never triggers a mismatch). But you usually want to declare a type so drift is caught.

Add one object to `fields` per column, and give each a `type`:

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

- **`"primary_key": true`** marks the field that identifies each row. A primary key is treated as **not nullable** unless you say otherwise, so `{"name": "id", "type": "integer", "primary_key": true}` normalizes to an integer field with `nullable: false`.
- **`"nullable": true`** marks a field that is allowed to be empty (`age` is a classic example). schemascope reports nulls no matter what; the `nullable` flag documents your *intent* so a human reading the schema knows an empty `age` is expected and an empty `email` is not. (In this MVP the profiler does not turn `nullable: false` into a `type_ok: false` failure by itself; it surfaces `null_count`/`null_fraction` so you can decide.)

The full model — validation rules, `source`, descriptions, metadata — is in [Schema Model](#schema-model) and [Schema Formats](#schema-formats).

### Accepted schema formats

You can write the same schema in any of four formats. schemascope picks the parser from the file extension (see [Format Detection](#format-detection)).

| Format | Extensions | One-line note |
| --- | --- | --- |
| **JSON** | `.json` | Curly-brace objects. Good for tooling and generated schemas. |
| **YAML** | `.yaml`, `.yml` | Indented, less punctuation than JSON. PyYAML is bundled. |
| **XML** | `.xml` | Attribute-based; root element must be `<schema>`. A default namespace is allowed and ignored. |
| **TXT DSL** | `.txt`, `.dsl`, `.schema` | The least fiddly to hand-write — no braces, no quotes. Cannot express schema `name`/`version`, entity `source`, or descriptions. |

For quick hand-authoring, the TXT DSL is easiest. This TXT file:

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

In the DSL, `pk` marks the primary key and `null` marks a nullable field. The full DSL rules are in [Schema Formats](#schema-formats).

### `source` vs `name`

Every entity has a `name`. It may *also* have a `source`. The difference:

- `name` is what the entity is called in your schema and in the report.
- `source` is the **backing table** schemascope actually reads: the database (or SQLite) table name.

If you set no `source`, schemascope uses the `name`. So an entity named `users` reads a table named `users`. But if the real table is named `app_users` while you want the entity called `users`, set a `source`:

```json
{
  "name": "users",
  "source": "app_users",
  "fields": [ {"name": "id", "type": "integer", "primary_key": true} ]
}
```

Now the entity is reported as `users` but the data is read from a table named `app_users`. Note: the TXT DSL cannot express `source`; use JSON, YAML, or XML if you need it.

### Forcing the format

If your schema file has a misleading or missing extension, force the parser with `--schema-format`:

```bash
schemascope schemafile "postgresql+psycopg://user@host/app" --schema-format yaml
```

Accepted values are `json`, `yaml`, `xml`, and `txt`.

> **Step 1 is done when** `schemascope <schema> <database-url>` runs without a `schema error:` and you see your entities and fields listed in the report — even if some are `present: false`. That means schemascope understood your schema.

> **Next:** if your schema needs to come out of a real database, see [Appendix A — Reading your schema and connecting, engine by engine](#appendix-a-reading-your-schema-and-connecting-engine-by-engine). It gives the exact read-the-structure command and the connection URL for every major engine.

---

## Step 2 — Point it at your database

The `DATA` argument is one of two things.

**1. A live database** — any SQLAlchemy URL, one table per entity. This is the main way to use schemascope: point it straight at your database.

```bash
schemascope schema.json "postgresql+psycopg://user:pw@host:5432/shop"
schemascope schema.json "mysql+pymysql://user:pw@host:3306/shop"
schemascope schema.json "mssql+pyodbc://user:pw@host/shop?driver=ODBC+Driver+18+for+SQL+Server"
schemascope schema.json "oracle+oracledb://user:pw@host:1521/?service_name=XEPDB1"
```

Install the driver for your engine first — `pip install "schemascope[postgres]"` (or `[mysql]` / `[mssql]` / `[oracle]` / …); SQLite needs none. schemascope recognizes a database source by the `://` in the URL. Add `--db-schema public` (or `dbo`, …) to target a specific namespace. Per-engine URLs and drivers are in [Data Sources → SQL database](#sql-database-any-sqlalchemy-url) and [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine).

**2. A single SQLite file** — one table per entity:

```bash
schemascope schema.yaml warehouse.sqlite
```

### The table / entity matching rule

schemascope matches an entity to its backing table by the entity's **source-or-name**:

- entity `users` → table `users`;
- if the entity has `source: app_users`, it reads table `app_users` instead;
- if the backing table is not found, the entity is reported with `present: false` rather than silently dropped.

Fields are matched to columns by name: exact match first, then a case-insensitive fallback (so `Email` in the schema matches `email` in the database); if nothing matches, the field is reported `present: false`.

The full read behavior (case-insensitive table resolution, dialect-quoted identifiers, streaming reads, SQLite native types) is in [Data Sources](#data-sources).

---

## Concepts and glossary

A few terms are used throughout this manual. Each is one or two sentences.

- **Entity** — one table. One entity maps to one database table (`users` entity → `users` table), or one table in a SQLite file.
- **Field** — one column of an entity (for example, `email` or `age`).
- **Source** — the *backing table* name schemascope actually reads for an entity. It defaults to the entity's `name` but can be overridden with a `source` value (see [`source` vs `name`](#source-vs-name)).
- **Declared type** — the type you *wrote* in your schema for a field (for example, `integer`). schemascope normalizes it to one of seven canonical types.
- **Inferred type** — the type schemascope *deduces from the actual data values* it scans in the database. Declared and inferred can differ; comparing them is the point of the tool.
- **Null** — a missing value. A real `NULL` in the database (or SQLite) counts as null.
- **null_fraction** — `null_count / row_count` for a field: the share of rows where that field was null. `0.4` means 40% of rows were null.
- **Distinct count** — the number of *different* non-null values seen in a field. A column of `0,0,1,0,0` has a distinct count of 2 (the values `0` and `1`).
- **Schema drift** — when the data no longer matches what the schema expects: a table went missing, a column disappeared, nulls appeared where they should not, or a column's values stopped looking like the declared type. Detecting drift is what schemascope is for.
- **Connector** — schemascope's internal reader for a data source. There are two: a SQL-database connector (any SQLAlchemy URL) and a SQLite-file connector. You never construct these by hand from the CLI; the tool picks one for you based on what you point it at.
- **Present** — whether a thing was actually found. An entity is `present: true` if its backing table exists; a field is `present: true` if a matching column exists. When something is missing, schemascope keeps it in the report with `present: false` instead of dropping it silently — that is how drift stays visible.

---

## Schema Model

Every schema format is normalized into the same model:

- A schema has optional `name` and `version` metadata.
- A schema contains one or more `entities`.
- Each entity has a `name`, optional `source`, optional `description`, and one or more fields.
- Each field has a `name`, a canonical `type`, `nullable`, `primary_key`, and optional `description`.

The profiler reads data from `entity.source` when it is set; otherwise it uses `entity.name`. Either way that resolves to a database (or SQLite) table named `<source>`.

Validation rules (violating any of these is a schema error, exit code `2`):

- The schema must define **at least one entity**.
- Each entity must define **at least one field**.
- **Entity names must be unique.**
- **Field names must be unique within each entity.**
- Empty entity names and empty field names are rejected.

A primary key is treated as `nullable: false` unless `nullable` is stated explicitly.

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

XML is attribute-based. A default XML namespace is allowed and ignored during parsing. The root element must be `<schema>`.

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
- Supported flags are `pk`, `primary_key`, `primary key` (mark a primary key); `null`, `nullable` (mark nullable); and `not null`, `notnull`, `required` (mark not-nullable).
- `unique` is accepted in the field text but is currently ignored.
- Indentation is cosmetic.
- Flags are case-insensitive and order-free.

TXT does **not** represent schema-level `name` or `version`, entity `source`, or descriptions. For strict whole-model equality across JSON, YAML, XML, and TXT, use only the subset of metadata the TXT DSL can express.

### Richer JSON/YAML/XML metadata

JSON and YAML support this fuller shape:

```yaml
name: customer_tables
version: "2026-07"
entities:
  - name: users
    source: app_users
    description: User accounts table
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
<schema name="customer_tables" version="2026-07">
  <entity name="users" source="app_users" description="User accounts table">
    <field name="id" type="integer" primary_key="true" description="Internal user id"/>
    <field name="email" type="varchar"/>
    <field name="created_at" type="timestamp" nullable="false"/>
  </entity>
</schema>
```

---

## Type Names

Declared type names are normalized before profiling. Matching is **case-insensitive**, ignores surrounding whitespace, and **strips any trailing `(size[,scale])` / `(max)` parameter** — so `VARCHAR(255)`, `numeric(10, 2)`, and `double precision` all resolve. A wide range of vendor/dialect spellings is recognized, so in most cases you can paste your database's own type names verbatim.

Canonical type | Accepted aliases (case-insensitive; `(…)` parameters stripped)
--- | ---
`string` | `str`, `string`, `char`, `character`, `nchar`, `varchar`, `varchar2`, `nvarchar`, `character varying`, `text`, `ntext`, `tinytext`/`mediumtext`/`longtext`, `clob`, `citext`, `uuid`, `guid`, `uniqueidentifier`, `enum`, `set`, `json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `struct`, `map`, `bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image`, `time`, `interval`, `year`, `inet`, `cidr`, `geometry`/`geography`, `objectid`
`integer` | `int`, `integer`, `int2`/`int4`/`int8`, `int64`, `bigint`, `smallint`, `tinyint`, `mediumint`, `serial`/`bigserial`/`smallserial`, `long`, `varint`, `counter`
`float` | `float`, `float4`/`float8`/`float64`, `double`, `double precision`, `real`, `decimal`, `numeric`, `number`, `money`, `smallmoney`, `bignumeric`, `decimal128`
`boolean` | `bool`, `boolean`, `bit`
`date` | `date`
`datetime` | `datetime`, `datetime2`, `smalldatetime`, `timestamp`, `timestamptz`, `timestamp with`/`without time zone`, `datetimeoffset`, `timestamp_ntz`/`ltz`/`tz`
`unknown` | empty, missing, non-string, array *notation* (`int[]`), or any spelling not covered above

The seven canonical types are `string`, `integer`, `float`, `boolean`, `date`, `datetime`, and `unknown` — those are the only type names schemascope reasons about internally. Every alias above simply normalizes to one of them.

Exotic types (`json`, `jsonb`, `blob`, `bytea`, `geometry`, `array`, …) map to `string` on purpose: read from the database their values arrive as serialized/hex/base64 text and infer as `string`, so a declared `string` accepts them. Only a spelling nothing above covers falls through to `unknown` — not a crash, but you lose the drift check for that one field (a declared `unknown` is compatible with any inferred type). The full per-database reference is [Appendix B](#appendix-b-type-mapping-cheat-sheet).

> **Rule to remember:** a primary key is treated as not nullable unless `nullable` is explicitly set. For example, `{"name": "id", "type": "int", "primary_key": true}` normalizes to an integer field with `nullable: false`.

---

## Data Sources

schemascope reads two kinds of data source: a **live SQL database** (any SQLAlchemy URL — the primary path) and a **local SQLite file**. It **only reads**, and streams rows so a large table never loads fully into memory.

### SQL database (any SQLAlchemy URL)

Pass a SQLAlchemy database URL as `DATA` and schemascope profiles the **live database** directly — this is the primary way to run it:

```bash
schemascope schema.json "postgresql+psycopg://user:pw@host:5432/shop"
schemascope schema.json "mysql+pymysql://user:pw@host:3306/shop"
schemascope schema.json "mssql+pyodbc://user:pw@host/shop?driver=ODBC+Driver+18+for+SQL+Server"
schemascope schema.json "oracle+oracledb://user:pw@host:1521/?service_name=XEPDB1"
schemascope schema.json "sqlite:////abs/path/app.db"
```

**Works with any engine SQLAlchemy has a dialect for.** The connector is fully generic — it uses only SQLAlchemy reflection plus a dialect-quoted `SELECT`, so every engine works the same way. Install the driver for yours and pass its URL:

| Engine | URL prefix | Install |
| --- | --- | --- |
| PostgreSQL (RDS/Aurora/Cloud SQL/Supabase/Neon) | `postgresql+psycopg://` | `pip install "schemascope[postgres]"` |
| MySQL / MariaDB | `mysql+pymysql://` | `pip install "schemascope[mysql]"` (or `[mariadb]`) |
| SQL Server / Azure SQL / Fabric | `mssql+pyodbc://…?driver=ODBC+Driver+18+for+SQL+Server` | `pip install "schemascope[mssql]"` |
| Oracle | `oracle+oracledb://` | `pip install "schemascope[oracle]"` |
| SQLite | `sqlite:///…` | built-in |
| DuckDB | `duckdb:///…` | `pip install "schemascope[duckdb]"` |
| CockroachDB | `cockroachdb://` | `pip install "schemascope[cockroach]"` |
| Amazon Redshift | `redshift+redshift_connector://` | `pip install "schemascope[redshift]"` |
| Snowflake | `snowflake://` | `pip install "schemascope[snowflake]"` |
| Google BigQuery | `bigquery://` | `pip install "schemascope[bigquery]"` |
| Databricks | `databricks://` | `pip install "schemascope[databricks]"` |
| IBM Db2 | `db2+ibm_db://` | `pip install "schemascope[db2]"` |
| Trino / Presto | `trino://` | `pip install "schemascope[trino]"` |
| ClickHouse | `clickhouse+native://` | `pip install "schemascope[clickhouse]"` |
| Any other engine | `dialect+driver://` | that dialect's SQLAlchemy package |

Behavior:

- A source is treated as a database when the `DATA` string contains `://`. SQLAlchemy ships with schemascope; only the per-engine driver is separate (SQLite needs none).
- Each entity maps to a table named by `entity.source` or `entity.name`, resolved **case-insensitively** to the database's real table name. Identifiers are quoted through the dialect's own preparer, so reserved-word and spaced names are safe.
- Add `--db-schema public` (or `dbo`, a Fabric/Snowflake schema, …) to read from a specific namespace.
- Tables are **read, never written**, and rows **stream**, so a large table never loads fully into memory.
- A table (or column) the schema names but the database does not have is reported `present: false` — schemascope profiles what exists and flags the rest as drift, rather than failing.

### SQLite database

Pass a `.db`, `.sqlite`, or `.sqlite3` file:

```bash
schemascope schema.yaml warehouse.sqlite
```

Each entity maps to a table named by `entity.source` or `entity.name`. SQLite values are read with their native Python types where SQLite provides them. A file that is not actually a SQLite database fails cleanly as a data-source error. (You can also point schemascope at the same file with a URL: `sqlite:////abs/path/warehouse.sqlite`.)

### Column matching

Fields are matched to source columns by name:

1. Exact column name match.
2. Case-insensitive fallback (so `Email` in the schema matches `email` in the data, and vice versa).
3. If no column matches, the field is reported with `present: false`.

Entity/table matching uses the entity source or name. Missing entities are reported with `present: false` rather than silently dropped.

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
`source` | Database (or SQLite) table name used for this entity
`present` | Whether the backing table exists
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

Missing entities and missing columns stay in the report with `present: false` (and their numeric fields at `0`, `inferred_type: unknown`, `type_ok: true`). That makes drift visible instead of dropping absent objects from the output.

---

## Type Inference

`schemascope` infers one type per field from observed non-null values.

Inference checks *every* non-null value for each field, in a single streaming pass. A type is chosen only when **every** value matches that type, so a column that drifts to a non-conforming value anywhere in the table — not just in the first few rows — is reported as a mismatch. If no specific type matches, the inferred type is `string`. If there are no non-null values at all, the inferred type is `unknown`.

Inference order (most specific first):

1. `boolean`
2. `integer`
3. `float`
4. `date`
5. `datetime`
6. `string` fallback

Recognized values:

- **Boolean:** real booleans, or `true`, `false`, `1`, `0`, `yes`, `no`, `t`, `f`, `y`, `n` case-insensitively.
- **Integer:** real integers or ASCII integer strings such as `1`, `0`, `-12`, `+42`. Real booleans are not integers. Values with a decimal point (`3.0`) are not integers.
- **Float:** real integers/floats or strings that parse as finite floats. `nan`, `inf`, and `infinity` are rejected.
- **Date:** strict `YYYY-MM-DD` calendar dates.
- **Datetime:** `YYYY-MM-DD` followed by a space or `T` and an `HH:MM` or `HH:MM:SS` time. Fractional seconds and a trailing `Z` are accepted.

> **Watch out:** because inference is strict, values that *look* like a type but do not match the exact format fall through to `string`. A timestamp with a numeric zone offset such as `2021-03-05 10:00:00+00` does **not** match the datetime pattern (only a trailing `Z` is stripped), so a column of such values infers as `string`. A time-of-day like `10:30:00` is not a recognized type and also infers as `string`. If you know a column will hold these, either declare it `string`, or return it in the strict format (for example via a view — see [Appendix A](#appendix-a-reading-your-schema-and-connecting-engine-by-engine)).

Compatibility is intentionally lenient. `type_ok` is `true` when:

- Declared and inferred types are **equal**.
- Declared `string` accepts **any** inferred type.
- Declared `float` accepts inferred `integer`.
- Declared `integer` accepts inferred `boolean` (an all-0/1 column often infers as boolean but is still valid integer data — this is the `deleted` field in the worked example).
- `unknown` on either side is treated as compatible.

Everything else is a type mismatch (`type_ok: false`). Note the asymmetry: a declared `boolean` whose data infers `integer` (values outside 0/1) *is* flagged.

> **Robustness:** schemascope has been exercised against 300+ generated, migrated database schemas across two engines (SQLite and DuckDB), streaming well over a million values — it never crashes and reports drift correctly.

---

## Python API

The main API is available from the top-level package:

```python
import schemascope

schema = schemascope.load_schema("users-schema.json")
connector = schemascope.open_connector("postgresql+psycopg://user:pw@host:5432/app")

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
    SqlConnector,
    SqliteConnector,
    load_schema,
    open_connector,
    profile,
)
```

`open_connector(path, db_schema=None)` chooses a connector automatically:

- A **SQLAlchemy URL** (any string containing `://`) → `SqlConnector` (a live database)
- A `.db`, `.sqlite`, `.sqlite3` file → `SqliteConnector`

`db_schema` names a database schema/namespace (Postgres `public`, SQL Server `dbo`, …) and is used only by `SqlConnector`. The caller owns connector lifecycle — close connectors when finished, ideally in a `try/finally`.

Also exported from the top-level package: `Schema`, `Entity`, `Field`, `SchemaError`, `ConnectorError`, `normalize_type`, `infer_type`, `type_compatible`, `detect_format`, `store_name`, `__version__`, and `CANONICAL_TYPES`.

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

- Leading `<` → XML
- Leading `{` or `[` → JSON
- YAML mapping with an `entities` key → YAML
- Anything else → TXT DSL
- An empty file is an error.

Use `--schema-format` when a file extension is misleading or absent:

```bash
schemascope schemafile "postgresql+psycopg://user@host/app" --schema-format yaml
```

---

## Troubleshooting

Almost every failure exits with code `2` and prints a one-line message to **stderr**. The report itself (when the run succeeds) goes to **stdout**, so you can redirect them separately. Here are the real failure modes and their fixes.

Symptom (stderr message) | What it means | Fix
--- | --- | ---
`data source error: cannot determine a connector for ... (expected a SQLAlchemy database URL such as postgresql+psycopg://user@host/db, or a .db/.sqlite/.sqlite3 SQLite file)` | You pointed `DATA` at something that is neither a SQLAlchemy URL (no `://`) nor a SQLite file. | Pass a database URL (`postgresql+psycopg://user@host/db`, `mysql+pymysql://…`, …), or a `.db`/`.sqlite`/`.sqlite3` file.
`data source error: reading a database URL needs SQLAlchemy — install it with 'pip install SQLAlchemy' ...` | SQLAlchemy is not importable. It ships with schemascope, so this normally only happens in a broken environment. | Reinstall schemascope (`pip install --force-reinstall schemascope`), which pulls in SQLAlchemy.
`data source error: cannot open database '...': ...` | The URL is malformed, or the dialect/driver for that engine is not installed. | Check the URL, and install the engine's extra, e.g. `pip install "schemascope[postgres]"`.
`data source error: cannot connect to '...': ...` | The engine was created but the connection failed — wrong host/port, bad credentials, TLS, or network/VPN. | Verify host, port, database, and credentials; confirm the server is reachable from where schemascope runs.
`data source error: cannot read table '...': ...` | Connected, but reading a specific table failed (permissions, or the table changed mid-run). | Grant read access to that table, or re-check its name/namespace (`--db-schema`).
`data source error: cannot open SQLite database ...: file is not a database` | The `.db`/`.sqlite` file you passed is not actually a SQLite database (wrong file, corrupt, or a text file renamed). | Rebuild the SQLite file, or point at the correct one. Verify with `sqlite3 file.db ".tables"`.
`data source error: SQLite database not found: ...` | The SQLite path does not exist. | Check the path.
`schema error: schema is missing the 'entities' key` | Your JSON/YAML parsed fine but has no top-level `entities`. | Add an `entities:` list; every schema needs at least one entity.
`schema error: schema defines no entities` | The `entities` list is present but empty. | Add at least one entity with at least one field.
`schema error: <path>: empty schema file` | The schema file is empty (for a file whose format had to be sniffed). | Put a real schema in the file. Note: an **empty `.json`** file instead reports `invalid JSON schema: Expecting value...` because the `.json` extension forces the JSON parser.
`schema error: invalid JSON schema: ...` / `invalid YAML schema: ...` / `invalid XML schema: ...` | The file is malformed for its format. | Fix the syntax. If the *format* was auto-detected wrongly, force it with `--schema-format`.
`schema error: duplicate entity name: 'users'` | Two entities share a name. | Rename one; entity names must be unique.
`schema error: entity 'users': duplicate field name: 'id'` | Two fields in one entity share a name. | Rename one; field names must be unique within an entity.
Wrong format auto-detected (e.g. a DSL file read as YAML) | The file has no recognized extension and the content sniffer guessed wrong. | Pass `--schema-format json|yaml|xml|txt`, or give the file a recognized extension.
`schemascope: command not found` | The console script is not on your `PATH`. | Run it as `python -m schemascope ...`, or reinstall so the script lands on `PATH`.

### Reading the report itself (not errors)

These are **not** crashes — they are the profile telling you something.

- **`"present": false` on an entity** — the backing database (or SQLite) table was not found. Check that the table name matches `<source>` (or the entity `name`) and that it lives in the namespace you targeted with `--db-schema`. This is drift, not an error; exit code is still `0`.
- **`"present": false` on a field** — no column matched that field name (after the case-insensitive fallback). The column may have been renamed or dropped. Also drift, not an error.
- **A field's `declared_type` is `unknown`** — the `type` you wrote matched no recognized alias. Most vendor spellings *are* recognized (`jsonb`, `timestamptz`, `serial`, `int4`, `varchar(255)`, …); the usual culprits are array *notation* (`int[]`), a bespoke domain type, or a typo. It will not fail (an `unknown` declared type is compatible with anything), but you lose the drift check. Replace it with a canonical name (`string`, `integer`, `datetime`, ...); see [Appendix B](#appendix-b-type-mapping-cheat-sheet).
- **`"type_ok": false`** — the type inferred from the data is not compatible with the declared type. Example: you declared `age` as `integer` but a row contains `"unknown"`, so the whole column infers as `string`, and `integer` does not accept `string`. Either fix the data, or reconsider the declared type. This is the core drift signal.

---

## Limitations

- This is not a full data validation engine. It profiles presence, nulls, distinct counts, inferred types, and type compatibility. It does **not** flag a `nullable: false` field just because nulls appear — it reports the counts and leaves the judgment to you.
- It does not enforce foreign keys, uniqueness, ranges, regexes, or custom constraints.
- Type inference scans every non-null value in one pass (O(1) memory per field), so drift anywhere in the column is caught — at the cost of running the type predicates over the full column rather than a sample.
- `distinct_count` tracks all distinct non-null values for each profiled field, which is simple and exact but not approximate-memory analytics.
- TXT schemas do not support metadata such as schema name, version, source, or descriptions.
- To read a **live database** you install the driver for that engine (`psycopg`/`psycopg2`, `PyMySQL`/`mysqlclient`, `pyodbc`, `oracledb`, …); SQLAlchemy itself ships with schemascope. schemascope only ever **reads** — it never writes to your database.

---

## Requirements

- **Python 3.8 or newer** (the floor declared in `pyproject.toml`).
- **PyYAML** and **SQLAlchemy** — installed automatically with the package.
- **A database driver** — only if you profile a live database, one per engine. schemascope is generic across **any SQLAlchemy dialect** (PostgreSQL, MySQL/MariaDB, SQL Server/Azure/Fabric, Oracle, CockroachDB, Redshift, Snowflake, BigQuery, Databricks, Db2, Trino, ClickHouse, DuckDB, …); SQLite needs none. Install the matching extra, e.g. `pip install "schemascope[postgres]"` — the full list is in [Data Sources → SQL database](#sql-database-any-sqlalchemy-url).

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

The build produces a wheel and source distribution in `dist/`.

---

## License

schemascope is released under the **MIT License** (see `pyproject.toml`).

---

> **Next:** see [Appendix A — Reading your schema and connecting, engine by engine](#appendix-a-reading-your-schema-and-connecting-engine-by-engine) for copy-paste recipes per engine — how to read a database's structure into a schema file, and the driver + SQLAlchemy URL to profile it **live**.

---

## Appendix A: Reading your schema and connecting, engine by engine

To profile a database you give schemascope two things — a **schema** (what the data should look like) and the **database** itself. This appendix gives, for each engine, both halves.

1. **Get the schema.** Read the real table structure — from the platform's DDL, its `information_schema` catalog, or an introspection command below — and translate each column type into one of schemascope's seven canonical types (`string`, `integer`, `float`, `boolean`, `date`, `datetime`, `unknown`). Write that as a small JSON/YAML/XML/TXT schema. (schemascope reads a schema; it does not invent one, so this step is yours.)

2. **Connect.** Install the driver for your engine and pass its **SQLAlchemy URL** as `DATA`; schemascope reads the tables directly. Each engine section gives the exact `pip install` and URL.

Either half alone leaves you stuck — a schema with no database, or a database with no schema.

### Which situation are you in?

| Your situation | What to do |
| --- | --- |
| Your data is in a **SQL database** (PostgreSQL, MySQL/MariaDB, SQL Server, Oracle, Db2, CockroachDB, BigQuery, Snowflake, Redshift, Databricks) | Read its catalog/DDL into a schema (Step 1), then **connect schemascope live with its SQLAlchemy URL** (Step 2). Find your engine's numbered section below. |
| Your data is in a **NoSQL / document store** (MongoDB, DynamoDB, Elasticsearch, Cassandra) | These have no SQLAlchemy dialect, so first **sample documents/items** to discover fields and their real types, then **load a representative sample into SQLite (or a SQL database)** and profile that. See your store's section. |
| Your data is **already in a SQLite file** | schemascope opens `.db`/`.sqlite`/`.sqlite3` directly. Just read its `.schema` and write a schemascope schema. See [A5. SQLite](#a5-sqlite). |
| Your data is **in flat files** (Parquet, JSON) | Read the types from the file itself, then load it into SQLite or DuckDB and point schemascope at that. See [A17. Schema from flat files](#a17-schema-from-flat-files). |

> **The primary path is the live URL.** For a SQL database, passing its SQLAlchemy URL as `DATA` (with the engine's driver installed) is how schemascope reads the tables. When schemascope genuinely can't reach the database — air-gapped, VPN-only, or a dump someone emailed you — the one supported fallback is to load the data into a **SQLite file** (for example with `pgloader`) and point schemascope at that; it is mentioned sparingly below.

### How schemascope uses what you give it

- **`SCHEMA`** is a **file you write** — JSON, YAML, XML, or TXT — listing your entities, fields, and their canonical types. schemascope reads it; it does not generate it for you.
- **`DATA`** is a **SQLAlchemy database URL** (schemascope connects and reads live) or a **single SQLite file**.
- A URL carries the host, port, database, and credentials for the live connection. schemascope opens a read-only session, streams the rows, and never writes back.

### Before you start — placeholders

The commands below use placeholders. Replace them with your real values:

| Placeholder | Replace with |
| --- | --- |
| `HOST` / `PORT` | Your database server's hostname and port. |
| `DBNAME` / `mydb` | The database (or schema) you are reading. |
| `USER` / `PASSWORD` | Credentials for that database. |
| `users`, `orders`, `TABLE` | The table (or collection) you are profiling. Each becomes one entity. |
| `warehouse.sqlite` | A local SQLite file used only for the "can't connect live" fallback (and for NoSQL samples). |

### A universal starting point: `information_schema.columns`

Most SQL engines implement the ANSI `information_schema`. This query lists a table's columns and types and works, with minor variation, on PostgreSQL, MySQL/MariaDB, SQL Server, Snowflake, Redshift, BigQuery, CockroachDB, Databricks, and others:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

Take each `data_type` it returns and map it to a canonical schemascope type using the master table below (and the per-platform notes that follow).

### Master type-mapping table

Write the **canonical schemascope type** in the middle column into your schema. The left column groups the database types you are likely to see.

Database column type (any platform) | Write this schemascope type | Notes
--- | --- | ---
`char`, `varchar`, `nvarchar`, `text`, `clob`, `string`, `character varying` | `string` | Plain text.
`uuid`, `guid`, `uniqueidentifier` | `string` | `uuid` is a recognized alias, but writing `string` is clearer. Read as text it infers `string`.
`enum`, `set` | `string` | `enum` is a recognized alias; values come back as text.
`json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `geometry`, `geography` | `string` | Recognized — all map to `string`. Read from the database as serialized text they also infer `string`, so `type_ok` holds. (Array *notation* like `int[]` is not covered → `unknown`.)
`bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image` | `string` | Binary. Read as hex/base64 text → infers `string`. (Consider excluding huge binary columns from the profile.)
`smallint`, `int`, `integer`, `bigint`, `int2`, `int4`, `int8`, `serial`, `bigserial`, `tinyint`, `mediumint`, `long` | `integer` | All recognized, including `int2`/`int4`/`int8`, `serial`/`bigserial`, `tinyint`/`mediumint`. (Oracle `NUMBER(p,0)` resolves via `number` → `float`, not `integer` — the scale is stripped with the parameter, and float safely accepts integer data.)
`decimal`, `numeric`, `float`, `double`, `double precision`, `real`, `money`, `number(p,s)` | `float` | `money`/`number` may come back with currency symbols or thousands separators; if so it infers `string` — declare `string`, or return a cleaned value from a view.
`boolean`, `bool`, `bit` | `boolean` | A single-bit or `tinyint(1)` flag column of 0/1 infers `boolean`; declaring `integer` also passes (integer accepts boolean).
`date` | `date` | Returned as `YYYY-MM-DD`.
`timestamp`, `datetime`, `datetime2`, `smalldatetime`, `timestamptz`, `timestamp with time zone` | `datetime` | All recognized as `datetime` (a `(precision)` parameter and `with`/`without time zone` wording are handled). Returned as `YYYY-MM-DD HH:MM:SS`. A trailing numeric zone offset (`+00`) makes the *data* infer `string`; store UTC or read without the offset.
`time`, `time with time zone`, `interval`, `year` | `string` | Recognized — all map to `string` (they read back as text and infer `string`).

> **Rule of thumb:** if you are unsure, declare `string`. A declared `string` accepts any inferred type, so it never produces a false `type_ok: false`. Use the more specific types when you actually want drift detection on that column.

---

### A1. PostgreSQL

**Read the structure.** In `psql`, `\d users` prints the column list and types. For a machine-readable version, use the catalog query:

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

**Map the types.** `text/varchar/char` → `string`; `uuid` → `string`; `smallint/integer/bigint/serial/bigserial/int2/int4/int8` → `integer`; `numeric/decimal/real/double precision/money` → `float`; `boolean` → `boolean`; `date` → `date`; `timestamp`/`timestamptz` → `datetime` (store UTC or read without a `+00` offset — see note above); `json`/`jsonb`/`bytea`/`ARRAY`/`interval` → `string`.

**Connect.** Install the driver and pass the URL:

```bash
pip install "schemascope[postgres]"
schemascope schema.json "postgresql+psycopg://USER:PASSWORD@HOST:5432/mydb"
```

Add `--db-schema public` (or another namespace) to target it explicitly.

*Can't connect live?* Load the data into a SQLite file once, then profile that:

```bash
pgloader postgresql://USER@HOST/mydb sqlite://./warehouse.sqlite
schemascope schema.json warehouse.sqlite
```

---

### A2. MySQL / MariaDB

**Read the structure.** `SHOW CREATE TABLE users;` prints the full DDL. Or use the catalog:

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

**Map the types.** `char/varchar/text` → `string`; `tinyint/smallint/mediumint/int/bigint` → `integer`; `tinyint(1)` is MySQL's boolean and infers `boolean` (declare `boolean` or `integer`); `decimal/float/double` → `float`; `date` → `date`; `datetime/timestamp` → `datetime`; `time`/`year` → `string`; `json`/`blob`/`enum` → `string` (`enum` is a recognized alias, and the data reads back as text either way).

**Connect.**

```bash
pip install "schemascope[mysql]"     # MariaDB: same driver, or use the [mariadb] extra
schemascope schema.json "mysql+pymysql://USER:PASSWORD@HOST:3306/mydb"
```

---

### A3. Microsoft SQL Server / Azure SQL

**Read the structure.** `EXEC sp_help 'dbo.users';` or the catalog:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

**Map the types.** `char/varchar/nvarchar/text/ntext` → `string`; `uniqueidentifier` → `string`; `tinyint/smallint/int/bigint` → `integer`; `decimal/numeric/float/real/money/smallmoney` → `float`; `bit` → `boolean`; `date` → `date`; `datetime/datetime2/smalldatetime/datetimeoffset` → `datetime` (all four are recognized); `time` → `string`; `varbinary`/`image`/`xml` → `string`.

**Connect.** Install the driver (and the Microsoft ODBC Driver 18 for SQL Server on your OS), then pass the URL:

```bash
pip install "schemascope[mssql]"
schemascope schema.json "mssql+pyodbc://USER:PASSWORD@HOST/mydb?driver=ODBC+Driver+18+for+SQL+Server"
```

Add `--db-schema dbo` to target the default namespace.

---

### A4. Oracle Database

**Read the structure.** Query the data dictionary:

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

**Map the types.** `VARCHAR2`/`CHAR`/`NVARCHAR2`/`CLOB` → `string`; `NUMBER(p,0)` → `integer` in intent, though it normalizes via `number` → `float` (harmless — float accepts integer data); `NUMBER(p,s)`/`FLOAT`/`BINARY_FLOAT`/`BINARY_DOUBLE` → `float`; `TIMESTAMP` → `datetime`; `RAW`/`BLOB` → `string`. Note that Oracle's `DATE` actually carries a time component, so it commonly reads as a full timestamp — declare it `datetime` (or `date` if it holds just the date part). There is no native boolean in table columns; a 0/1 `NUMBER(1)` flag infers `boolean`.

**Connect.**

```bash
pip install "schemascope[oracle]"
schemascope schema.json "oracle+oracledb://USER:PASSWORD@HOST:1521/?service_name=XEPDB1"
```

---

### A5. SQLite

SQLite is the easy case: **schemascope opens a `.db`/`.sqlite`/`.sqlite3` file directly, so there is nothing to connect to.**

**Read the structure.** In the `sqlite3` shell:

```bash
sqlite3 warehouse.sqlite ".schema users"
```

**Map the types.** SQLite uses type *affinities*: `INTEGER` → `integer`; `REAL`/`FLOAT`/`DOUBLE` → `float`; `TEXT`/`VARCHAR`/`CHAR` → `string`; `NUMERIC`/`DECIMAL` → `float`; `BLOB` → `string`; `DATE`/`DATETIME` are stored as text or numbers, so declare `date`/`datetime` and confirm the stored format is `YYYY-MM-DD`(`T`/space time). SQLite has no dedicated boolean; 0/1 columns infer `boolean`.

**Run it directly:**

```bash
schemascope schema.json warehouse.sqlite
```

You can equally reach the same file with a URL: `schemascope schema.json "sqlite:////abs/path/warehouse.sqlite"`.

---

### A6. IBM Db2

**Read the structure.** Query the catalog:

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

**Map the types.** `CHAR/VARCHAR/CLOB/GRAPHIC` → `string`; `SMALLINT/INTEGER/BIGINT` → `integer`; `DECIMAL/DECFLOAT/REAL/DOUBLE` → `float`; `BOOLEAN` → `boolean`; `DATE` → `date`; `TIMESTAMP` → `datetime`; `TIME` → `string`; `BLOB`/`XML` → `string`.

**Connect.**

```bash
pip install "schemascope[db2]"
schemascope schema.json "db2+ibm_db://USER:PASSWORD@HOST:50000/MYDB"
```

---

### A7. CockroachDB

CockroachDB speaks the PostgreSQL wire protocol, so its introspection is Postgres-compatible.

**Read the structure.** `SHOW CREATE TABLE users;` prints the DDL, or use `information_schema.columns` as in the [universal query](#a-universal-starting-point-information_schemacolumns).

**Map the types.** Same as [A1. PostgreSQL](#a1-postgresql).

**Connect.**

```bash
pip install "schemascope[cockroach]"
schemascope schema.json "cockroachdb://USER:PASSWORD@HOST:26257/mydb?sslmode=verify-full"
```

---

### A8. Google BigQuery

**Read the structure.** Print a table's schema with the `bq` CLI:

```bash
bq show --schema --format=prettyjson mydataset.users
```

Or query the catalog:

```sql
SELECT column_name, data_type, is_nullable
FROM `myproject.mydataset.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'users';
```

**Map the types.** `STRING` → `string`; `INT64`/`INTEGER` → `integer`; `NUMERIC`/`BIGNUMERIC`/`FLOAT64` → `float`; `BOOL` → `boolean`; `DATE` → `date`; `DATETIME`/`TIMESTAMP` → `datetime` (`YYYY-MM-DD HH:MM:SS` form); `TIME` → `string`; `BYTES`/`JSON`/`GEOGRAPHY`/`ARRAY`/`STRUCT` → `string`.

**Connect.**

```bash
pip install "schemascope[bigquery]"
schemascope schema.json "bigquery://myproject/mydataset"
```

(Authenticate with `GOOGLE_APPLICATION_CREDENTIALS` pointing at a service-account key, or an active `gcloud` login.)

---

### A9. Snowflake

**Read the structure.** `DESCRIBE TABLE users;` lists columns and types, or:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'USERS';
```

**Map the types.** `VARCHAR/STRING/TEXT/CHAR` → `string`; `NUMBER(p,0)`/`INT`/`INTEGER`/`BIGINT` → `integer` in intent (normalizes via `number` → `float`, harmless); `NUMBER(p,s)`/`FLOAT`/`DOUBLE`/`REAL` → `float`; `BOOLEAN` → `boolean`; `DATE` → `date`; `DATETIME`/`TIMESTAMP_NTZ`/`TIMESTAMP_LTZ`/`TIMESTAMP_TZ` → `datetime` (strip the zone offset if present); `TIME` → `string`; `VARIANT`/`OBJECT`/`ARRAY`/`BINARY`/`GEOGRAPHY` → `string`.

**Connect.**

```bash
pip install "schemascope[snowflake]"
schemascope schema.json "snowflake://USER:PASSWORD@ACCOUNT/mydb/myschema?warehouse=MY_WH&role=MY_ROLE"
```

The `myschema` in the URL sets the default namespace; `--db-schema` can override it.

---

### A10. Amazon Redshift

**Read the structure.** Redshift exposes `SVV_COLUMNS` and the legacy `PG_TABLE_DEF` (remember to set `search_path`):

```sql
SELECT column_name, data_type, is_nullable
FROM svv_columns
WHERE table_name = 'users';
```

**Map the types.** `CHAR/VARCHAR/TEXT` → `string`; `SMALLINT/INT2/INTEGER/INT4/BIGINT/INT8` → `integer`; `DECIMAL/NUMERIC/REAL/FLOAT4/DOUBLE PRECISION/FLOAT8` → `float`; `BOOLEAN` → `boolean`; `DATE` → `date`; `TIMESTAMP`/`TIMESTAMPTZ` → `datetime` (strip the zone); `TIME`/`TIMETZ`/`SUPER`/`VARBYTE` → `string`.

**Connect.**

```bash
pip install "schemascope[redshift]"
schemascope schema.json "redshift+redshift_connector://USER:PASSWORD@HOST:5439/mydb"
```

---

### A11. Databricks / Spark SQL

**Read the structure.** `DESCRIBE TABLE users;` (or `DESCRIBE TABLE EXTENDED users`) lists columns and types; `information_schema.columns` is available in Unity Catalog.

**Map the types.** `STRING` → `string`; `TINYINT/SMALLINT/INT/BIGINT` → `integer`; `FLOAT/DOUBLE/DECIMAL` → `float`; `BOOLEAN` → `boolean`; `DATE` → `date`; `TIMESTAMP`/`TIMESTAMP_NTZ` → `datetime`; `BINARY`/`ARRAY`/`MAP`/`STRUCT` → `string`.

**Connect.**

```bash
pip install "schemascope[databricks]"
schemascope schema.json "databricks://token:DAPI_TOKEN@HOST?http_path=/sql/1.0/warehouses/XXXX&catalog=main&schema=default"
```

---

### A12. MongoDB

MongoDB is **schemaless** — documents in a collection need not share the same fields or types — and it has no SQLAlchemy dialect. So *you* decide which fields to profile, sample the data to learn their real types, then load a sample into SQLite (or a SQL database) and profile that.

**Read the structure (discover fields and types).** In `mongosh`, sample documents:

```javascript
db.orders.aggregate([{ $sample: { size: 100 } }])
```

MongoDB Compass has a built-in **Schema** tab that analyzes a collection and reports each field's observed types and how often they appear. The community `variety.js` script does the same from the shell. Use whichever to pick your fields and their dominant types.

**Map the types.** BSON `String` → `string`; `Int32`/`Int64`/`Long` → `integer`; `Double`/`Decimal128` → `float`; `Boolean` → `boolean`; `Date` → `datetime` (Mongo dates carry a time and serialize as ISO-8601 like `2021-03-05T10:00:00.000Z` — the trailing `Z` is fine for schemascope's datetime inference); `ObjectId`/`UUID` → `string`; embedded documents/arrays → `string`.

**Load a sample into SQLite and profile it.** Pull the fields you care about into a SQLite table:

```bash
pip install pymongo
```

```python
import sqlite3
from pymongo import MongoClient

fields = ("orderId", "customerId", "status", "total", "createdAt")
docs = MongoClient("mongodb://localhost:27017").mydb.orders.aggregate(
    [{"$sample": {"size": 1000}}]
)

con = sqlite3.connect("warehouse.sqlite")
con.execute(
    "CREATE TABLE orders (orderId TEXT, customerId TEXT, status TEXT, total REAL, createdAt TEXT)"
)
con.executemany(
    "INSERT INTO orders VALUES (:orderId, :customerId, :status, :total, :createdAt)",
    [{k: d.get(k) for k in fields} for d in docs],
)
con.commit()
con.close()
```

Then run `schemascope schema.json warehouse.sqlite`.

---

### A13. Cassandra / ScyllaDB

Cassandra/ScyllaDB have no SQLAlchemy dialect, so profile a **sample loaded into SQLite** (or a SQL database).

**Read the structure.** In `cqlsh`, `DESCRIBE TABLE users;` prints the DDL, or query the catalog:

```sql
SELECT column_name, type FROM system_schema.columns
WHERE keyspace_name = 'myks' AND table_name = 'users';
```

**Map the types.** `text/varchar/ascii` → `string`; `tinyint/smallint/int/bigint/varint/counter` → `integer`; `decimal/float/double` → `float`; `boolean` → `boolean`; `date` → `date`; `timestamp` → `datetime`; `time` → `string`; `uuid`/`timeuuid`/`inet` → `string`; `blob`/`list`/`set`/`map` → `string`.

**Load a sample into SQLite and profile it.** Read a bounded sample with the `cassandra-driver` and insert the columns you care about into a SQLite table, then `schemascope schema.json warehouse.sqlite`:

```python
import sqlite3
from cassandra.cluster import Cluster

session = Cluster(["127.0.0.1"]).connect("myks")
rows = session.execute("SELECT id, email, age FROM users LIMIT 5000")

con = sqlite3.connect("warehouse.sqlite")
con.execute("CREATE TABLE users (id TEXT, email TEXT, age INTEGER)")
con.executemany(
    "INSERT INTO users VALUES (?, ?, ?)",
    [(str(r.id), r.email, r.age) for r in rows],
)
con.commit()
con.close()
```

---

### A14. Amazon DynamoDB

DynamoDB is **schemaless** apart from its key schema, and has no SQLAlchemy dialect. `describe-table` tells you only the partition/sort keys — not the other attributes — so sample items to learn the rest, then load a sample into SQLite (or a SQL database) and profile that.

**Read the structure (key schema and sample attributes):**

```bash
aws dynamodb describe-table --table-name Orders \
  --query "Table.{Keys:KeySchema, Attrs:AttributeDefinitions}"

# sample some items to see the other attributes:
aws dynamodb scan --table-name Orders --max-items 25
```

**Map the types.** DynamoDB attribute types: `S` (string) → `string`; `N` (number) → `integer` or `float` depending on the values; `BOOL` → `boolean`; `B` (binary) → `string`; `M`/`L` (map/list) → `string`; `SS`/`NS`/`BS` (sets) → `string`. Because attributes are per-item, pick the fields you care about and declare them from what the sample shows.

**Load a sample into SQLite and profile it.** `scan` the table to JSON, flatten the attributes, and insert into SQLite (DynamoDB's native export to S3 writes DynamoDB JSON / Ion / Parquet, which schemascope can't read directly, so this scan-and-load route is the pragmatic one for modest tables):

```bash
aws dynamodb scan --table-name Orders --output json > orders.json
```

```python
import json, sqlite3

items = json.load(open("orders.json"))["Items"]

con = sqlite3.connect("warehouse.sqlite")
con.execute("CREATE TABLE orders (orderId TEXT, customerId TEXT, status TEXT, total REAL)")
con.executemany(
    "INSERT INTO orders VALUES (?, ?, ?, ?)",
    [
        (i["orderId"]["S"], i["customerId"]["S"], i["status"]["S"], float(i["total"]["N"]))
        for i in items
    ],
)
con.commit()
con.close()
```

Then run `schemascope schema.json warehouse.sqlite`. (For large tables, use AWS Glue or an export-then-transform pipeline into a SQL database; the `scan` route is best for modest volumes.)

---

### A15. Elasticsearch

Elasticsearch is document-oriented and has no SQLAlchemy dialect; each index has a **mapping** that plays the role of a schema. Read the mapping, then load a sample into SQLite (or a SQL database) and profile that.

**Read the structure (the mapping):**

```bash
curl -s "http://localhost:9200/orders/_mapping?pretty"
```

**Map the types.** `text`/`keyword` → `string`; `integer`/`long`/`short`/`byte` → `integer`; `float`/`double`/`half_float`/`scaled_float` → `float`; `boolean` → `boolean`; `date` → `datetime` (Elasticsearch dates are usually full timestamps); `ip`/`geo_point`/`object`/`nested` → `string`.

**Load a sample into SQLite and profile it.** Pull a page of hits and insert the fields you care about:

```python
import sqlite3
from elasticsearch import Elasticsearch

es = Elasticsearch("http://localhost:9200")
hits = es.search(index="orders", size=1000)["hits"]["hits"]

con = sqlite3.connect("warehouse.sqlite")
con.execute("CREATE TABLE orders (orderId TEXT, status TEXT, total REAL, createdAt TEXT)")
con.executemany(
    "INSERT INTO orders VALUES (?, ?, ?, ?)",
    [
        (h["_source"].get("orderId"), h["_source"].get("status"),
         h["_source"].get("total"), h["_source"].get("createdAt"))
        for h in hits
    ],
)
con.commit()
con.close()
```

Then run `schemascope schema.json warehouse.sqlite`.

---

### A16. Schema from code and tooling

Sometimes the truest schema lives in your application, not the database. You can read the field types there and translate them the same way. You still point schemascope at the live database (or a SQLite file) as above — these only give you the schema half.

- **Django** — `python manage.py inspectdb > models.py` reverse-engineers models from an existing database; or read your existing model fields. `CharField/TextField/UUIDField/SlugField/EmailField` → `string`; `IntegerField/BigIntegerField/SmallIntegerField/AutoField` → `integer`; `FloatField/DecimalField` → `float`; `BooleanField` → `boolean`; `DateField` → `date`; `DateTimeField` → `datetime`; `JSONField`/`BinaryField` → `string`.
- **SQLAlchemy** — reflect an existing table (`Table('users', metadata, autoload_with=engine)`) or read your models. `String/Text/Unicode` → `string`; `Integer/BigInteger/SmallInteger` → `integer`; `Float/Numeric` → `float`; `Boolean` → `boolean`; `Date` → `date`; `DateTime` → `datetime`; `JSON`/`LargeBinary` → `string`.
- **Ruby on Rails** — `db/schema.rb` lists every column. `t.string/t.text` → `string`; `t.integer/t.bigint` → `integer`; `t.float/t.decimal` → `float`; `t.boolean` → `boolean`; `t.date` → `date`; `t.datetime/t.timestamp` → `datetime`; `t.json/t.jsonb/t.binary` → `string`.
- **Prisma** — `schema.prisma` model fields. `String` → `string`; `Int/BigInt` → `integer`; `Float/Decimal` → `float`; `Boolean` → `boolean`; `DateTime` → `datetime`; `Json/Bytes` → `string`. (Prisma has no bare `date` type; a date-only column is still `DateTime`.)
- **dbt** — column types live in each model's `schema.yml` (and, if you run `dbt docs generate`, in `target/catalog.json`, which carries the warehouse's real types). Map those warehouse types with the platform tables above.

---

### A17. Schema from flat files

If your data is already in flat files, read the types from the file itself, then load it into a database schemascope can open — a **SQLite file** or a **DuckDB** database — and profile that.

- **Parquet / Arrow** — `pyarrow.parquet.read_schema('users.parquet')` prints the column types. `string/large_string` → `string`; `int8/16/32/64` → `integer`; `float/double/decimal` → `float`; `bool` → `boolean`; `date32/date64` → `date`; `timestamp` → `datetime`; `binary`/`list`/`struct` → `string`. DuckDB reads Parquet natively, so materialize a table and profile it live:

  ```bash
  pip install "schemascope[duckdb]" duckdb
  duckdb warehouse.duckdb "CREATE TABLE users AS SELECT * FROM 'users.parquet'"
  schemascope schema.json "duckdb:///warehouse.duckdb"
  ```

  (Or load into SQLite with pandas: `pandas.read_parquet('users.parquet').to_sql('users', sqlite3.connect('warehouse.sqlite'), index=False)`, then `schemascope schema.json warehouse.sqlite`.)
- **JSON** — inspect the object keys to choose fields, and map each value's JSON type: string → `string`; whole-number → `integer`; fractional number → `float`; `true`/`false` → `boolean`; date-looking strings → `date`/`datetime` if they match the strict formats, else `string`. Load into DuckDB (`CREATE TABLE users AS SELECT * FROM read_json_auto('users.json')`) or into SQLite via pandas `json_normalize(...).to_sql(...)`, then profile that.

---

### A18. Worked end-to-end example: from a PostgreSQL `users` table to a schemascope report

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

**Step 2 — translate to canonical types and hand-write the schema.** Using the [master table](#master-type-mapping-table): `bigint` → `integer`, `character varying` → `string`, `integer` → `integer`, `boolean` → `boolean`, `date` → `date`. The primary key becomes `primary_key: true` (not nullable); `age` is nullable. Save this as `schema.json`:

```json
{
  "name": "customer_tables",
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

**Step 3 — connect and profile.** Install the driver and point schemascope at the live database:

```bash
pip install "schemascope[postgres]"
schemascope schema.json "postgresql+psycopg://USER:PASSWORD@HOST:5432/mydb"
```

> **Note:** read live, Postgres booleans come back as Python `True`/`False`, which schemascope recognizes as boolean values — so `active` infers `boolean`.

**Alternative Step 3 — bridge through SQLite.** If schemascope can't reach the database from where it runs, load the same rows into a SQLite file once (for example with `pgloader postgresql://USER@HOST/mydb sqlite://./warehouse.sqlite`) and point schemascope at `warehouse.sqlite` instead.

**Step 4 — read the result.** You get the same report structure as the [getting-started walkthrough](#getting-started--profile-your-database): `users` is `present: true`, each field is `present: true`, `age` shows whatever `null_fraction` your real data has, `deleted` infers `boolean` but `type_ok` stays `true` (integer accepts boolean), and every other field's `type_ok` is `true` if the data matches. Any `present: false` or `type_ok: false` in that output is drift worth investigating.

### How do I check my schema file worked?

schemascope has no separate "inspect" or "validate" command — running the tool *is* the check. Point it at your schema and your database:

```bash
schemascope schema.json "postgresql+psycopg://USER@HOST/mydb"
```

- If you get a `schema error: ...` on stderr, the schema file itself is wrong — fix it (see [Troubleshooting](#troubleshooting)).
- If it runs and every entity and field you declared appears in the report, schemascope understood your schema. `present: true` on an entity means its table was found; `present: true` on a field means a matching column was found.
- A `present: false` entity or field, or a `type_ok: false`, means your schema is fine but the **data** does not match it — that is drift, not a schema problem, and the exit code is still `0`.

---

## Appendix B: Type-mapping cheat sheet

A consolidated reference: given a database column type, the schemascope type in the right-hand column is what it normalizes to. **All the spellings below are recognized aliases** — including parameterized (`varchar(255)`) and multi-word (`double precision`) forms — so you can usually paste your database's own type verbatim. Full rules are in [Type Names](#type-names); only a spelling that appears **nowhere below** falls through to `unknown`.

Canonical schemascope type | Database types that map to it
--- | ---
`string` | `char`, `varchar`, `nvarchar`, `text`, `clob`, `character varying`, `uuid`, `guid`, `uniqueidentifier`, `enum`, `set`, `json`, `jsonb`, `xml`, `hstore`, `variant`, `object`, `array`, `struct`, `map`, `bytea`, `blob`, `binary`, `varbinary`, `bytes`, `image`, `time`, `interval`, `year`, `inet`, `geometry`/`geography`, `ip`, `ObjectId`
`integer` | `int`, `integer`, `bigint`, `smallint`, `tinyint`, `mediumint`, `int2`/`int4`/`int8`, `serial`/`bigserial`, `long`, `int64`, `varint`, `counter`
`float` | `float`, `double`, `double precision`, `real`, `decimal`, `numeric`, `number` (any `NUMBER(p,s)` **or** `NUMBER(p,0)` — the scale is stripped, so Oracle integers land here too; harmless, since float accepts integer data), `money`, `smallmoney`, `float4`/`float8`, `float64`, `decimal128`, `BIGNUMERIC`
`boolean` | `boolean`, `bool`, `bit`. *(A MySQL `tinyint(1)` normalizes to `integer` — the `(1)` is stripped to `tinyint` — but a 0/1 column infers `boolean` from its data and `integer` accepts that, so it still passes.)*
`date` | `date`
`datetime` | `datetime`, `datetime2`, `smalldatetime`, `timestamp`, `timestamptz`, `timestamp with/without time zone`, `TIMESTAMP_NTZ/LTZ/TZ`, `datetimeoffset` (strip any zone offset so it reads back as `YYYY-MM-DD HH:MM:SS`)
`unknown` | Only a spelling that appears nowhere above (e.g. array *notation* `int[]`, a bespoke domain type, or a genuine typo), a non-string, or an empty/missing type. A declared `unknown` is compatible with any inferred type, so its `type_ok` is always `true` — you simply get no drift check on that field.

Reminders that catch people out:

- Vendor spellings **are** recognized — `json`, `jsonb`, `blob`, `bytea`, `array`, `money`, `interval`, `time`, `year`, `serial`, `int4`, `nvarchar`, `datetime2`, `timestamptz`, `varchar(255)`, `double precision`, and the rest of the table above all resolve. You generally don't need to hand-translate types.
- A native UUID column is fine: when read it comes back as text, infers `string`, and a declared `string` (or `uuid`) accepts it.
- When unsure, declare `string` — it accepts any inferred type, so it never produces a false mismatch; use specific types where you want real drift detection.

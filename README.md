# schemascope

**Profile a clinical EMR database into a portable, schema-validated corpus profile.**

Point `schemascope` at your SQL database. It reads through the whole thing,
counts tokens exactly with [tiktoken](https://github.com/openai/tiktoken),
measures the scope of every clinical data stream, captures one representative
patient in full, and hands you two files:

- **`profile.yaml`** — the profile in a human-readable form you can open and read.
- **`profile.json`** — the same content, machine-readable and validated against
  the corpus schema bundled with the tool.

That pair is the whole deliverable. It describes *what your corpus contains and
how big it is* — enough to size, plan, and reason about the dataset.
`schemascope` reads and measures only; it leaves the database untouched.

Before producing those deliverables, `schemascope autodetect` can generate a
working `mapping.yaml`. That file is the reviewed configuration that tells
schemascope how your database schema maps onto the fixed clinical corpus model.

---

## Table of contents

- [What problem it solves](#what-problem-it-solves)
- [What it produces](#what-it-produces)
- [How it works — the four steps](#how-it-works--the-four-steps)
- [Files and generation at a glance](#files-and-generation-at-a-glance)
- [Generate the mapping file (schema generation step)](#generate-the-mapping-file-schema-generation-step)
- [Appendix A: Generate/read schema from database catalogs](#appendix-a-generateread-schema-from-database-catalogs)
- [Install](#install)
- [Using schemascope on the command line](#using-schemascope-on-the-command-line)
  - [The three commands](#the-three-commands)
  - [Step-by-step walkthrough](#step-by-step-walkthrough)
  - [Command & option reference](#command--option-reference)
  - [Exit codes](#exit-codes)
- [Using schemascope from Python](#using-schemascope-from-python)
- [The mapping file — full reference](#the-mapping-file--full-reference)
- [The token model (full vs clinical)](#the-token-model-full-vs-clinical)
- [Quality gates](#quality-gates)
- [Read-only](#read-only)
- [Scope & limitations](#scope--limitations)
- [Requirements & supported databases](#requirements--supported-databases)
- [License](#license)

---

## What problem it solves

Every clinical EMR database is laid out differently — different table names,
different column names, labs stored one way here and another way there, some
data streams present and some absent. Before you can plan anything with such a
dataset, you need an honest, precise description of it: how many patients and
encounters, how many years it spans, which clinical streams exist, how the
diagnoses are coded, and — crucially — **how large it is in tokens**, the unit
that actually governs the cost and feasibility of any downstream language-model
work.

`schemascope` produces exactly that description, in a single standard shape, from
**any SQL EMR database — whatever its table and column names.** It doesn't guess
what your columns mean: you point it at the right ones **once** in a small,
hand-editable [mapping file](#the-mapping-file--full-reference), and after that it
does the counting, the measuring, and the self-checking on its own. In other
words it is *schema-agnostic by configuration* — it fits any layout you map, but
the model it maps **onto** is fixed and clinical (see
[Scope & limitations](#scope--limitations)).

---

## What it produces

The final profile output has three parts. All three appear in both
`profile.yaml` and `profile.json`.

### 1. `corpus` — identity block

The dataset's identity: `name`, `provider`, `country`, `source_system`,
`source_database`, and a `contact`. **You fill these in** at the top of the
mapping file; `schemascope` copies them through verbatim. They describe *whose*
data this is and *where it came from* — the tool cannot infer them.

### 2. Scope — the whole dataset measured

Twelve scope sections, `A1`–`A12`, each computed as SQL aggregates across the
**entire** database (not a sample):

| # | Section | What it measures |
| --- | --- | --- |
| A1 | `scale` | Patients, encounters, source rows, linked tables — **plus the exact token counts** (see [token model](#the-token-model-full-vs-clinical)). |
| A2 | `stream_inventory` | For each of the 17 canonical clinical streams: whether it's present, and how many source rows it holds. Streams you don't hold are recorded as absent. |
| A3 | `record_depth` | Fields populated per encounter, visits per patient (mean & median), and the split of documentation across *consultation / treatment / investigations*. |
| A4 | `longitudinal` | First and last encounter date, number of years covered, and encounters (and new patients) per year. |
| A5 | `geography` | Distinct facilities, regions covered, and the per-region share of activity. |
| A6 | `demographics_scope` | Gender split, mean age, age-parse rate, and the age-band distribution. |
| A7 | `diagnoses_scope` | Coding system, coded-record count, distinct codes, ICD-10 shape-match rate, paired free-text rate, a breakdown by ICD-10 chapter, and the top conditions. |
| A8 | `laboratory_scope` | Distinct analytes, result and order counts, how often units and reference ranges travel with a result, and the top analytes. |
| A9 | `vitals_scope` | Triage row count and per-vital coverage (temperature, blood pressure, pulse, weight, height, BMI, …). |
| A10 | `examination_scope` | Regions in the exam grid, total region cells, and the normal / abnormal / not-examined split. |
| A11 | `medications_scope` | Prescription lines, distinct items, and how completely frequency / route / duration are recorded. |
| A12 | `specialties_scope` | Number of distinct clinical specialties. |

Sections for streams your dataset doesn't hold come back empty or `present:
false`, so a dataset that lacks (say) radiology or vitals profiles just as
cleanly as one that has them.

### 3. One worked patient

A single real patient assembled into the full nested record shape —
demographics, then encounters, each with its notes, diagnoses, labs,
prescriptions, and so on. This shows the *shape* of a record end-to-end, so the
scope numbers above have a concrete example to stand next to. `schemascope` picks
the first patient that has a real encounter, so the example is representative
rather than a stub.

---

## How it works — the four steps

```
   ┌─────────────┐   1 autodetect    ┌──────────────┐   2 review / edit
   │  your SQL   │ ────────────────▶ │ mapping.yaml │ ◀───────────────  you
   │  database   │                   └──────┬───────┘
   └──────┬──────┘                          │
          │            3 profile            │
          └───────────────┬─────────────────┘
                          ▼
                 ┌──────────────────┐   4 QA + schema-validate
                 │   schemascope    │ ─────────────────────────▶  profile.yaml
                 │  (exact tokens + │                             profile.json
                 │  scope + patient)│                             (ready to send)
                 └──────────────────┘
```

1. **autodetect** — `schemascope` reflects your live schema and writes a *proposed*
   mapping: its best guess at which physical table and columns feed each canonical
   stream.
2. **review** — you open that one file and confirm it. Fix any column it guessed
   wrong, and mark streams you don't have as absent. This is the only manual step,
   and it's hand-editable YAML.
3. **profile** — `schemascope` reads the whole database. It makes **two exact
   passes**: a token pass (one patient at a time, so even a billion-token corpus
   never loads more than one record into memory) and a pass of SQL aggregates for
   the scope sections.
4. **QA + validate** — every run checks the finished profile against the bundled
   schema and a set of [quality gates](#quality-gates), then writes the two files.
   A run that fails a gate stops with a non-zero exit instead of writing.

### In plain terms — what you actually do

schemascope does the heavy lifting; your part is small and one-time:

1. **Point it at your database** — give it a read-only connection URL (see
   [Requirements](#requirements--supported-databases) for supported databases).
2. **Confirm the mapping** — run `autodetect`, then open the one `mapping.yaml`
   file and check it: does each clinical stream point at the right table and
   column? Fill in your dataset's name/provider, and mark any stream you don't
   have as `present: false`. It's about a dozen lines to eyeball. This is the only
   judgement call — it exists because no tool can reliably tell from a name like
   `pid`, `subject_key`, or `x12_ref` that it's the patient key. You say so once.
3. **Run it and hand over the result** — `profile` reads the whole database and
   writes `profile.yaml` + `profile.json`. Those two files are the deliverable.

Everything else — reading every row, exact token counting, the scope aggregates,
and the QA + schema checks — is automatic. The numbers are only ever as good as
the mapping you confirm in step 2, so that's the step worth care.

---

## Files and generation at a glance

There are three different file concepts in the workflow. Keeping them separate
removes most confusion:

| File | Created by | Purpose | Final deliverable? |
| --- | --- | --- | --- |
| `mapping.yaml` | `schemascope autodetect --source "$DB_URL" --out mapping.yaml` | A generated **starting map** from your physical database schema to schemascope's 17 canonical clinical streams. You review and edit this before profiling. | No |
| `profile.yaml` | `schemascope profile ... --out-yaml profile.yaml` | Human-readable corpus profile: scope metrics, token counts, stream inventory, and one worked patient. | Yes |
| `profile.json` | `schemascope profile ... --out-json profile.json` | Machine-readable profile with the same content as YAML, validated against the bundled corpus schema. | Yes |

The bundled `corpus_schema.json` is an internal validation contract shipped with
the package. You do not generate or edit it during normal use.

Terminology:

- **Source database schema** means your real tables and columns.
- **Mapping file** means the generated/reviewed `mapping.yaml` that connects your
  tables and columns to the canonical streams.
- **Corpus schema** means the bundled JSON Schema used to validate
  `profile.json`.

When people say "generate the schema" for this tool, they usually mean
**generate the mapping file**. schemascope does not emit SQL DDL, JSON Schema, or
XSD from your database. It generates a reviewable YAML map of your existing
tables and columns.

---

## Generate the mapping file (schema generation step)

The generation step is `autodetect`. It connects to the live database, reflects
table and column metadata, guesses which physical tables/columns match the
canonical clinical streams, and writes a proposed YAML file at the path you pass
to `--out`.

```bash
schemascope autodetect --source "$DB_URL" --out mapping.yaml
```

If your database uses a named schema/namespace, include it:

```bash
schemascope autodetect --source "$DB_URL" --schema dbo --out mapping.yaml
```

If the patient or encounter key columns are not named `patient_id` and
`encounter_id`, tell autodetect the real names:

```bash
schemascope autodetect --source "$DB_URL" \
  --patient-id pat_no \
  --encounter-id visit_no \
  --out mapping.yaml
```

`--out` controls where the generated file is written. A relative path writes
relative to your current working directory; an absolute path writes exactly
there.

The generated file has this shape:

```yaml
corpus:
  name:
  provider:
  country:
  source_system:
  source_database:
  contact:
    name:
    email:
    role:

schema: dbo

keys:
  patient_id: patient_id
  encounter_id: encounter_id

streams:
  demographics:
    present: true
    table: patients
    columns:
      age_years: age
      gender: sex

  encounters:
    present: true
    table: visits
    date_column: visit_date
    columns:
      facility_id: facility
      specialty_id: specialty
      visit_type: visit_type

  radiology:
    present: false
    table:
```

Review it before profiling. The generator is intentionally a starting point, not
the source of truth. It can see names and types; it cannot know the meaning of a
site-specific column such as `pid`, `x12_ref`, `documento`, or `adm_no`.

Review checklist:

- Fill in the `corpus:` identity block. schemascope copies those values into the
  final profile and cannot infer them from the database.
- Confirm `keys.patient_id` and `keys.encounter_id`.
- Confirm every `streams.<name>.table`.
- Confirm each `columns:` mapping: left side is the canonical schemascope field,
  right side is your physical database column.
- Mark streams you do not have as `present: false`.
- Add per-stream `patient_id_column` or `encounter_id_column` if a table uses
  different link column names.
- Add `date_column` where a stream needs time-based metrics.
- For labs, set `layout: long` or `layout: wide`; for wide labs, list
  `analyte_columns`.
- Add `where` filters when rows should be excluded consistently, such as voided
  or cancelled records.
- Add `value_maps` when local codes need interpretation, especially gender.
- Add `clinical_extra` for free-text clinical columns that have no canonical
  field but should count toward clinical-content tokens.

If autodetect cannot produce a useful starting point, write `mapping.yaml`
manually from the same structure. The [mapping file reference](#the-mapping-file--full-reference)
below defines every supported field.

---

## Appendix A: Generate/read schema from database catalogs

Use this appendix when you need to inspect a source database and produce the
schema information needed to fill or verify `mapping.yaml`. These commands do
not profile the data. They list tables, columns, nullability, and database-native
types so you can decide which physical columns map to schemascope's canonical
clinical streams.

The workflow is:

1. Run the catalog command for your database.
2. Identify the patient key, encounter key, date columns, and clinical columns.
3. Translate database-native types into practical schemascope types when needed.
4. Fill or correct `mapping.yaml`.
5. Run `schemascope profile`.

### Universal SQL starting point

Many SQL databases support `information_schema.columns`:

```sql
SELECT
  table_schema,
  table_name,
  column_name,
  data_type,
  is_nullable,
  ordinal_position
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name, ordinal_position;
```

For one table:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

### Type translation guide

Use this table when converting database-native column types into the canonical
types used in schemascope documentation and mapping review.

| Database type family | Canonical type | Notes |
| --- | --- | --- |
| `char`, `varchar`, `nvarchar`, `text`, `clob`, `uuid`, `json`, `jsonb`, `xml`, `blob`, `binary`, arrays, objects | `string` | Use for text, IDs, JSON-like values, binary references, and complex values. |
| `smallint`, `int`, `integer`, `bigint`, `serial`, `tinyint`, `mediumint` | `integer` | Whole-number values. |
| `decimal`, `numeric`, `float`, `double`, `real`, `money`, `number` | `float` | Numeric values with decimals or uncertain scale. |
| `boolean`, `bool`, `bit` | `boolean` | 0/1 flag columns may also be treated as boolean during review. |
| `date` | `date` | Date-only values. |
| `datetime`, `timestamp`, `timestamptz`, `datetime2`, `datetimeoffset` | `datetime` | Values with date and time. |
| unknown/custom/domain types | `string` or review manually | Prefer `string` unless you are certain the values behave as numeric/date/boolean. |

### PostgreSQL

List all user tables and columns:

```sql
SELECT
  table_schema,
  table_name,
  column_name,
  data_type,
  udt_name,
  is_nullable,
  ordinal_position
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name, ordinal_position;
```

Inspect one table in `psql`:

```bash
psql "$DATABASE_URL" -c "\d+ public.users"
```

Dump schema only:

```bash
pg_dump --schema-only --no-owner --no-privileges "$DATABASE_URL" > schema.sql
```

Common type mapping: `text`/`varchar`/`uuid`/`jsonb` -> `string`;
`smallint`/`integer`/`bigint`/`serial` -> `integer`; `numeric`/`double precision`
-> `float`; `boolean` -> `boolean`; `date` -> `date`; `timestamp`/`timestamptz`
-> `datetime`.

### MySQL / MariaDB

List all columns in a database:

```sql
SELECT
  table_schema,
  table_name,
  column_name,
  data_type,
  column_type,
  is_nullable,
  ordinal_position
FROM information_schema.columns
WHERE table_schema = 'DBNAME'
ORDER BY table_name, ordinal_position;
```

Show one table's DDL:

```sql
SHOW CREATE TABLE users;
```

Schema-only dump:

```bash
mysqldump --no-data DBNAME > schema.sql
```

Common type mapping: `char`/`varchar`/`text`/`json`/`enum` -> `string`;
`tinyint`/`smallint`/`mediumint`/`int`/`bigint` -> `integer`; `tinyint(1)` often
represents boolean flags; `decimal`/`float`/`double` -> `float`; `date` ->
`date`; `datetime`/`timestamp` -> `datetime`.

### SQL Server / Azure SQL / Microsoft Fabric

List all columns:

```sql
SELECT
  s.name AS schema_name,
  t.name AS table_name,
  c.name AS column_name,
  ty.name AS data_type,
  c.max_length,
  c.precision,
  c.scale,
  c.is_nullable,
  c.column_id
FROM sys.schemas s
JOIN sys.tables t ON t.schema_id = s.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
ORDER BY s.name, t.name, c.column_id;
```

Use `information_schema` for a portable view:

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'dbo'
ORDER BY table_schema, table_name, ordinal_position;
```

Inspect one table:

```sql
EXEC sp_help 'dbo.users';
```

Common type mapping: `char`/`varchar`/`nvarchar`/`text`/`ntext`/`uniqueidentifier`
-> `string`; `tinyint`/`smallint`/`int`/`bigint` -> `integer`;
`decimal`/`numeric`/`float`/`real`/`money` -> `float`; `bit` -> `boolean`;
`date` -> `date`; `datetime`/`datetime2`/`smalldatetime`/`datetimeoffset` ->
`datetime`.

### Oracle

List columns visible to the current user:

```sql
SELECT
  owner,
  table_name,
  column_name,
  data_type,
  data_length,
  data_precision,
  data_scale,
  nullable,
  column_id
FROM all_tab_columns
WHERE owner = UPPER('SCHEMA_NAME')
ORDER BY owner, table_name, column_id;
```

For current user's tables only:

```sql
SELECT column_name, data_type, nullable, column_id
FROM user_tab_columns
WHERE table_name = 'USERS'
ORDER BY column_id;
```

Full DDL for one table:

```sql
SELECT DBMS_METADATA.GET_DDL('TABLE', 'USERS') FROM dual;
```

Common type mapping: `VARCHAR2`/`CHAR`/`NVARCHAR2`/`CLOB` -> `string`;
`NUMBER(p,0)` usually behaves like `integer`; `NUMBER(p,s)`/`FLOAT` ->
`float`; `DATE` may include time and often maps to `datetime`; `TIMESTAMP` ->
`datetime`; `RAW`/`BLOB` -> `string`.

### SQLite

List tables:

```bash
sqlite3 warehouse.sqlite ".tables"
```

Show schema DDL:

```bash
sqlite3 warehouse.sqlite ".schema"
sqlite3 warehouse.sqlite ".schema users"
```

Column metadata for one table:

```bash
sqlite3 warehouse.sqlite "PRAGMA table_info(users);"
```

Common type mapping: `INTEGER` -> `integer`; `REAL`/`FLOAT`/`DOUBLE` ->
`float`; `TEXT`/`VARCHAR`/`CHAR` -> `string`; `NUMERIC`/`DECIMAL` -> `float`;
`BLOB` -> `string`; date/datetime values depend on how they are stored.

### DuckDB

List tables:

```sql
SHOW TABLES;
```

Describe one table:

```sql
DESCRIBE users;
```

Query columns through `information_schema`:

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name, ordinal_position;
```

Common type mapping: `VARCHAR`/`TEXT`/`JSON` -> `string`; integer types ->
`integer`; `DOUBLE`/`FLOAT`/`DECIMAL` -> `float`; `BOOLEAN` -> `boolean`;
`DATE` -> `date`; `TIMESTAMP` -> `datetime`.

### IBM Db2

List columns:

```sql
SELECT
  tabschema,
  tabname,
  colname,
  typename,
  length,
  scale,
  nulls,
  colno
FROM syscat.columns
WHERE tabschema = UPPER('SCHEMA_NAME')
ORDER BY tabschema, tabname, colno;
```

Capture DDL:

```bash
db2look -d DBNAME -e -t USERS > schema.sql
```

Common type mapping: `CHAR`/`VARCHAR`/`CLOB`/`GRAPHIC` -> `string`;
`SMALLINT`/`INTEGER`/`BIGINT` -> `integer`; `DECIMAL`/`DECFLOAT`/`REAL`/`DOUBLE`
-> `float`; `BOOLEAN` -> `boolean`; `DATE` -> `date`; `TIMESTAMP` ->
`datetime`; `TIME`/`BLOB`/`XML` -> `string`.

### CockroachDB

CockroachDB is PostgreSQL-compatible for most schema inspection:

```sql
SHOW CREATE TABLE users;
```

Or:

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name, ordinal_position;
```

Use the PostgreSQL type mapping as a starting point.

### BigQuery

Show a table schema with the CLI:

```bash
bq show --schema --format=prettyjson PROJECT_ID:DATASET.users
```

Query dataset columns:

```sql
SELECT column_name, data_type, is_nullable
FROM `PROJECT_ID.DATASET.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

Common type mapping: `STRING`/`BYTES`/`JSON`/`GEOGRAPHY`/`ARRAY`/`STRUCT` ->
`string`; `INT64` -> `integer`; `NUMERIC`/`BIGNUMERIC`/`FLOAT64` -> `float`;
`BOOL` -> `boolean`; `DATE` -> `date`; `DATETIME`/`TIMESTAMP` -> `datetime`;
`TIME` -> `string`.

### Snowflake

Describe a table:

```sql
DESCRIBE TABLE users;
```

Query columns:

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'SCHEMA_NAME'
ORDER BY table_schema, table_name, ordinal_position;
```

Common type mapping: `VARCHAR`/`STRING`/`TEXT`/`CHAR`/`VARIANT`/`OBJECT`/`ARRAY`
-> `string`; `NUMBER(p,0)`/`INT`/`INTEGER`/`BIGINT` -> `integer` in intent;
`NUMBER(p,s)`/`FLOAT`/`DOUBLE`/`REAL` -> `float`; `BOOLEAN` -> `boolean`;
`DATE` -> `date`; `TIMESTAMP_NTZ`/`TIMESTAMP_LTZ`/`TIMESTAMP_TZ`/`DATETIME` ->
`datetime`; `TIME`/`BINARY`/`GEOGRAPHY` -> `string`.

### Amazon Redshift

Use Redshift catalog views:

```sql
SELECT table_schema, table_name, column_name, data_type, is_nullable
FROM svv_columns
WHERE table_schema = 'public'
ORDER BY table_schema, table_name, ordinal_position;
```

Legacy option:

```sql
SELECT schemaname, tablename, "column", type
FROM pg_table_def
WHERE schemaname = 'public'
ORDER BY schemaname, tablename, ordinal;
```

Common type mapping: `CHAR`/`VARCHAR`/`TEXT`/`SUPER`/`VARBYTE` -> `string`;
`SMALLINT`/`INTEGER`/`BIGINT` -> `integer`; `DECIMAL`/`NUMERIC`/`REAL`/`DOUBLE`
-> `float`; `BOOLEAN` -> `boolean`; `DATE` -> `date`; `TIMESTAMP` ->
`datetime`.

### Databricks / Spark SQL

Describe one table:

```sql
DESCRIBE TABLE users;
DESCRIBE TABLE EXTENDED users;
```

Unity Catalog information schema:

```sql
SELECT table_catalog, table_schema, table_name, column_name, data_type, is_nullable
FROM system.information_schema.columns
WHERE table_schema = 'default'
ORDER BY table_catalog, table_schema, table_name, ordinal_position;
```

Common type mapping: `STRING`/`BINARY`/`ARRAY`/`MAP`/`STRUCT` -> `string`;
`TINYINT`/`SMALLINT`/`INT`/`BIGINT` -> `integer`; `FLOAT`/`DOUBLE`/`DECIMAL` ->
`float`; `BOOLEAN` -> `boolean`; `DATE` -> `date`; `TIMESTAMP` ->
`datetime`.

### MongoDB

MongoDB is schemaless, so generate schema information by sampling documents and
reviewing observed fields/types.

Sample documents:

```javascript
db.orders.aggregate([{ $sample: { size: 100 } }])
```

Sample keys and BSON types:

```javascript
db.orders.aggregate([
  { $sample: { size: 1000 } },
  { $project: { kv: { $objectToArray: "$$ROOT" } } },
  { $unwind: "$kv" },
  { $group: { _id: "$kv.k", types: { $addToSet: { $type: "$kv.v" } }, count: { $sum: 1 } } },
  { $sort: { _id: 1 } }
])
```

Type mapping: BSON `string` -> `string`; `int`/`long` -> `integer`;
`double`/`decimal` -> `float`; `bool` -> `boolean`; `date` -> `datetime`;
`objectId`, arrays, and embedded documents -> `string` or manual review.

### DynamoDB

Table key schema:

```bash
aws dynamodb describe-table --table-name Orders \
  --query "Table.{Keys:KeySchema, Attrs:AttributeDefinitions}"
```

Sample items:

```bash
aws dynamodb scan --table-name Orders --max-items 25 --output json
```

Attribute mapping: `S` -> `string`; `N` -> `integer` or `float` depending on
values; `BOOL` -> `boolean`; `B`, `M`, `L`, and sets -> `string` or manual
review.

### Elasticsearch

Read an index mapping:

```bash
curl -s "http://localhost:9200/orders/_mapping?pretty"
```

Type mapping: `text`/`keyword`/`ip`/`geo_point`/`object`/`nested` -> `string`;
integer families -> `integer`; float families -> `float`; `boolean` ->
`boolean`; `date` -> `datetime`.

### Cassandra / ScyllaDB

Describe a table:

```sql
DESCRIBE TABLE users;
```

Catalog query:

```sql
SELECT column_name, type
FROM system_schema.columns
WHERE keyspace_name = 'myks' AND table_name = 'users';
```

Type mapping: `text`/`varchar`/`ascii`/`uuid`/`timeuuid`/`inet` -> `string`;
integer families -> `integer`; `decimal`/`float`/`double` -> `float`;
`boolean` -> `boolean`; `date` -> `date`; `timestamp` -> `datetime`;
collections and `blob` -> `string`.

### Schema from code and tooling

Sometimes the most reliable schema source is application code:

- **Django:** `python manage.py inspectdb > models.py`; map `CharField`/`TextField`/`UUIDField` to `string`, integer fields to `integer`, `FloatField`/`DecimalField` to `float`, `BooleanField` to `boolean`, `DateField` to `date`, `DateTimeField` to `datetime`.
- **SQLAlchemy:** reflect with `Table("users", metadata, autoload_with=engine)`; map `String`/`Text` to `string`, integer types to `integer`, `Float`/`Numeric` to `float`, `Boolean` to `boolean`, `Date` to `date`, `DateTime` to `datetime`.
- **Rails:** use `db/schema.rb`; map `t.string`/`t.text` to `string`, `t.integer`/`t.bigint` to `integer`, `t.float`/`t.decimal` to `float`, `t.boolean` to `boolean`, `t.date` to `date`, `t.datetime`/`t.timestamp` to `datetime`.
- **Prisma:** use `schema.prisma`; map `String` to `string`, `Int`/`BigInt` to `integer`, `Float`/`Decimal` to `float`, `Boolean` to `boolean`, `DateTime` to `datetime`, `Json`/`Bytes` to `string`.
- **dbt:** inspect model `schema.yml` or `target/catalog.json` after `dbt docs generate`.

### Schema from flat files

For Parquet/Arrow:

```python
import pyarrow.parquet as pq
print(pq.read_schema("users.parquet"))
```

Map Arrow strings to `string`; integer types to `integer`; float/decimal types
to `float`; bool to `boolean`; date types to `date`; timestamp to `datetime`;
binary/list/struct/map to `string` or manual review.

For JSON, sample keys and value types:

```python
import json
from collections import defaultdict

types = defaultdict(set)
with open("users.json", "r", encoding="utf-8") as fh:
    data = json.load(fh)

rows = data if isinstance(data, list) else [data]
for row in rows[:1000]:
    for key, value in row.items():
        types[key].add(type(value).__name__)

for key in sorted(types):
    print(key, sorted(types[key]))
```

Then load the file into SQLite or DuckDB if you need schemascope to profile the
actual rows.

---

## Install

schemascope is a proprietary, engagement-scoped tool (see [License](#license)), so
it is installed from the source checkout — not from a public package index.

```bash
pip install .            # from the repo root; or:  pip install -e ".[dev]"   (editable + test deps)
```

Then confirm it's on your `PATH`:

```bash
schemascope --version
```

Everything it needs installs with it — database access (SQLAlchemy + pyodbc), the
exact tokeniser (tiktoken), the YAML writer, and the schema validator. There is
nothing else to install and no companion tool to run. **Python 3.9+.**

Pointing it at a database other than SQL Server / SQLite? Add that engine's
driver as an extra: `pip install ".[postgres]"` (also `mysql`, `oracle`,
`snowflake`, `bigquery`, `redshift`).

---

## Using schemascope on the command line

Installing the package puts one command on your `PATH`: **`schemascope`**. It has
three sub-commands, run in order. You only ever touch two things: a **connection
URL** (which database to read) and a **mapping file** (how your schema maps to the
canonical streams).

### The three commands

| Command | What it does | You run it |
| --- | --- | --- |
| `schemascope autodetect` | Inspects your live schema and writes a **proposed** `mapping.yaml`. | Once, to get a starting point. |
| `schemascope profile` | Reads the whole database and writes `profile.yaml` + `profile.json`. | Every time you want a profile. |
| `schemascope validate` | Checks an existing `profile.json` against the schema. | Any time, e.g. before sending. |

Run `schemascope --help` (or `schemascope <command> --help`) to see the same
options listed below, and `schemascope --version` to print the version.

### Step-by-step walkthrough

The **connection URL** is a standard
[SQLAlchemy database URL](https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls).
Two common shapes:

```bash
# Microsoft Fabric / Azure SQL analytics endpoint:
export DB_URL="mssql+pyodbc://@<sql-endpoint>.datawarehouse.fabric.microsoft.com/<database>?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryInteractive"

# A local SQLite file (handy for a trial):
export DB_URL="sqlite:///./mydata.db"
```

**Step 1 — generate the mapping file.** Point `autodetect` at your database. It
reflects the live schema and writes a proposed `mapping.yaml`:

```bash
schemascope autodetect --source "$DB_URL" --out mapping.yaml
```
```text
Proposed mapping -> mapping.yaml
REVIEW IT before profiling: confirm each stream's table/columns and which streams are present:false.
```

> If your patient / encounter key columns aren't named `patient_id` /
> `encounter_id`, tell autodetect: `--patient-id pat_no --encounter-id visit_no`.
> If your tables live under a named schema, add `--schema dbo`.
> See [Generate the mapping file](#generate-the-mapping-file-schema-generation-step)
> for the generated file shape and review checklist.

**Step 2 — review the mapping (the one manual step).** Open `mapping.yaml` and
confirm it against your real schema: fix any column it guessed wrong, fill in the
`corpus:` identity block at the top, and mark any stream you don't have as
`present: false`. See [The mapping file](#the-mapping-file--full-reference) for
every supported field and a larger mapping example.

**Step 3 — profile the database.** This does the full read — the exact token pass
plus the scope aggregates — then runs QA and writes the two files:

```bash
schemascope profile --source "$DB_URL" --mapping mapping.yaml \
    --out-yaml profile.yaml --out-json profile.json
```
```text
Profiling (exact token pass + scope aggregates)…

QA: 0 error(s), 0 warning(s)

  patients   : 48,213
  tokens     : 412,556,190 full  |  210,004,731 clinical (50.9%)  [tiktoken o200k_base]
  wrote -> profile.yaml
  wrote -> profile.json
```

If QA finds an **error**, the run stops and writes nothing:

```text
QA: 1 error(s), 0 warning(s)
  ERROR   [tokens] clinical_content_tokens (…) > total_tokens (…)

QA FAILED — no profile written.
```

So a run either writes a clean, schema-valid profile or stops without writing.
(Both `--out-*` flags are optional; omit them to do a **dry run** that prints QA
and the headline numbers but writes nothing.)

**Step 4 — hand off (and optionally re-check).** The two files *are* the
deliverable. You can re-validate the JSON on its own at any time — no database
needed:

```bash
schemascope validate --json profile.json
```
```text
valid against the corpus schema.
```

### Command & option reference

**`schemascope autodetect`** — propose a mapping from a live database.

| Option | Required | Meaning |
| --- | --- | --- |
| `--source <url>` | yes | SQLAlchemy connection URL of the source database. |
| `--out <path>` | yes | Where to write the proposed mapping YAML. |
| `--schema <name>` | no | DB schema/namespace (e.g. `dbo`) if your tables live under one. |
| `--patient-id <col>` | no | Patient key column name (default `patient_id`). |
| `--encounter-id <col>` | no | Encounter key column name (default `encounter_id`). |

**`schemascope profile`** — build the profile from a mapped database.

| Option | Required | Meaning |
| --- | --- | --- |
| `--source <url>` | yes | SQLAlchemy connection URL of the source database. |
| `--mapping <path>` | yes | Your reviewed mapping YAML. |
| `--out-yaml <path>` | no | Write the human-readable YAML here. |
| `--out-json <path>` | no | Write the schema-valid JSON here. |
| `--schema <name>` | no | DB schema/namespace (e.g. `dbo`). |

**`schemascope validate`** — check a profile JSON against the schema.

| Option | Required | Meaning |
| --- | --- | --- |
| `--json <path>` | yes | Profile JSON to validate. |

### Exit codes

Every command returns `0` on success and a non-zero code on failure, so it drops
straight into a script or CI pipeline:

| Command | `0` (success) | non-zero (failure) |
| --- | --- | --- |
| `autodetect` | mapping written | connection/reflection error |
| `profile` | QA passed; files written | **any QA error** — nothing written |
| `validate` | JSON is valid | JSON is invalid (errors printed) |

## Using schemascope from Python

Everything the CLI does is available as a library — the same four moves in code:

```python
import schemascope as cs

# 1. connect (read-only) and load your reviewed mapping
db = cs.Db(cs.connect("<sqlalchemy-url>"))
mapping = cs.Mapping.from_yaml("mapping.yaml")

# 2. build the profile — exact token pass + scope + one worked patient
profile = cs.build_profile(db, mapping)

# 3. run the QA gates and stop on any error (same gate the CLI enforces)
issues = cs.run_qa(profile)
assert not [i for i in issues if i.level == "error"], issues

# 4. write the deliverable
cs.write_yaml(profile, "profile.yaml")
cs.write_json(profile, "profile.json")
```

`build_profile` returns the profile as a plain Python `dict`, so you can inspect
any number before writing it — e.g. `profile["scale"]["total_tokens"]` or
`profile["scale"]["clinical_content_pct"]`.

---

## The mapping file — full reference

Your tables and columns won't match the canonical names `schemascope` reports in,
so a small **mapping file** bridges the two. `autodetect` writes a first draft;
you review it. This is the tool's single point of configuration, and it's plain,
auditable YAML.

A mapping has four top-level parts:

```yaml
corpus:                       # identity — copied verbatim into the profile
  name: Example Clinical Corpus
  provider: Example Health
  country: Colombia
  source_system: Example EMR
  source_database: SQL Server 2019
  contact: { name: Jane Doe, email: jane@example.org, role: Data lead }

schema:                       # DB schema/namespace, e.g. dbo — leave blank if none

keys:                         # the columns that link rows to a patient / encounter
  patient_id: patient_id
  encounter_id: encounter_id

streams:                      # one entry per canonical stream (see below)
  ...
```

### Per-stream fields

Each stream tells `schemascope` where its data physically lives:

```yaml
streams:
  demographics:
    table: tbl_patient
    columns: { age_years: age_years, gender: sex, home_region: home_region }

  encounters:
    table: tbl_encounter
    date_column: encounter_start                 # drives the longitudinal metrics
    columns: { facility_id: care_center_code, specialty_id: specialty_code, visit_type: care_setting }

  diagnoses:
    table: tbl_encounter                         # two streams may share one table
    columns: { icd10_code: admission_diagnosis_code, diagnosis_name: admission_diagnosis }

  lab_results:                                   # analytes stored as columns (wide)
    table: tbl_lab
    layout: wide
    analyte_columns: [hemoglobin, hba1c, creatinine, total_cholesterol, hdl, ldl]

  prescriptions:
    table: tbl_medication
    columns: { generic_name: medication_name, dose: dose, route: admin_route }

  # a stream you don't hold:
  immunizations: { present: false }
```

Every knob a stream can carry:

| Field | Meaning |
| --- | --- |
| `table` | The physical table this stream reads from. Two streams may point at the same table (e.g. `encounters` and `diagnoses`); the tool de-duplicates so a shared table's storage is never counted twice. |
| `present: false` | You don't hold this stream. Recorded as absent in the profile. |
| `columns` | Map of `canonical_field: physical_column`. Only the fields you have. |
| `patient_id_column` / `encounter_id_column` | Override the link columns from `keys` when *this* table names them differently (e.g. notes that link by `admission_id`). |
| `date_column` | The date/datetime column for time-based metrics (used for `encounters` longitudinal coverage). |
| `layout` | For `lab_results`: `long` (one row per analyte) or `wide` (one column per analyte). Both are supported. |
| `analyte_columns` | For a `wide` lab layout: the list of analyte columns to count. |
| `where` | An optional raw SQL filter applied uniformly to every metric over this stream (e.g. `is_annulled = 0` to exclude voided records). |
| `clinical_extra` | Extra free-text columns whose *values* are clinical content but have no canonical field (e.g. `result_interpretation`, `medical_indications`). Counted into the clinical-content tokens. |
| `value_maps` | Per-field value coding. Most important for gender, where single-letter codes conflict across datasets — `m` is *male* in one, *mujer/female* in another. Declaring the coding makes the buckets correct. |

The `value_maps` gender example, showing why it matters:

```yaml
demographics:
  table: pacientes
  columns: { gender: sex }
  value_maps:
    gender:                 # in THIS dataset m = mujer (female), h = hombre (male)
      female: [m, mujer, f]
      male:   [h, hombre]
      other:  [i, unknown]
```

### The 17 canonical streams

`demographics`, `encounters`, `triage_vitals`, `history_notes`, `physical_exam`,
`region_findings`, `impression_notes`, `diagnoses`, `lab_requests`, `lab_results`,
`radiology`, `prescriptions`, `pharmacy_requests`, `procedures`, `immunizations`,
`allergies`, `referrals`.

Map the streams you have; mark the rest `present: false`.

### Starting from scratch

Start from `autodetect` whenever you can; it gives you the table names, column
names, key columns, and absent streams that it can infer from the live database.
If you need to write a mapping manually, copy the structure above:

- fill `corpus`;
- set `schema` if your tables live under a namespace such as `dbo`;
- set the shared `keys`;
- add one stream block per canonical stream you hold;
- mark the rest `present: false`.

The mapping is ordinary YAML, so it can live in source control and be reviewed
like any other configuration file.

---

## The token model (full vs clinical)

Tokens are the headline number. Every patient record is measured on two content
axes and by two encoders.

**Two content axes:**

| | what it counts |
| --- | --- |
| **full-record** | Every stored field, serialized — values **and** labels, ids, flags, timestamps, JSON structure. This is the storage / ingestion cost of the record. |
| **clinical-content** | Only the *stored values* of the mapped clinical columns — diagnoses, results, medications, narrative, vitals, findings. **Never** field names, headers, keys, ids, dates, flags, or JSON syntax, and nulls / blanks / placeholder-only cells are stripped (see below). The medical signal a model would actually learn from. |

The split between them (e.g. *"51% clinical content"*) tells you how much of the
raw size is real signal versus structural overhead. Which fields count as
clinical content is defined declaratively and auditably — one list per record
section — not buried in the counter.

**How the clinical count is kept clean.** It is built from the stored *values* of
the mapped clinical columns and nothing else — no column header, field name, key,
id, date, flag, or JSON brace/quote ever enters it. Each value is filtered before
it is counted:

- **`NULL` / `None` cells are skipped** — a missing value adds nothing.
- **Blank and whitespace-only cells are skipped.**
- **Sign- or punctuation-only cells are dropped** — a value must contain at least
  one letter or digit, so a lone `-`, `.`, `/`, `|`, or `...` is not counted.
- **Explicit null placeholders are dropped** — a cell that *is* (case-insensitively,
  as the whole value) one of `-` `--` `.` `/` `n/a` `na` `null` `none` `nil` `nan`
  `s/d` `sin dato` `no aplica` `ninguno` `ninguna` `desconocido` `no reportado` … is
  treated as empty. (A note that merely *contains* the word "none" is untouched —
  only a cell that equals the placeholder is dropped.)
- **Real values are kept exactly as stored** — a negative or decimal lab result
  (`-1.2`, `98.6`), a blood pressure (`138/86`), a coded diagnosis, or free-text
  narrative all count, because they carry clinical signal. Kept values are joined
  by newlines, with no structural glue between them.

So the clinical total reflects genuine medical content, not headers, nulls, or
placeholder noise. The **full-record** total, by contrast, is the entire row
serialized as compact JSON — keys, ids, dates, flags, braces, and rendered nulls
included — de-duplicated so a physical table shared by two streams is counted
once. Clinical ÷ full is the "% clinical content" headline.

**Two encoders:** both axes are counted with **`o200k_base`** (the primary,
reported number) *and* **`cl100k_base`** (an independent second count) — a
built-in cross-check on the total.

**Per-patient distribution:** alongside the totals you get min / max, the p50 /
p90 / p99 percentiles, and a 12-bin histogram (`<1k`, `1k-3k`, … `5M+`) of tokens
per patient — so you can see not just the total but how it's spread.

The counting is **streaming**: `schemascope` tokenises one patient at a time and
keeps only running totals, so an exact count over an arbitrarily large corpus
never loads more than a single record into memory.

---

## Quality gates

Every run checks the finished profile before writing it, against these gates:

- the JSON **validates against the bundled corpus schema**;
- clinical tokens ≤ full tokens, and `structure = full − clinical` exactly;
- the distribution bins sum to the patient count; percentiles are monotonic
  (`p50 ≤ p90 ≤ p99`);
- gender, age-band, exam-outcome, and stream-split shares each sum to ~100%;
- no negative counts anywhere;
- the worked patient is present and non-empty.

An **error** stops the run (non-zero exit, nothing written); a **warning** is
reported for review but doesn't block. The package also ships an end-to-end test
that profiles a synthetic database and checks the numbers against known answers.

---

## Read-only

`schemascope` reads and measures only. Every SQL statement it runs is a `SELECT` —
no inserts, updates, or deletes — and its entire output is the two profile files.

---

## Scope & limitations

schemascope is deliberately bounded. It is **schema-agnostic by configuration —
not a universal schema ingester.** Read this before assuming it fits a dataset:

- **Clinical EMR only — the target model is fixed.** It maps your data onto one
  fixed model: the 17 canonical clinical streams and the bundled corpus schema
  (diagnoses, labs, vitals, medications, encounters, …). A non-clinical database
  (e-commerce, logs, finance) has nothing to map onto and won't produce a
  meaningful profile.
- **SQL sources only.** The single input is a live SQL database read via
  SQLAlchemy. It does **not** read schema files — no JSON Schema, XSD, CSV, or
  SQL DDL — and it doesn't reshape your data; you map it in place.
- **Supported adapters are a short list** — SQL Server (incl. Fabric / Azure SQL),
  PostgreSQL, SQLite. Other dialects are untested (see
  [Requirements](#requirements--supported-databases)).
- **Meaning is yours to confirm.** `autodetect` proposes a mapping from table and
  column *names*, but a name is not its meaning — it can't know whether `pid`,
  `subject_key`, or `x12_ref` is the patient key. You confirm the mapping once;
  that human step is the contract, on purpose.
- **Every number is only as good as the mapping.** Point a stream at the wrong
  column and the profile faithfully describes the wrong column. The
  [quality gates](#quality-gates) catch structural faults (bad totals, invalid
  dates, broken distributions) — they cannot catch a plausible-but-wrong mapping.

---

## Requirements & supported databases

- **Python 3.9+**
- The engine-specific SQL (year extraction, the case-folded patient merge, and
  the numeric/blank casts) has a dedicated branch per dialect. Support tiers:
  - **Verified (known-answer tested):** **SQL Server** (incl. **Microsoft Fabric /
    Azure SQL** analytics endpoints), **PostgreSQL**, **SQLite**, and **DuckDB** —
    the DuckDB↔SQLite cross-engine test asserts every scope metric matches, so the
    dialect branches are proven on a genuinely different engine (columnar, strict
    casts, its own regexp/collation).
  - **Hardened (dialect SQL written, pending live validation):** **MySQL /
    MariaDB** and **Oracle** — each has correct year-extraction, binary merge
    ordering, regexp numeric predicate, and guarded numeric cast; run a
    known-answer check against a live instance before trusting the numbers.
  - **Generic fallback:** any other SQLAlchemy dialect (BigQuery, Snowflake, …)
    *connects*, but falls back to generic SQL that can silently miscount — add a
    dialect branch (they're small; see `io.py` / `scope.py`) before relying on it.
- A cross-engine note: token counts reflect the values the driver returns, so a
  column stored as 32-bit `REAL` on one engine and 64-bit on another tokenises
  slightly differently (`13.2` vs `13.199999…`). The **SQL-derived scope metrics**
  are engine-stable; the **token totals** track the actual stored representation.
- All Python dependencies (`SQLAlchemy`, `pyodbc`, `tiktoken`, `PyYAML`,
  `jsonschema`) install automatically with the package. Point it at a non-default
  engine? Add that driver extra — `pip install ".[postgres]"` (also `mysql`,
  `oracle`, `snowflake`, `bigquery`, `redshift`).

---

## License

**Proprietary — © 2026 Meridian Intelligence. All rights reserved.** Not open
source. This software is provided to the counterparty under a **limited,
non-transferable license for use solely within the scope of the parties'
engagement/agreement**, and may not be used, copied, distributed, modified, or
exploited beyond that Purpose. See [LICENSE](LICENSE) for the full terms.

"""Database access + the streaming per-patient merge.

The profiler reads from the same kind of SQL source deidkit does — a SQLAlchemy
URL (Microsoft Fabric / Azure SQL analytics endpoint, or any SQL database; SQLite
for tests). Two access patterns:

* :meth:`Db.scalar` / :meth:`Db.rows` — cheap aggregates for the scope metrics.
* :meth:`Db.iter_patients` — a **k-way merge** over every mapped table ordered by
  ``patient_id``, yielding one patient's raw rows at a time. This is what lets the
  exact token pass cover a billion-token corpus without ever holding more than a
  single patient in memory.

Identifiers (table/column names) are always quoted through the dialect preparer,
never string-interpolated raw.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

# Fold only ASCII A-Z, matching SQLite's ASCII-only SQL LOWER() (str.lower() would
# fold Unicode the SQL side leaves unchanged, desyncing the merge key from ORDER BY).
_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def _ascii_lower(s: str) -> str:
    return s.translate(_ASCII_LOWER)

from .mapping import Mapping, StreamMap
from .model import STREAM_ORDER


def connect(url: str):
    """Create a SQLAlchemy engine for a source URL (read-only usage)."""
    kwargs: Dict[str, Any] = {}
    if not url.startswith("sqlite"):
        # The per-patient merge holds one streaming connection open per present
        # stream for the whole pass; with up to 17 canonical streams the default
        # QueuePool (5 + 10 overflow) would be exhausted. Size it well above the
        # stream count, and pre-ping so a long profiling run survives idle drops.
        kwargs = dict(pool_size=25, max_overflow=25, pool_pre_ping=True)
    return create_engine(url, **kwargs)


class Db:
    def __init__(self, engine, schema: Optional[str] = None):
        self.engine = engine
        self.schema = schema
        self._prep = engine.dialect.identifier_preparer

    # --- identifier quoting ---------------------------------------------- #
    def qi(self, name: str) -> str:
        return self._prep.quote(name)

    # --- dialect-aware text helpers -------------------------------------- #
    def text_cast(self, expr: str) -> str:
        """Cast an expression to text, for stable comparison/blank-detection.
        NVARCHAR(4000)/TEXT are bounded on purpose so the result is usable in
        ORDER BY / GROUP BY (an unbounded MAX/LOB type can't be sorted or grouped
        on some engines). The default branch uses VARCHAR(4000), not CHAR, so an
        unlisted dialect doesn't blank-pad or (Oracle) truncate to one char."""
        d = self.engine.dialect.name
        if d == "mssql":
            return f"CAST({expr} AS NVARCHAR(4000))"
        if d in ("sqlite", "postgresql", "duckdb"):
            return f"CAST({expr} AS TEXT)"
        if d in ("mysql", "mariadb"):
            return f"CAST({expr} AS CHAR)"          # MySQL/MariaDB cast text with CHAR, not VARCHAR
        if d == "oracle":
            return f"CAST({expr} AS VARCHAR2(4000))"
        return f"CAST({expr} AS VARCHAR(4000))"

    def nonblank_count(self, col_quoted: str) -> str:
        """A COUNT expression that treats NULL and blank/whitespace as unpopulated
        (SQL ``COUNT(col)`` counts ``''`` and ``'   '`` as present, overstating
        coverage/parse rates on real clinical data)."""
        return f"COUNT(NULLIF(LTRIM(RTRIM({self.text_cast(col_quoted)})), ''))"

    def nonblank_distinct(self, col_quoted: str) -> str:
        """DISTINCT count that ignores NULL and blank/whitespace, so blank codes /
        analyte names don't inflate distinct_codes / distinct_analytes."""
        return f"COUNT(DISTINCT NULLIF(LTRIM(RTRIM({self.text_cast(col_quoted)})), ''))"

    def nonblank_predicate(self, col_quoted: str) -> str:
        """A WHERE condition true only for populated (non-NULL, non-blank) values —
        so AVG / GROUP BY exclude '' and whitespace, not just NULL."""
        return f"LTRIM(RTRIM({self.text_cast(col_quoted)})) <> ''"

    def fold(self, col_quoted: str) -> str:
        """The folded patient key: trimmed, lower-cased, and BINARY-collated. Used
        by both the merge ``ORDER BY`` and every scope ``GROUP BY`` / ``DISTINCT``
        so they impose the SAME order/equality — otherwise a case-insensitive server
        collation (the Azure SQL default) would group ``'A'``/``'a'`` while the
        merge splits them (or vice-versa) and the counts disagree with the token
        pass. Binary collation also matches Python's ``str.lower()`` code-point
        order for ASCII hashed / document-number keys.

        The mssql collation is Fabric Warehouse's default (also on SQL Server
        2019+); the legacy ``Latin1_General_BIN2`` is not guaranteed to resolve on
        Fabric, so we use the ``_100_..._UTF8`` variant."""
        d = self.engine.dialect.name
        base = f"LOWER(LTRIM(RTRIM({self.text_cast(col_quoted)})))"
        if d == "mssql":
            return f"{base} COLLATE Latin1_General_100_BIN2_UTF8"
        if d == "postgresql":
            return f'{base} COLLATE "C"'
        if d in ("mysql", "mariadb"):
            # MySQL/MariaDB default to a case-INSENSITIVE collation, which would
            # merge 'A'/'a' while the Python side splits them (or vice-versa) — cast
            # to BINARY to force byte-wise ordering that matches str.lower() order.
            return f"CAST({base} AS BINARY)"
        return base  # sqlite / duckdb / oracle default to BINARY / code-point order

    def pid_order_sql(self, col_quoted: str) -> str:
        """The merge ORDER BY key — the same folded key scope groups by, so SQL and
        Python (str.strip(' ').lower()) impose one consistent total order."""
        return self.fold(col_quoted)

    def safe_real(self, col_quoted: str) -> str:
        """Cast to REAL without erroring on non-numeric text. A bare
        ``CAST(x AS REAL)`` raises on SQL Server / Postgres for a value like
        'unknown' even when a WHERE clause 'should' have filtered it (SQL does not
        guarantee WHERE runs before the projection). Returns NULL for non-numbers,
        which AVG ignores and a band CASE treats as unmatched."""
        d = self.engine.dialect.name
        if d == "mssql":
            return f"TRY_CAST({col_quoted} AS REAL)"
        if d == "postgresql":
            return f"(CASE WHEN {self.numeric_predicate(col_quoted)} THEN CAST({col_quoted} AS REAL) END)"
        if d == "duckdb":
            return f"TRY_CAST({col_quoted} AS DOUBLE)"
        if d in ("mysql", "mariadb"):
            # no TRY_CAST — guard with the numeric predicate so a text value is
            # never cast (MySQL would otherwise coerce it to 0, skewing the mean).
            return f"(CASE WHEN {self.numeric_predicate(col_quoted)} THEN CAST({col_quoted} AS DECIMAL(38,10)) END)"
        if d == "oracle":
            return f"(CASE WHEN {self.numeric_predicate(col_quoted)} THEN CAST({col_quoted} AS BINARY_DOUBLE) END)"
        return f"CAST({col_quoted} AS REAL)"

    def numeric_predicate(self, col_quoted: str) -> str:
        """A WHERE condition true only when the (trimmed) value is a plain number,
        so a text age like 'unknown' or a band like '90+' is not treated as a
        parseable age. Dialect-aware; falls back to non-blank where unsupported."""
        d = self.engine.dialect.name
        t = f"LTRIM(RTRIM({self.text_cast(col_quoted)}))"
        if d == "sqlite":
            return f"({t} GLOB '[0-9]*' AND NOT {t} GLOB '*[^0-9.]*')"
        if d == "mssql":
            # digits/one-dot only: reject 'unknown', '90+', and multi-dot '4.5.6'
            return f"({t} LIKE '[0-9]%' AND {t} NOT LIKE '%[^0-9.]%' AND {t} NOT LIKE '%.%.%')"
        if d == "postgresql":
            return f"({t} ~ '^[0-9]+(\\.[0-9]+)?$')"
        # New branches use a [.] char-class instead of \. so a dialect that
        # processes backslash escapes in string literals (MySQL) can't turn the
        # literal dot into "any char".
        if d in ("mysql", "mariadb"):
            return f"({t} REGEXP '^[0-9]+([.][0-9]+)?$')"
        if d == "oracle":
            return f"REGEXP_LIKE({t}, '^[0-9]+([.][0-9]+)?$')"
        if d == "duckdb":
            return f"regexp_matches({t}, '^[0-9]+([.][0-9]+)?$')"
        return self.nonblank_predicate(col_quoted)

    def qtable(self, table: str) -> str:
        if self.schema:
            return f"{self._prep.quote_schema(self.schema)}.{self._prep.quote(table)}"
        return self._prep.quote(table)

    # --- simple queries -------------------------------------------------- #
    def scalar(self, sql: str) -> Any:
        with self.engine.connect() as c:
            return c.execute(text(sql)).scalar()

    def rows(self, sql: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            res = c.execute(text(sql))
            keys = list(res.keys())
            return [dict(zip(keys, r)) for r in res.fetchall()]

    def columns(self, table: str) -> List[str]:
        from sqlalchemy import inspect
        return [c["name"] for c in inspect(self.engine).get_columns(table, schema=self.schema)]

    def table_row_count(self, sm: StreamMap) -> int:
        where = f" WHERE {sm.where}" if sm.where else ""
        return int(self.scalar(f"SELECT COUNT(*) FROM {self.qtable(sm.table)}{where}") or 0)

    def _check_pool_capacity(self, needed: int) -> None:
        """Fail fast with an actionable message if the caller's engine can't supply
        one connection per mapped stream, rather than a cryptic mid-profile
        QueuePool timeout. Skips silently for unlimited/unknown pools."""
        pool = getattr(self.engine, "pool", None)
        try:
            size = pool.size()
            overflow = pool._max_overflow
        except Exception:
            return  # NullPool / unknown pool — nothing to enforce
        if overflow is not None and overflow < 0:
            return  # unlimited overflow
        cap = size + (overflow or 0)
        if cap and needed > cap:
            raise RuntimeError(
                f"schemascope: profiling needs {needed} concurrent connections (one "
                f"per mapped stream) but the supplied engine's pool allows {cap}. "
                f"Increase pool_size/max_overflow, or create the engine with "
                f"schemascope.connect(), which sizes the pool for you."
            )

    # --- streaming per-patient merge ------------------------------------- #
    def iter_patients(self, mapping: Mapping) -> Iterator[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
        """Yield ``(patient_id, {stream: [raw_row_dict, ...]})`` one patient at a
        time, merged across every present stream.

        The merge compares keys as **lower-cased text** (both in SQL, via a
        binary-collation ``LOWER`` cast, and in Python, via ``str().lower()``), so
        ``ORDER BY`` and ``min(...)`` impose the same total order even when the key
        is a different type across tables or the server collation is
        case-insensitive — and case-variant ids merge to one patient. NULL
        patient_ids are filtered out per stream. Each cursor gets its **own
        connection** (no MARS required), drawn from a dedicated unpooled engine so
        the fan-out — up to one connection per present stream — can never exhaust a
        caller-supplied connection pool.
        """
        present = mapping.present_streams()
        url = str(self.engine.url)
        is_sqlite = url.startswith("sqlite")
        is_memory = is_sqlite and (
            ":memory:" in url or "mode=memory" in url or url in ("sqlite://", "sqlite:///"))
        if is_sqlite and not is_memory:
            # File SQLite (trial runs): a dedicated NullPool engine hands out a
            # fresh connection per cursor, sidestepping the small default pool.
            # SQLite has no auth/custom config to preserve, so recreating is safe.
            merge_engine, own_engine = create_engine(url, poolclass=NullPool), True
        else:
            # Non-SQLite: REUSE the caller's engine so custom connect_args, auth
            # token hooks, event listeners, execution options and creators survive
            # (essential for Azure / Fabric / ODBC). In-memory SQLite: reuse too (a
            # new engine from the URL would be a different, empty database).
            merge_engine, own_engine = self.engine, False
            if not is_sqlite:
                self._check_pool_capacity(len(present))
        cursors: List[_StreamCursor] = []
        try:
            for sm in present:
                pid = sm.pid(mapping.keys)
                cols = _safe_cols(self, sm.table)
                order = [self.pid_order_sql(self.qi(pid))]
                eid = sm.eid(mapping.keys)
                if eid and eid in cols:
                    order.append(self.qi(eid))
                if sm.date_column and sm.date_column in cols:
                    order.append(self.qi(sm.date_column))
                order_sql = ", ".join(order)
                # a patient_id that is NULL or blank/whitespace is not a patient —
                # exclude it so it can't become (or split) a record.
                conds = [self.nonblank_predicate(self.qi(pid))]
                if sm.where:
                    conds.append(f"({sm.where})")
                where = " WHERE " + " AND ".join(conds)
                sql = f"SELECT * FROM {self.qtable(sm.table)}{where} ORDER BY {order_sql}"
                conn = merge_engine.connect().execution_options(stream_results=True)
                cursors.append(_StreamCursor(sm.stream, pid, conn.execute(text(sql)), conn))

            while True:
                active = [c for c in cursors if c.active()]
                if not active:
                    break
                current = min(c.key for c in active)
                # merge on the folded/trimmed key, but display the original id
                # (trimmed of surrounding whitespace, original case) so the profile
                # shows the real patient_id — not a lower-cased or padded copy.
                display = str(next(c.raw for c in active if c.key == current)).strip()
                bucket: Dict[str, List[Dict[str, Any]]] = {}
                for c in cursors:
                    while c.active() and c.key == current:
                        bucket.setdefault(c.stream, []).append(c.take())
                yield str(display), bucket
        finally:
            for c in cursors:
                c.close()
            if own_engine:
                merge_engine.dispose()


def _safe_cols(db: Db, table: str) -> List[str]:
    try:
        return db.columns(table)
    except Exception:
        return []


class _StreamCursor:
    """One ordered result stream (own connection) with a single-row lookahead.

    ``key`` is the lower-cased-text patient_id used for merge comparison; ``raw``
    is the original value (for display). NULLs are excluded by the query, so both
    are non-None while ``active()``.
    """

    def __init__(self, stream: str, pid_col: str, result, conn):
        self.stream = stream
        self._res = result
        self._conn = conn
        self._keys = list(result.keys())
        self._pid_idx = self._keys.index(pid_col) if pid_col in self._keys else 0
        self._row: Optional[Dict[str, Any]] = None
        self.key: Any = None
        self.raw: Any = None
        self._done = False
        self._advance()

    def _advance(self) -> None:
        row = self._res.fetchone()
        if row is None:
            self._row, self.key, self.raw, self._done = None, None, None, True
        else:
            self._row = dict(zip(self._keys, row))
            # compare as space-trimmed, ASCII-lower-cased text to match the SQL
            # LOWER(LTRIM(RTRIM(text))) ordering (see Db.fold). Two subtleties, both
            # to keep SQL and Python's total order identical (else the merge splits a
            # patient): SQL LTRIM/RTRIM strip only the space char (so strip(' '), not
            # str.strip() which also strips tabs/newlines); and SQLite's LOWER folds
            # only ASCII A-Z (so _ascii_lower, not str.lower() which would fold
            # Unicode like Turkish 'İ' that SQL leaves as-is). NULL/blank ids are
            # excluded by the query, so key is never None/empty while active.
            self.raw = row[self._pid_idx]
            self.key = None if self.raw is None else _ascii_lower(str(self.raw).strip(" "))

    def active(self) -> bool:
        return not self._done

    def take(self) -> Dict[str, Any]:
        row = self._row
        self._advance()
        return row

    def close(self) -> None:
        for closer in (self._res, self._conn):
            try:
                closer.close()
            except Exception:
                pass

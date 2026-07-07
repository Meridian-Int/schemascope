"""Data connectors — where the rows come from.

The profiler depends only on the tiny :class:`Connector` protocol, so adding a
source is a matter of implementing four methods. Two connectors ship:

* :class:`CsvConnector` (primary) — a directory of ``<source>.csv`` files, one
  per entity, header row = columns. Pure stdlib, trivially diffable.
* :class:`SqliteConnector` (secondary) — a ``.db``/``.sqlite`` file, one table
  per entity. Also pure stdlib.

Both map a schema ``Field`` to a column *by name* (exact, then case-insensitive
fallback) and read the entity's backing store named ``entity.source or
entity.name``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:  # typing.Protocol is 3.8+, present on our 3.9 floor; guard just in case.
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from .model import ConnectorError, Entity

# CSV cells matching one of these (after stripping) count as null. Defaults to
# just the empty string; callers can opt into "NULL"/"NA" etc.
DEFAULT_NULL_TOKENS = frozenset({""})


@runtime_checkable
class Connector(Protocol):
    """The minimal surface the profiler reads through."""

    def has_entity(self, name: str) -> bool:
        """True if a backing store for ``name`` exists."""

    def columns(self, name: str) -> List[str]:
        """The column names available for entity/store ``name``."""

    def rows(self, name: str) -> Iterator[Dict[str, Any]]:
        """Yield one ``{column: value}`` dict per row, streaming."""

    def close(self) -> None:
        """Release any held resources. Safe to call more than once."""


def _resolve_column(wanted: str, available: List[str]) -> Optional[str]:
    """Match ``wanted`` against ``available``: exact first, then case-insensitive.

    Returns the actual column name to read, or ``None`` if there's no match.
    """
    if wanted in available:
        return wanted
    lower = {c.lower(): c for c in available}
    return lower.get(wanted.lower())


def _check_unique_header(name: str, header: List[str]) -> None:
    """Reject duplicate column names in a CSV header.

    ``csv.DictReader`` silently collapses duplicate columns (keeping the last),
    which would make ``columns()`` and ``rows()`` disagree and drop a column's
    data without warning. Surfacing it as an error keeps the two views consistent
    and flags a genuine data-quality problem instead of hiding it.
    """
    seen = set()
    for col in header:
        if col in seen:
            raise ConnectorError(
                f"CSV file for {name!r} has a duplicate column: {col!r}"
            )
        seen.add(col)


def _quote_sqlite_identifier(name: str) -> str:
    """Return ``name`` quoted as a SQLite identifier.

    SQLite parameters bind values, not table/column identifiers. Any identifier
    interpolated into SQL therefore needs SQL-standard double-quote escaping so
    names like ``select`` or ``weird"name`` remain valid identifiers instead of
    malformed SQL.
    """
    return '"' + name.replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# CSV connector (primary)
# --------------------------------------------------------------------------- #
class CsvConnector:
    """Read a directory of CSV files, one per entity.

    ``path`` is a directory; entity *foo* is expected at ``<path>/foo.csv`` with
    a header row. Empty cells (and any extra ``null_tokens``) are reported as
    ``None`` so the profiler counts them as nulls uniformly with SQLite.
    """

    def __init__(self, path: str | Path, null_tokens=DEFAULT_NULL_TOKENS) -> None:
        self.path = Path(path)
        if not self.path.is_dir():
            raise ConnectorError(f"CSV source is not a directory: {self.path}")
        self.null_tokens = frozenset(null_tokens)

    def _file(self, name: str) -> Path:
        return self.path / f"{name}.csv"

    def has_entity(self, name: str) -> bool:
        return self._file(name).is_file()

    def columns(self, name: str) -> List[str]:
        import csv

        f = self._file(name)
        if not f.is_file():
            return []
        # ``utf-8-sig`` transparently strips a leading BOM (common in Excel /
        # Windows CSV exports) so the first column name isn't mangled to
        # ``"﻿id"`` and thus rendered unresolvable.
        with f.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return []
        _check_unique_header(name, header)
        return header

    def rows(self, name: str) -> Iterator[Dict[str, Any]]:
        import csv

        f = self._file(name)
        if not f.is_file():
            return
        with f.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is not None:
                _check_unique_header(name, list(reader.fieldnames))
            for row in reader:
                # Cells beyond the header (ragged/over-long rows) land in a list
                # under the ``None`` restkey. Drop them rather than crash so
                # imperfect exports profile cleanly.
                row.pop(None, None)
                yield {k: self._nullify(v) for k, v in row.items()}

    def _nullify(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return None if value.strip() in self.null_tokens else value

    def close(self) -> None:
        # Files are opened per-call and closed by their context managers.
        return None


# --------------------------------------------------------------------------- #
# SQLite connector (secondary)
# --------------------------------------------------------------------------- #
class SqliteConnector:
    """Read tables from a SQLite database file, one table per entity."""

    def __init__(self, path: str | Path) -> None:
        import sqlite3

        self.path = Path(path)
        if not self.path.is_file():
            raise ConnectorError(f"SQLite database not found: {self.path}")
        try:
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
            # ``sqlite3.connect`` is lazy and succeeds even on a non-database
            # file; the "file is not a database" error only surfaces on the first
            # real read. Touch ``sqlite_master`` now so a bad file fails here as a
            # ``ConnectorError`` rather than leaking ``sqlite3.DatabaseError`` out
            # of a later query.
            self._conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        except sqlite3.Error as e:
            raise ConnectorError(f"cannot open SQLite database {self.path}: {e}") from e

    def has_entity(self, name: str) -> bool:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return cur.fetchone() is not None

    def columns(self, name: str) -> List[str]:
        import sqlite3

        try:
            cur = self._conn.execute(
                f"PRAGMA table_info({_quote_sqlite_identifier(name)})"
            )
            return [row[1] for row in cur.fetchall()]
        except sqlite3.Error as e:
            raise ConnectorError(f"cannot read SQLite table {name!r}: {e}") from e

    def rows(self, name: str) -> Iterator[Dict[str, Any]]:
        import sqlite3

        try:
            cur = self._conn.execute(
                f"SELECT * FROM {_quote_sqlite_identifier(name)}"
            )
            for row in cur:
                yield {k: row[k] for k in row.keys()}
        except sqlite3.Error as e:
            raise ConnectorError(f"cannot read SQLite table {name!r}: {e}") from e

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


# --------------------------------------------------------------------------- #
# Auto-selection
# --------------------------------------------------------------------------- #
_SQLITE_EXTS = {".db", ".sqlite", ".sqlite3"}


def open_connector(path: str | Path) -> Connector:
    """Pick a connector for ``path``: a directory -> CSV, a ``.db``/``.sqlite``
    file -> SQLite. Anything else raises :class:`ConnectorError`."""
    p = Path(path)
    if p.is_dir():
        return CsvConnector(p)
    if p.is_file() and p.suffix.lower() in _SQLITE_EXTS:
        return SqliteConnector(p)
    raise ConnectorError(
        f"cannot determine a connector for {p!r} "
        "(expected a CSV directory or a .db/.sqlite/.sqlite3 file)"
    )


def store_name(entity: Entity) -> str:
    """The backing table / file stem for an entity: ``source`` or its ``name``."""
    return entity.source or entity.name

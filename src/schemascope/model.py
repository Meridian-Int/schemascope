"""The canonical schema model — one shape for four input formats.

Everything schemascope does downstream (profiling, rendering, the cross-format
equivalence guarantee) is expressed against these three plain dataclasses. A
``Schema`` is a list of ``Entity`` objects; an ``Entity`` is a list of ``Field``
objects; a ``Field`` carries a *canonical* type plus a few flags.

The one subtlety worth knowing up front: ``Field.raw_type`` (the type string
exactly as written in the source, e.g. ``"int"`` vs ``"integer"``) is excluded
from equality. Two schemas parsed from different formats therefore compare equal
whenever their *canonical* fields agree — which is exactly what the keystone
cross-format test asserts with a single ``==``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The closed set of types the profiler reasons about. Every source type string is
# normalized onto one of these; anything unrecognized becomes ``"unknown"``.
CANONICAL_TYPES = (
    "string",
    "integer",
    "float",
    "boolean",
    "date",
    "datetime",
    "unknown",
)

# Vendor/dialect type spelling -> canonical (the mapping documented in the README's
# "Type Names" + "Appendix B" tables). Lookups are lower-cased, have any
# ``(size[,scale])`` / ``(max)`` parameter stripped, and inner whitespace collapsed,
# so ``VARCHAR(255)``, ``numeric(10, 2)``, ``double precision`` and
# ``timestamp with time zone`` all resolve. Grouped by target for auditability.
#
# Exotic types (json/jsonb/xml, arrays, spatial, and binary blob/bytea/varbinary)
# map to ``string``: read from the database they arrive as serialized/hex/base64
# text and infer ``string``, so a declared ``string`` accepts them. Ambiguous
# numerics (``number``, ``numeric``, ``money``) map to ``float`` — the safe
# superset, since integer data is compatible with a declared float but not the
# reverse. Only genuinely uninterpretable spellings fall through to ``"unknown"``.
def _build_aliases() -> Dict[str, str]:
    groups: Dict[str, List[str]] = {
        "integer": [
            "int", "integer", "int2", "int4", "int8", "int64", "bigint",
            "smallint", "tinyint", "mediumint", "byteint", "long",
            "serial", "smallserial", "bigserial", "varint", "counter",
        ],
        "float": [
            "float", "float4", "float8", "float64", "double", "double precision",
            "real", "decimal", "decimal128", "dec", "numeric", "number",
            "bignumeric", "fixed", "money", "smallmoney",
        ],
        "string": [
            "str", "string", "char", "character", "nchar", "bpchar", "varchar",
            "varchar2", "nvarchar", "nvarchar2", "character varying",
            "national character varying", "text", "ntext", "tinytext",
            "mediumtext", "longtext", "clob", "nclob", "citext", "name",
            "uuid", "guid", "uniqueidentifier", "enum", "set",
            "json", "jsonb", "xml", "hstore", "variant", "object", "array",
            "struct", "map",
            "bytea", "blob", "binary", "varbinary", "bytes", "image",
            "time", "timetz", "time with time zone", "time without time zone",
            "interval", "year", "inet", "cidr", "macaddr",
            "geometry", "geography", "ip", "objectid", "rowid", "urowid",
        ],
        "boolean": ["bool", "boolean", "bit"],
        "date": ["date"],
        "datetime": [
            "datetime", "datetime2", "smalldatetime", "datetimeoffset",
            "timestamp", "timestamptz", "timestamp with time zone",
            "timestamp without time zone", "timestamp with local time zone",
            "timestamp_ntz", "timestamp_ltz", "timestamp_tz",
        ],
    }
    table: Dict[str, str] = {}
    for canon, names in groups.items():
        for n in names:
            table[n] = canon
    return table


_TYPE_ALIASES: Dict[str, str] = _build_aliases()

# A trailing size/precision parameter, e.g. "(255)", "(10, 2)", "(max)".
_TYPE_PARAM = re.compile(r"\(.*", re.DOTALL)
_WS = re.compile(r"\s+")


def normalize_type(raw: Optional[str]) -> str:
    """Map a source type string onto one of :data:`CANONICAL_TYPES`.

    Vendor-aware: a ``(size[,scale])`` / ``(max)`` parameter is stripped and inner
    whitespace collapsed before lookup, so ``VARCHAR(255)``, ``numeric(10, 2)``,
    ``timestamp with time zone`` and ``double precision`` all resolve.

    Empty / ``None`` / any non-string value -> ``"unknown"``. Genuinely unmodeled
    types (binary, spatial, arrays, composites) and unrecognized names also become
    ``"unknown"`` — the raw string is kept on the ``Field`` so nothing is lost. A
    non-string ``type`` (e.g. ``123`` from malformed JSON/YAML) does not crash.
    """
    if not isinstance(raw, str):
        return "unknown"
    key = _TYPE_PARAM.sub("", raw).strip().lower()   # drop "(255)" / "(10,2)" / "(max)"
    key = _WS.sub(" ", key)                           # "double  precision" -> "double precision"
    if not key:
        return "unknown"
    return _TYPE_ALIASES.get(key, "unknown")


@dataclass
class Field:
    """One scalar column of an entity.

    ``type`` is always canonical. ``raw_type`` is the original spelling and is
    excluded from ``==`` so cross-format equality holds. By convention a primary
    key is not nullable unless a parser was told otherwise explicitly.
    """

    name: str
    type: str = "unknown"
    nullable: bool = True
    primary_key: bool = False
    description: Optional[str] = None
    # As written in the source ("int", "VARCHAR", ...). Ignored by equality.
    raw_type: Optional[str] = field(default=None, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serializable view (used by ``Schema.to_dict`` / the ``schemascope`` CLI)."""
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "description": self.description,
        }


@dataclass
class Entity:
    """A named collection of fields, backed by one database table."""

    name: str
    fields: List[Field] = field(default_factory=list)
    # Backing table / file stem. Defaults to ``name`` at profile time when None.
    source: Optional[str] = None
    description: Optional[str] = None

    def field_by_name(self, name: str) -> Optional[Field]:
        """Return the field named ``name`` (exact match), or ``None``."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def primary_keys(self) -> List[Field]:
        """The subset of fields flagged ``primary_key``."""
        return [f for f in self.fields if f.primary_key]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "description": self.description,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class Schema:
    """The whole normalized schema: an ordered list of entities plus metadata."""

    entities: List[Entity] = field(default_factory=list)
    name: Optional[str] = None
    version: Optional[str] = None

    def entity(self, name: str) -> Optional[Entity]:
        """Return the entity named ``name`` (exact match), or ``None``."""
        for e in self.entities:
            if e.name == name:
                return e
        return None

    def to_dict(self) -> Dict[str, Any]:
        """A plain, order-preserving dict suitable for JSON/YAML round-trips."""
        return {
            "name": self.name,
            "version": self.version,
            "entities": [e.to_dict() for e in self.entities],
        }


class SchemaError(ValueError):
    """Raised when schema input is malformed or fails validation."""


class ConnectorError(RuntimeError):
    """Raised when a data source can't be opened or read."""

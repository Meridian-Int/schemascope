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

# alias -> canonical. Lower-cased, stripped lookups only. Unknown aliases fall
# through to ``"unknown"`` (the raw string is preserved on the Field so nothing
# is lost). Extend the tool by editing this table — deliberately not pluggable.
_TYPE_ALIASES: Dict[str, str] = {
    "str": "string", "string": "string", "text": "string", "varchar": "string",
    "char": "string", "uuid": "string", "enum": "string",
    "int": "integer", "integer": "integer", "bigint": "integer",
    "smallint": "integer", "long": "integer",
    "float": "float", "double": "float", "decimal": "float", "numeric": "float",
    "real": "float", "number": "float",
    "bool": "boolean", "boolean": "boolean",
    "date": "date",
    "datetime": "datetime", "timestamp": "datetime",
}


def normalize_type(raw: Optional[str]) -> str:
    """Map a source type string onto one of :data:`CANONICAL_TYPES`.

    Empty / ``None`` / any non-string value -> ``"unknown"``. Lookup is case- and
    whitespace-insensitive. Unrecognized names also become ``"unknown"`` (callers
    keep the raw string on the ``Field`` for reporting). A non-string ``type``
    (e.g. ``123`` or ``true`` from malformed JSON/YAML) normalizes to
    ``"unknown"`` rather than crashing.
    """
    if not isinstance(raw, str):
        return "unknown"
    key = raw.strip().lower()
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
    """A named collection of fields, backed by one table / CSV file."""

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

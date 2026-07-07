"""Profile a data source against a schema.

This is the module the rest of the package exists to serve: given a normalized
:class:`~schemascope.model.Schema` and a :class:`~schemascope.connector.Connector`,
walk each entity's backing store once and compute, per field:

* ``row_count`` — rows scanned for the entity;
* ``null_count`` / ``null_fraction`` — how many values were null;
* ``distinct_count`` — number of distinct non-null values;
* ``inferred_type`` — the canonical type the *data* implies
  (see :func:`schemascope.typeinfer.infer_type`);
* ``type_ok`` — whether that inferred type is compatible with the field's
  *declared* type (:func:`schemascope.typeinfer.type_compatible`).

Fields whose column is absent from the source, and entities whose backing store
is missing entirely, are reported (``present=False``) rather than skipped, so a
drifted schema shows up in the profile instead of silently disappearing.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional

from .connector import Connector, _resolve_column, store_name
from .model import Entity, Schema
from .typeinfer import TypeInferer, type_compatible


@dataclass
class FieldProfile:
    """Per-field metrics plus the declared-vs-inferred type verdict."""

    name: str
    declared_type: str
    column: Optional[str]  # resolved source column, or None if the field is absent
    present: bool
    row_count: int = 0
    null_count: int = 0
    distinct_count: int = 0
    inferred_type: str = "unknown"
    type_ok: bool = True

    @property
    def null_fraction(self) -> float:
        return self.null_count / self.row_count if self.row_count else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "declared_type": self.declared_type,
            "column": self.column,
            "present": self.present,
            "row_count": self.row_count,
            "null_count": self.null_count,
            "null_fraction": round(self.null_fraction, 6),
            "distinct_count": self.distinct_count,
            "inferred_type": self.inferred_type,
            "type_ok": self.type_ok,
        }


@dataclass
class EntityProfile:
    """One entity's profile: its rows and a :class:`FieldProfile` per field."""

    name: str
    source: str
    present: bool
    row_count: int = 0
    fields: List[FieldProfile] = _dc_field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "present": self.present,
            "row_count": self.row_count,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class SchemaProfile:
    """The whole profile: one :class:`EntityProfile` per schema entity."""

    entities: List[EntityProfile] = _dc_field(default_factory=list)

    def entity(self, name: str) -> Optional[EntityProfile]:
        for e in self.entities:
            if e.name == name:
                return e
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"entities": [e.to_dict() for e in self.entities]}


def profile(schema: Schema, connector: Connector) -> SchemaProfile:
    """Profile every entity in ``schema`` against ``connector``.

    The connector is read but not closed — the caller owns its lifecycle.
    """
    return SchemaProfile(
        entities=[_profile_entity(entity, connector) for entity in schema.entities]
    )


def _profile_entity(entity: Entity, connector: Connector) -> EntityProfile:
    source = store_name(entity)

    if not connector.has_entity(source):
        return EntityProfile(
            name=entity.name,
            source=source,
            present=False,
            fields=[
                FieldProfile(
                    name=f.name, declared_type=f.type, column=None, present=False
                )
                for f in entity.fields
            ],
        )

    available = connector.columns(source)
    resolved = {f.name: _resolve_column(f.name, available) for f in entity.fields}

    row_count = 0
    nulls: Dict[str, int] = {f.name: 0 for f in entity.fields}
    distinct: Dict[str, set] = {f.name: set() for f in entity.fields}
    # Infer each field's type incrementally over EVERY non-null value in this one
    # pass (O(1) memory per field), so drift anywhere in the file is caught — not
    # just in the first N rows.
    inferers: Dict[str, TypeInferer] = {f.name: TypeInferer() for f in entity.fields}

    for row in connector.rows(source):
        row_count += 1
        for f in entity.fields:
            col = resolved[f.name]
            if col is None:
                continue
            value = row.get(col)
            if value is None:
                nulls[f.name] += 1
                continue
            distinct[f.name].add(value)
            inferers[f.name].add(value)

    field_profiles: List[FieldProfile] = []
    for f in entity.fields:
        col = resolved[f.name]
        present = col is not None
        inferred = inferers[f.name].result() if present else "unknown"
        field_profiles.append(
            FieldProfile(
                name=f.name,
                declared_type=f.type,
                column=col,
                present=present,
                row_count=row_count if present else 0,
                null_count=nulls[f.name] if present else 0,
                distinct_count=len(distinct[f.name]) if present else 0,
                inferred_type=inferred,
                type_ok=type_compatible(f.type, inferred) if present else True,
            )
        )

    return EntityProfile(
        name=entity.name,
        source=source,
        present=True,
        row_count=row_count,
        fields=field_profiles,
    )

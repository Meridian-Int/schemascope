"""Load a schema from JSON / YAML / XML / TXT-DSL into one :class:`Schema`.

This module is the heart of schemascope: four input formats, one output model.
JSON and YAML deserialize to the same Python mapping, so both go through
:func:`_from_mapping`; XML and the line-oriented TXT DSL get one parser each.
Whatever the source, the result is validated (at least one entity, each entity
has at least one field, names are unique) and the primary-key-implies-not-null
convention is applied — so equivalent declarations in any format normalize to an
*identical* ``Schema``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import Entity, Field, Schema, SchemaError, normalize_type

# Extension -> format. Authoritative when the file has one of these suffixes.
_EXT_FORMAT = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".txt": "txt",
    ".dsl": "txt",
    ".schema": "txt",
}

_TRUE_TOKENS = {"true", "1", "yes", "y", "t"}
_FALSE_TOKENS = {"false", "0", "no", "n", "f"}


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce an attribute/flag to bool. Real bools pass through; strings are
    matched case-insensitively against the true/false token sets; anything
    unrecognized falls back to ``default``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    return default


# --------------------------------------------------------------------------- #
# Format detection
# --------------------------------------------------------------------------- #
def detect_format(path: str | Path, content: Optional[str] = None) -> str:
    """Return ``"json" | "yaml" | "xml" | "txt"`` for ``path``.

    Extension wins when recognized. Otherwise the content is sniffed: a leading
    ``<`` is XML; a leading ``{`` or ``[`` is JSON; text that ``yaml.safe_load``s
    to a mapping *with an ``entities`` key* is YAML; everything else is the TXT
    DSL.

    The ``entities`` guard matters because a lot of DSL is also syntactically
    valid YAML — ``id: int`` loads as ``{"id": "int"}`` and ``entity: users``
    loads as ``{"entity": "users"}``. Only a real schema mapping carries the
    ``entities`` key, so keying on it stops such DSL from being mis-sniffed as
    YAML (and then failing with a misleading "missing 'entities' key"). Give the
    file a ``.txt``/``.dsl``/``.schema`` (or ``.yaml``) extension to skip
    sniffing entirely.
    """
    ext = Path(path).suffix.lower()
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext]

    if content is None:
        content = Path(path).read_text(encoding="utf-8")
    stripped = content.strip()
    if not stripped:
        raise SchemaError(f"{path}: empty schema file")
    if stripped[0] == "<":
        return "xml"
    if stripped[0] in "{[":
        return "json"
    try:
        import yaml

        loaded = yaml.safe_load(content)
        if isinstance(loaded, dict) and "entities" in loaded:
            return "yaml"
    except Exception:
        pass
    return "txt"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def load_schema(path: str | Path, fmt: Optional[str] = None) -> Schema:
    """Read, detect (unless ``fmt`` given), parse, validate. Returns a Schema.

    Raises :class:`SchemaError` on malformed input or a failed validation.
    """
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SchemaError(f"cannot read schema file {path}: {e}") from e

    fmt = fmt or detect_format(path, content)
    parsers = {
        "json": _from_json,
        "yaml": _from_yaml,
        "xml": _from_xml,
        "txt": _from_txt,
    }
    if fmt not in parsers:
        raise SchemaError(f"unknown schema format: {fmt!r}")

    schema = parsers[fmt](content)
    _validate(schema)
    return schema


# --------------------------------------------------------------------------- #
# Mapping-based parsers (JSON + YAML share one code path)
# --------------------------------------------------------------------------- #
def _from_json(text: str) -> Schema:
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SchemaError(f"invalid JSON schema: {e}") from e
    return _from_mapping(data)


def _from_yaml(text: str) -> Schema:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SchemaError(f"invalid YAML schema: {e}") from e
    return _from_mapping(data)


def _from_mapping(data: Any) -> Schema:
    """Build a Schema from the canonical wire mapping shared by JSON and YAML::

        {name?, version?, entities: [
            {name, source?, description?, fields: [
                {name, type?, nullable?, primary_key?, description?}, ...]}, ...]}
    """
    if not isinstance(data, dict):
        raise SchemaError("schema root must be a mapping/object")

    raw_entities = data.get("entities")
    if raw_entities is None:
        raise SchemaError("schema is missing the 'entities' key")
    if not isinstance(raw_entities, list):
        raise SchemaError("'entities' must be a list")

    entities: List[Entity] = []
    for raw_e in raw_entities:
        if not isinstance(raw_e, dict):
            raise SchemaError("each entity must be a mapping/object")
        name = raw_e.get("name")
        if not name:
            raise SchemaError("entity is missing a 'name'")

        raw_fields = raw_e.get("fields") or []
        if not isinstance(raw_fields, list):
            raise SchemaError(f"entity {name!r}: 'fields' must be a list")

        fields = [_field_from_mapping(name, rf) for rf in raw_fields]
        entities.append(
            Entity(
                name=str(name),
                fields=fields,
                source=_opt_str(raw_e.get("source")),
                description=_opt_str(raw_e.get("description")),
            )
        )

    return Schema(
        entities=entities,
        name=_opt_str(data.get("name")),
        version=_opt_str(data.get("version")),
    )


def _field_from_mapping(entity_name: str, raw: Any) -> Field:
    if not isinstance(raw, dict):
        raise SchemaError(f"entity {entity_name!r}: each field must be a mapping")
    fname = raw.get("name")
    if not fname:
        raise SchemaError(f"entity {entity_name!r}: a field is missing its 'name'")

    raw_type = raw.get("type")
    primary_key = _as_bool(raw.get("primary_key"), default=False)
    # pk implies not-null unless 'nullable' was stated explicitly.
    if "nullable" in raw:
        nullable = _as_bool(raw.get("nullable"), default=True)
    else:
        nullable = not primary_key

    return Field(
        name=str(fname),
        type=normalize_type(raw_type),
        nullable=nullable,
        primary_key=primary_key,
        description=_opt_str(raw.get("description")),
        raw_type=_opt_str(raw_type),
    )


# --------------------------------------------------------------------------- #
# XML parser
# --------------------------------------------------------------------------- #
def _local_name(tag: str) -> str:
    """Strip an XML namespace from a tag: ``{uri}entity`` -> ``entity``.

    ``ElementTree`` reports namespaced tags in Clark notation, so a document with
    a default ``xmlns`` would otherwise never match ``schema``/``entity``/
    ``field``. Ignoring the namespace lets real-world namespaced schema documents
    parse identically to their bare equivalents.
    """
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else tag


def _children_named(el, name: str) -> List[Any]:
    """Direct children of ``el`` whose local (namespace-stripped) tag is ``name``."""
    return [child for child in el if _local_name(child.tag) == name]


def _from_xml(text: str) -> Schema:
    """Parse ``<schema><entity><field/></entity></schema>`` into a Schema.

    Namespaces are ignored (a default ``xmlns`` is fine); the format is
    attribute-based (``<field name="id" type="int"/>``).
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise SchemaError(f"invalid XML schema: {e}") from e

    if _local_name(root.tag) != "schema":
        raise SchemaError(f"XML root must be <schema>, got <{root.tag}>")

    entities: List[Entity] = []
    for e_el in _children_named(root, "entity"):
        name = e_el.get("name")
        if not name:
            raise SchemaError("XML <entity> is missing a 'name' attribute")

        fields: List[Field] = []
        for f_el in _children_named(e_el, "field"):
            fname = f_el.get("name")
            if not fname:
                raise SchemaError(
                    f"entity {name!r}: XML <field> is missing a 'name' attribute"
                )
            raw_type = f_el.get("type")
            primary_key = _as_bool(f_el.get("primary_key"), default=False)
            if f_el.get("nullable") is not None:
                nullable = _as_bool(f_el.get("nullable"), default=True)
            else:
                nullable = not primary_key
            fields.append(
                Field(
                    name=fname,
                    type=normalize_type(raw_type),
                    nullable=nullable,
                    primary_key=primary_key,
                    description=_opt_str(f_el.get("description")),
                    raw_type=_opt_str(raw_type),
                )
            )

        entities.append(
            Entity(
                name=name,
                fields=fields,
                source=_opt_str(e_el.get("source")),
                description=_opt_str(e_el.get("description")),
            )
        )

    return Schema(
        entities=entities,
        name=_opt_str(root.get("name")),
        version=_opt_str(root.get("version")),
    )


# --------------------------------------------------------------------------- #
# TXT DSL parser
# --------------------------------------------------------------------------- #
# Field flags, checked case-insensitively. "not null" is scanned before "null"
# so the substring match never mis-fires (see _parse_field_flags).
_NOTNULL_FLAGS = ("not null", "notnull", "required")
_NULL_FLAGS = ("nullable", "null")
_PK_FLAGS = ("primary_key", "primary key", "pk")


def _from_txt(text: str) -> Schema:
    """Parse the line-oriented DSL::

        # comment
        entity <name>[:]
          <field>: <type> [flag ...]

    Flags (case-insensitive, order-free): ``pk``/``primary_key``,
    ``not null``/``notnull``/``required``, ``null``/``nullable``, ``unique``
    (accepted, ignored in the MVP). Blank lines and ``#`` comments are ignored;
    indentation is cosmetic.
    """
    entities: List[Entity] = []
    current: Optional[Entity] = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        low = line.lower()
        if low == "entity" or low.startswith("entity ") or low.startswith("entity\t"):
            ename = line[len("entity"):].strip().rstrip(":").strip()
            if not ename:
                raise SchemaError(f"line {lineno}: 'entity' with no name")
            current = Entity(name=ename, fields=[])
            entities.append(current)
            continue

        # Otherwise this must be a field line: "<name>: <type> [flags]".
        if current is None:
            raise SchemaError(
                f"line {lineno}: field {line!r} appears before any 'entity'"
            )
        if ":" not in line:
            raise SchemaError(
                f"line {lineno}: expected '<field>: <type>', got {line!r}"
            )
        fname, rest = line.split(":", 1)
        fname = fname.strip()
        if not fname:
            raise SchemaError(f"line {lineno}: field line with no name")
        current.fields.append(_field_from_dsl(fname, rest.strip()))

    return Schema(entities=entities)


def _field_from_dsl(name: str, rest: str) -> Field:
    """``rest`` is the text after ``name:`` — a type token then optional flags."""
    parts = rest.split(None, 1)
    raw_type = parts[0] if parts else None
    flag_text = parts[1] if len(parts) > 1 else ""
    primary_key, nullable_explicit, nullable = _parse_field_flags(flag_text)

    if nullable_explicit is not None:
        nullable = nullable_explicit
    else:
        nullable = not primary_key

    return Field(
        name=name,
        type=normalize_type(raw_type),
        nullable=nullable,
        primary_key=primary_key,
        raw_type=_opt_str(raw_type),
    )


def _parse_field_flags(flag_text: str):
    """Return ``(primary_key, nullable_explicit, nullable_default)``.

    ``nullable_explicit`` is ``True``/``False`` when the line stated nullability
    outright, else ``None`` (so the pk rule decides). "not null" is checked
    before "null" so the shorter token can't shadow it.
    """
    text = " " + flag_text.lower().strip() + " "
    primary_key = any(f" {flag} " in text or text.strip() == flag for flag in _PK_FLAGS)

    nullable_explicit: Optional[bool] = None
    if any(nf in text for nf in _NOTNULL_FLAGS):
        nullable_explicit = False
    elif any(f" {nf} " in text for nf in _NULL_FLAGS):
        nullable_explicit = True

    return primary_key, nullable_explicit, True


# --------------------------------------------------------------------------- #
# Validation + helpers
# --------------------------------------------------------------------------- #
def _validate(schema: Schema) -> None:
    """Enforce the structural invariants every parser must satisfy."""
    if not schema.entities:
        raise SchemaError("schema defines no entities")

    seen_entities: Dict[str, bool] = {}
    for e in schema.entities:
        if not e.name:
            raise SchemaError("an entity has an empty name")
        if e.name in seen_entities:
            raise SchemaError(f"duplicate entity name: {e.name!r}")
        seen_entities[e.name] = True

        if not e.fields:
            raise SchemaError(f"entity {e.name!r} has no fields")

        seen_fields: Dict[str, bool] = {}
        for f in e.fields:
            if not f.name:
                raise SchemaError(f"entity {e.name!r} has a field with an empty name")
            if f.name in seen_fields:
                raise SchemaError(
                    f"entity {e.name!r}: duplicate field name: {f.name!r}"
                )
            seen_fields[f.name] = True


def _opt_str(value: Any) -> Optional[str]:
    """Normalize an optional scalar to ``str`` (or ``None``)."""
    if value is None:
        return None
    return str(value)

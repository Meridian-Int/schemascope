"""schemascope — profile a data source against a schema.

Load a schema from any of four formats (JSON / YAML / XML / TXT-DSL) into one
canonical model, point a connector at a data source (a directory of CSVs, a
SQLite file, or a live database via a SQLAlchemy URL), and profile the data:
per-field null counts, distinct counts, the
type inferred from the values, and whether that data agrees with the declared
schema type.

The public API is re-exported here so the natural top-level calls just work::

    import schemascope
    schema = schemascope.load_schema("schema.yaml")
    report = schemascope.profile(schema, schemascope.open_connector("data/"))
    schemascope.__version__
"""

from __future__ import annotations

from .connector import (
    Connector,
    CsvConnector,
    SqlConnector,
    SqliteConnector,
    open_connector,
    store_name,
)
from .model import (
    CANONICAL_TYPES,
    ConnectorError,
    Entity,
    Field,
    Schema,
    SchemaError,
    normalize_type,
)
from .profile import (
    EntityProfile,
    FieldProfile,
    SchemaProfile,
    profile,
)
from .schema_loader import detect_format, load_schema
from .typeinfer import infer_type, type_compatible
from .version import __version__

__all__ = [
    "__version__",
    # model
    "Schema",
    "Entity",
    "Field",
    "SchemaError",
    "ConnectorError",
    "CANONICAL_TYPES",
    "normalize_type",
    # loading
    "load_schema",
    "detect_format",
    # connectors
    "Connector",
    "CsvConnector",
    "SqliteConnector",
    "SqlConnector",
    "open_connector",
    "store_name",
    # inference
    "infer_type",
    "type_compatible",
    # profiling
    "profile",
    "SchemaProfile",
    "EntityProfile",
    "FieldProfile",
]

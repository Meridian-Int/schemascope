"""The public package surface: top-level API and version are importable.

Regression for the QA finding that ``import schemascope; schemascope.load_schema``
and ``schemascope.__version__`` raised ``AttributeError`` (no ``__init__.py``).
"""

import re

import schemascope


def test_version_surfaced_at_package_level():
    # The regression this guards is ``__version__`` raising AttributeError, not a
    # specific value — assert it is a real, non-empty version string so a release
    # bump never breaks the test.
    assert isinstance(schemascope.__version__, str)
    assert re.match(r"^\d+\.\d+", schemascope.__version__), schemascope.__version__


def test_public_api_is_importable_from_top_level():
    for attr in (
        "load_schema",
        "detect_format",
        "profile",
        "open_connector",
        "Schema",
        "Entity",
        "Field",
        "SchemaError",
        "ConnectorError",
        "infer_type",
        "type_compatible",
    ):
        assert hasattr(schemascope, attr), attr

    assert callable(schemascope.load_schema)
    assert callable(schemascope.profile)

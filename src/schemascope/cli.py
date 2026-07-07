"""The ``schemascope`` command-line interface.

Usage::

    schemascope SCHEMA DATA [--output json|yaml] [--schema-format FMT]

Loads ``SCHEMA`` (JSON/YAML/XML/TXT, auto-detected), opens ``DATA`` (a directory
of CSVs or a ``.db``/``.sqlite`` file), profiles the data against the schema, and
writes the report to stdout as JSON (default) or YAML.

Exit codes: ``0`` success, ``2`` a schema or data-source error (message on
stderr), plus argparse's own ``2`` for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .model import ConnectorError, SchemaError
from .version import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schemascope",
        description="Profile a data source against a schema.",
    )
    parser.add_argument(
        "schema", help="path to a JSON/YAML/XML/TXT schema file"
    )
    parser.add_argument(
        "data", help="data source: a directory of CSVs or a .db/.sqlite file"
    )
    parser.add_argument(
        "--schema-format",
        default=None,
        choices=("json", "yaml", "xml", "txt"),
        help="override schema format auto-detection",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="json",
        choices=("json", "yaml"),
        help="report output format (default: json)",
    )
    parser.add_argument(
        "--version", action="version", version=f"schemascope {__version__}"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Imported lazily so ``--help`` / ``--version`` stay fast and don't pull in
    # the connector/profiler stack.
    from .connector import open_connector
    from .profile import profile
    from .schema_loader import load_schema

    args = _build_parser().parse_args(argv)

    try:
        schema = load_schema(args.schema, fmt=args.schema_format)
    except SchemaError as e:
        print(f"schema error: {e}", file=sys.stderr)
        return 2

    connector = None
    try:
        connector = open_connector(args.data)
        report = profile(schema, connector).to_dict()
    except ConnectorError as e:
        print(f"data source error: {e}", file=sys.stderr)
        return 2
    finally:
        if connector is not None:
            connector.close()

    if args.output == "yaml":
        import yaml

        sys.stdout.write(yaml.safe_dump(report, sort_keys=False))
    else:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

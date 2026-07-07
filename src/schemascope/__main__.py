"""Enable ``python -m schemascope`` as an alias for the ``schemascope`` CLI."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

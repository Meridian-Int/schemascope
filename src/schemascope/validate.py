"""Validate a profile against the bundled intake JSON schema (04b)."""

from __future__ import annotations

from typing import Any, Dict, List

from jsonschema import Draft202012Validator, FormatChecker

from .model import is_profile_date
from .render import load_schema

# Enforce the schema's `format: date` fields (jsonschema ignores `format` unless a
# checker is supplied). Uses the same lenient-but-real rule as the profiler, so
# full dates and HIPAA year-only both pass but "not-a-date" is rejected.
_FORMATS = FormatChecker()
_FORMATS.checks("date")(is_profile_date)


def validate(profile: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable schema errors; empty means valid."""
    validator = Draft202012Validator(load_schema(), format_checker=_FORMATS)
    errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path))
    out: List[str] = []
    for e in errors:
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append(f"{loc}: {e.message}")
    return out

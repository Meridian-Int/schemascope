"""Load the bundled intake contract and write the profile out as YAML + JSON."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

import yaml

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_SCHEMA = os.path.join(_DATA_DIR, "corpus_schema.json")


def load_schema() -> Dict[str, Any]:
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def render_json(profile: Dict[str, Any]) -> str:
    # allow_nan=False guarantees valid RFC-8259 JSON — a stray nan/inf raises here
    # instead of emitting a NaN/Infinity token that strict parsers reject. Part B
    # numerics are already coerced finite, so this only ever fires as a backstop.
    return json.dumps(profile, ensure_ascii=False, indent=2, default=str, allow_nan=False)


def render_yaml(profile: Dict[str, Any]) -> str:
    return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, default_flow_style=False)


def write_json(profile: Dict[str, Any], path: str) -> None:
    _atomic_write(path, render_json(profile))


def write_yaml(profile: Dict[str, Any], path: str) -> None:
    _atomic_write(path, render_yaml(profile))


def _atomic_write(path: str, data: str) -> None:
    """Write via a temp file + os.replace so a reader never sees a half-written
    file, and a failure leaves the target untouched."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_outputs(profile: Dict[str, Any], yaml_path: Optional[str],
                  json_path: Optional[str]) -> List[str]:
    """Write the requested deliverables all-or-nothing: both are serialized first
    (so a rendering error touches no file), then written atomically; if any write
    fails, files written in this call are rolled back so no partial output remains."""
    outputs = []
    if yaml_path:
        outputs.append((yaml_path, render_yaml(profile)))
    if json_path:
        outputs.append((json_path, render_json(profile)))
    written: List[str] = []
    try:
        for path, data in outputs:
            _atomic_write(path, data)
            written.append(path)
    except BaseException:
        for p in written:
            try:
                os.remove(p)
            except OSError:
                pass
        raise
    return written

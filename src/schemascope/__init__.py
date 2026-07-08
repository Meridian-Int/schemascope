"""schemascope — profile a clinical EMR database into the Meridian corpus intake profile.

Reads a whole SQL database (the same source deidkit reads), counts tokens exactly
with tiktoken (full-record vs clinical-content, dual encoder), computes the A1–A12
scope metrics and one worked patient, and emits a YAML/JSON deliverable that
validates against the intake schema.

    import schemascope as cs

    db = cs.Db(cs.connect(url))
    mapping = cs.Mapping.from_yaml("mapping.yaml")
    profile = cs.build_profile(db, mapping)
    issues = cs.run_qa(profile)          # QA gates
    cs.write_yaml(profile, "profile.yaml")
"""

from .version import __version__
from .io import Db, connect
from .mapping import Mapping, StreamMap, Keys, autodetect
from .profile import build_profile
from .qa import run_qa, errors, Issue
from .validate import validate
from .render import load_schema, write_json, write_yaml

__all__ = [
    "__version__",
    "Db", "connect",
    "Mapping", "StreamMap", "Keys", "autodetect",
    "build_profile",
    "run_qa", "errors", "Issue",
    "validate",
    "load_schema", "write_json", "write_yaml",
]

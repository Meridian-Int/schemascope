"""Command-line interface.

    schemascope autodetect --source <url> --out mapping.yaml
    schemascope profile    --source <url> --mapping mapping.yaml \
                           --out-yaml profile.yaml --out-json profile.json
    schemascope validate   --json profile.json

`profile` builds the corpus profile, runs the QA gates, validates against the
intake schema, and exits non-zero if any QA error is found.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .version import __version__


def _connect(url: str, schema):
    from .io import Db, connect
    return Db(connect(url), schema=schema)


def cmd_autodetect(args) -> int:
    from .mapping import autodetect
    db = _connect(args.source, args.schema)
    mapping = autodetect(db.engine, schema=args.schema,
                         patient_id=args.patient_id, encounter_id=args.encounter_id)
    mapping.to_yaml(args.out)
    print(f"Proposed mapping -> {args.out}")
    print("REVIEW IT before profiling: confirm each stream's table/columns and "
          "which streams are present:false.")
    return 0


def cmd_profile(args) -> int:
    from .mapping import Mapping
    from .profile import build_profile
    from .qa import errors, run_qa
    from .render import write_outputs

    db = _connect(args.source, args.schema)
    mapping = Mapping.from_yaml(args.mapping)
    if args.schema and not mapping.schema:
        mapping.schema = args.schema

    print("Profiling (exact token pass + scope aggregates)…", file=sys.stderr)
    try:
        profile = build_profile(db, mapping)
    except ValueError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    issues = run_qa(profile)
    _print_qa(issues)

    scale = profile["scale"]
    print(f"\n  patients   : {scale.get('patients_total'):,}"
          if scale.get('patients_total') is not None else "  patients   : n/a")
    if scale.get("total_tokens") is not None:
        print(f"  tokens     : {scale['total_tokens']:,} full  |  "
              f"{scale.get('clinical_content_tokens', 0):,} clinical "
              f"({scale.get('clinical_content_pct')}%)  [{scale.get('tokeniser')}]")

    # Hard gate: a profile that fails QA is never written — the README promises the
    # gates run "before anything is written", so a failed run leaves no deliverable
    # that could be mistaken for a valid one.
    if errors(issues):
        print("\nQA FAILED — no profile written.", file=sys.stderr)
        return 1

    # All-or-nothing: both deliverables are written atomically, so a bad path can't
    # leave a partial/stale file, and the failure is a clean message (not a traceback).
    try:
        written = write_outputs(profile, args.out_yaml, args.out_json)
    except (OSError, ValueError) as e:
        print(f"\nERROR writing output: {e} — no profile written.", file=sys.stderr)
        return 1
    for p in written:
        print(f"  wrote -> {p}")
    return 0


def cmd_validate(args) -> int:
    from .validate import validate
    with open(args.json, "r", encoding="utf-8") as fh:
        profile = json.load(fh)
    errs = validate(profile)
    if errs:
        print("INVALID:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("valid against the corpus schema.")
    return 0


def _print_qa(issues: List) -> None:
    errs = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level == "warning"]
    print(f"\nQA: {len(errs)} error(s), {len(warns)} warning(s)")
    for i in errs:
        print(f"  ERROR   [{i.check}] {i.message}")
    for i in warns:
        print(f"  warning [{i.check}] {i.message}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="schemascope",
                                description="Profile a clinical EMR database into a portable, schema-validated corpus profile.")
    p.add_argument("--version", action="version", version=f"schemascope {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("autodetect", help="Propose a schema mapping from a live DB.")
    pa.add_argument("--source", required=True, help="Source DB SQLAlchemy URL.")
    pa.add_argument("--out", required=True, help="Write the proposed mapping YAML here.")
    pa.add_argument("--schema", help="DB schema/namespace (e.g. dbo).")
    pa.add_argument("--patient-id", default="patient_id", help="Patient key column (default patient_id).")
    pa.add_argument("--encounter-id", default="encounter_id", help="Encounter key column (default encounter_id).")
    pa.set_defaults(func=cmd_autodetect)

    pp = sub.add_parser("profile", help="Build the corpus profile from a mapped DB.")
    pp.add_argument("--source", required=True, help="Source DB SQLAlchemy URL.")
    pp.add_argument("--mapping", required=True, help="Mapping YAML (from autodetect, reviewed).")
    pp.add_argument("--out-yaml", help="Write the human-readable profile YAML here.")
    pp.add_argument("--out-json", help="Write the schema-valid JSON here.")
    pp.add_argument("--schema", help="DB schema/namespace (e.g. dbo).")
    pp.set_defaults(func=cmd_profile)

    pv = sub.add_parser("validate", help="Validate an existing profile JSON against the schema.")
    pv.add_argument("--json", required=True, help="Profile JSON to validate.")
    pv.set_defaults(func=cmd_validate)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

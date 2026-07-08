"""Orchestrator: turn a mapped database into the schema-shaped corpus profile.

Two passes over the database, both exact:

* **token pass** — :meth:`Db.iter_patients` streams one patient's raw rows at a
  time; each patient is tokenised full-record (every stored field, as JSON) and
  clinical-content (mapped clinical values only), by both encoders. One
  representative patient's rows are kept for Part B.
* **scope pass** — SQL aggregates for A1–A12 (:class:`ScopeProfiler`).

The token totals are merged into ``scale`` and the whole thing is returned as one
dict ready to validate against the intake schema.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .assemble import build_patient
from .io import Db
from .mapping import Mapping, StreamMap
from .model import CLINICAL_LEAVES
from .scope import ScopeProfiler
from .tokens import TokenAccumulator


# Values that carry no clinical information — explicit null placeholders. Compared
# case-folded against the whole trimmed value (so a note that merely contains the
# word "none" is untouched; only a cell that IS the placeholder is dropped).
_NULLISH = {
    "-", "--", "---", ".", "..", "...", "/", "\\", "|", "n/a", "n.a", "n.a.",
    "na", "null", "none", "nil", "nan", "s/d", "sin dato", "sin datos",
    "no aplica", "n/aplica", "ninguno", "ninguna", "desconocido", "no reportado",
}


def _meaningful(s: str) -> bool:
    """True only for a value worth counting as clinical content: it must have real
    substance — at least one alphanumeric character — and not be a bare null
    placeholder. Empty / whitespace / punctuation-only cells are dropped."""
    t = s.strip()
    if not t or not any(ch.isalnum() for ch in t):
        return False
    return t.casefold() not in _NULLISH


def _clinical_columns(sm: StreamMap) -> List[str]:
    key = "encounter" if sm.stream == "encounters" else sm.stream
    cols = [sm.columns[f] for f in CLINICAL_LEAVES.get(key, ()) if f in sm.columns]
    if sm.stream == "lab_results" and sm.layout == "wide":
        cols = cols + list(sm.analyte_columns)
    # mapping-declared free-text clinical columns with no canonical leaf (e.g. a
    # de-identified set's result_interpretation / order_observation / indications).
    cols = cols + list(sm.clinical_extra)
    return list(dict.fromkeys(cols))


def _clinical_text(bucket: Dict[str, List[Dict[str, Any]]], clin_cols: Dict[str, List[str]]) -> str:
    """Clinical-content text = the VALUABLE medical signal only. It counts stored
    values, never field/column names, and skips nulls, blank/whitespace, and
    nonsensical punctuation-/placeholder-only cells — so the clinical token total
    reflects real content, not structure or empties."""
    parts: List[str] = []
    for stream, rows in bucket.items():
        cols = clin_cols.get(stream)
        if not cols:
            continue
        for row in rows:
            for c in cols:
                v = row.get(c)
                if v is None:
                    continue
                s = str(v).strip()
                if _meaningful(s):
                    parts.append(s)
    return "\n".join(parts)


def _full_text(bucket: Dict[str, List[Dict[str, Any]]],
               source_key: Dict[str, Any]) -> str:
    """Serialize a patient's stored rows for the full-record token count.

    Two canonical streams can map to one physical table (e.g. ``encounters`` and
    ``diagnoses`` both off ``tbl_encounter``); the merge then holds that table's
    rows under both stream keys. Counting both would double the table's storage
    cost, so we serialize each distinct physical source ``(table, where)`` once —
    matching how ``scale_counts`` de-dups ``source_rows_total``.
    """
    seen = set()
    physical: Dict[str, List[Dict[str, Any]]] = {}
    for stream, rows in bucket.items():
        key = source_key.get(stream, stream)
        if key in seen:
            continue
        seen.add(key)
        physical[stream] = rows
    return json.dumps(physical, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def _clinical_cols_by_stream(present: List[StreamMap]) -> Dict[str, List[str]]:
    """Clinical columns per stream, de-duplicated across streams that share a
    physical table: a column mapped as a clinical leaf in two streams on one table
    (e.g. a diagnosis column reused by ``encounters`` and ``diagnoses``) is counted
    once, matching how the full-record surface de-dups the shared table."""
    seen = set()
    out: Dict[str, List[str]] = {}
    for sm in present:
        cols = []
        for c in _clinical_columns(sm):
            key = (sm.table, c)
            if key in seen:
                continue
            seen.add(key)
            cols.append(c)
        out[sm.stream] = cols
    return out


def _token_pass(db: Db, mapping: Mapping) -> Tuple[TokenAccumulator, Optional[Tuple[str, Dict]]]:
    acc = TokenAccumulator()
    present = mapping.present_streams()
    clin_cols = _clinical_cols_by_stream(present)
    # physical-source identity per stream, so shared tables aren't double-counted.
    source_key = {sm.stream: (sm.table, sm.where) for sm in present}
    sample: Optional[Tuple[str, Dict]] = None
    first_any: Optional[Tuple[str, Dict]] = None
    for pid, bucket in db.iter_patients(mapping):
        acc.add(_full_text(bucket, source_key), _clinical_text(bucket, clin_cols))
        if first_any is None:
            first_any = (pid, bucket)   # fallback worked patient (real data, no fake)
        if sample is None and bucket.get("encounters"):
            sample = (pid, bucket)   # first patient with a real encounter -> Part B
    return acc, (sample or first_any)


def _preflight(db: Db, mapping: Mapping) -> None:
    """Verify every present stream's table and referenced columns actually exist,
    and fail with ONE actionable message rather than a cryptic mid-profile SQL
    error (e.g. a stream mapped to a table whose patient-id column is named
    differently, or a mapped column that doesn't exist)."""
    problems: List[str] = []
    for sm in mapping.present_streams():
        cols = set(db.columns(sm.table))
        if not cols:
            problems.append(f"stream '{sm.stream}': table '{sm.table}' not found (or has no columns)")
            continue
        need = {sm.pid(mapping.keys): "patient-id column (set patient_id_column)"}
        if sm.date_column:
            need[sm.date_column] = "date_column"
        for field, phys in sm.columns.items():
            need[phys] = f"column mapped to '{field}'"
        for c in sm.analyte_columns:
            need[c] = "analyte column"
        for c in sm.clinical_extra:
            need[c] = "clinical_extra column"
        for c, why in need.items():
            if c not in cols:
                problems.append(f"stream '{sm.stream}': table '{sm.table}' has no column '{c}' ({why})")
    if problems:
        raise ValueError("mapping does not match the database:\n  - " + "\n  - ".join(problems))


def build_profile(db: Db, mapping: Mapping) -> Dict[str, Any]:
    # 0) fail fast if the mapping references tables/columns the DB doesn't have
    _preflight(db, mapping)

    # 1) exact token pass (+ representative patient for Part B)
    acc, sample = _token_pass(db, mapping)
    token_fields = acc.result()

    # 2) scope aggregates — the token pass's distinct-patient count is authoritative
    #    (it is what the distribution bins are built from), so pass it in.
    scope = ScopeProfiler(db, mapping, patients_total=acc.patients)
    scoped = scope.run()

    # 3) merge scale = counts + token fields
    scale = dict(scoped.pop("_scale_counts"))
    scale.update(token_fields)

    # 4) Part B — one worked patient. `sample` is a real patient (one with an
    #    encounter if any exists, else the first patient seen); build_patient
    #    synthesizes an encounter when the patient genuinely has none, so Part B is
    #    always real data. Only a truly empty corpus falls back to the placeholder.
    if sample is not None:
        patients = [build_patient(sample[0], sample[1], mapping)]
    else:
        patients = [{"patient_id": "n/a", "demographics": {},
                     "encounters": [{"encounter_id": "n/a-e1"}]}]

    corpus = dict(mapping.corpus or {})
    corpus.setdefault("name", None)
    corpus.setdefault("provider", None)
    corpus.setdefault("country", None)
    corpus.setdefault("source_system", None)
    # `contact` is optional but its sub-fields are non-nullable strings; an unfilled
    # `contact: {name:, email:, role:}` template block (all null) would fail schema
    # validation, so drop empty sub-fields and drop the block if nothing remains.
    contact = corpus.get("contact")
    if isinstance(contact, dict):
        contact = {k: v for k, v in contact.items() if v not in (None, "")}
        if contact:
            corpus["contact"] = contact
        else:
            corpus.pop("contact", None)

    profile: Dict[str, Any] = {"corpus": corpus, "scale": scale}
    profile.update(scoped)              # stream_inventory, record_depth, ...
    profile["patients"] = patients
    return profile

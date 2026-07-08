"""Schema mapping: bind a client's physical tables/columns to canonical streams.

A client database never matches the canonical stream/field names, so the profiler
is driven by a **mapping** — declarative, auditable, hand-editable YAML. An
:func:`autodetect` helper proposes a starting mapping from live schema reflection;
the operator reviews and corrects it before the full run (config + auto-detect).

The mapping model is intentionally small: per stream, a physical ``table``, the
``patient_id`` / ``encounter_id`` link columns, an optional ``date_column``, and a
``columns`` map of ``canonical_field -> physical_column``. Laboratory results may
be stored ``long`` (one row per analyte) or ``wide`` (one column per analyte); both
are supported so no client has to reshape their data first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .model import STREAM_ORDER


def _codes(v: Any) -> List[str]:
    """Normalize a value-map bucket's value to a list of string codes. Accepts a
    list/tuple or a single scalar (``female: mujer`` -> ``["mujer"]``); a scalar
    string is one code, not characters to iterate, and an int can't crash."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


@dataclass
class StreamMap:
    stream: str
    table: Optional[str] = None
    present: bool = True
    patient_id_column: Optional[str] = None       # defaults to keys.patient_id
    encounter_id_column: Optional[str] = None      # defaults to keys.encounter_id
    date_column: Optional[str] = None
    columns: Dict[str, str] = field(default_factory=dict)  # canonical_field -> physical
    layout: str = "long"                           # "long" | "wide" (lab analytes)
    analyte_columns: List[str] = field(default_factory=list)  # wide-lab value columns
    where: Optional[str] = None                    # optional SQL filter
    # extra physical columns whose values are clinical content but have no canonical
    # leaf (e.g. a de-identified set's free-text `result_interpretation`,
    # `order_observation`, `medical_indications`). Counted into clinical-content.
    clinical_extra: List[str] = field(default_factory=list)
    # per canonical-field value coding, e.g. {"gender": {"female": ["m","mujer"],
    # "male": ["h","hombre"], "other": ["i"]}} — lets a dataset declare that `m`
    # means mujer/female without breaking datasets where `m` means male.
    value_maps: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)

    def pid(self, keys: "Keys") -> str:
        return self.patient_id_column or keys.patient_id

    def eid(self, keys: "Keys") -> Optional[str]:
        return self.encounter_id_column or keys.encounter_id

    def gender_map(self) -> Optional[Dict[str, List[str]]]:
        return self.value_maps.get("gender")


@dataclass
class Keys:
    patient_id: str = "patient_id"
    encounter_id: Optional[str] = "encounter_id"


@dataclass
class Mapping:
    corpus: Dict[str, Any] = field(default_factory=dict)
    keys: Keys = field(default_factory=Keys)
    streams: Dict[str, StreamMap] = field(default_factory=dict)
    schema: Optional[str] = None  # DB schema/namespace (e.g. "dbo")

    def present_streams(self) -> List[StreamMap]:
        return [self.streams[s] for s in STREAM_ORDER
                if s in self.streams and self.streams[s].present and self.streams[s].table]

    def get(self, stream: str) -> Optional[StreamMap]:
        sm = self.streams.get(stream)
        return sm if (sm and sm.present and sm.table) else None

    # --- serialization ---------------------------------------------------- #
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mapping":
        keysd = data.get("keys") or {}
        keys = Keys(patient_id=keysd.get("patient_id", "patient_id"),
                    encounter_id=keysd.get("encounter_id", "encounter_id"))
        streams: Dict[str, StreamMap] = {}
        for name, sd in (data.get("streams") or {}).items():
            sd = sd or {}
            # value_maps is the general form; `gender_map`/`gender_values` are
            # accepted shorthands that populate value_maps["gender"]. A bucket's
            # values may be a list OR a single scalar (`female: mujer`); a scalar is
            # one code, NOT a string to iterate char-by-char (and an int must not
            # crash `list(...)`).
            value_maps = {k: {b: _codes(v) for b, v in (vm or {}).items()}
                          for k, vm in (sd.get("value_maps") or {}).items()}
            gm = sd.get("gender_map") or sd.get("gender_values")
            if gm:
                value_maps["gender"] = {b: _codes(v) for b, v in gm.items()}
            streams[name] = StreamMap(
                stream=name,
                table=sd.get("table"),
                present=sd.get("present", bool(sd.get("table"))),
                patient_id_column=sd.get("patient_id_column"),
                encounter_id_column=sd.get("encounter_id_column"),
                date_column=sd.get("date_column"),
                columns=dict(sd.get("columns") or {}),
                layout=sd.get("layout", "long"),
                analyte_columns=list(sd.get("analyte_columns") or []),
                where=sd.get("where"),
                clinical_extra=list(sd.get("clinical_extra") or []),
                value_maps=value_maps,
            )
        return cls(corpus=dict(data.get("corpus") or {}), keys=keys,
                   streams=streams, schema=data.get("schema"))

    @classmethod
    def from_yaml(cls, path: str) -> "Mapping":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "corpus": self.corpus or {
                "name": None, "provider": None, "country": None,
                "source_system": None, "source_database": None,
                "contact": {"name": None, "email": None, "role": None},
            },
            "schema": self.schema,
            "keys": {"patient_id": self.keys.patient_id,
                     "encounter_id": self.keys.encounter_id},
            "streams": {},
        }
        for name in STREAM_ORDER:
            sm = self.streams.get(name)
            if sm is None:
                out["streams"][name] = {"present": False, "table": None}
                continue
            entry: Dict[str, Any] = {"present": sm.present, "table": sm.table}
            # Link/filter overrides must survive a round-trip: without them an
            # autodetect -> edit -> re-serialize cycle silently drops (e.g.) a
            # notes stream's encounter_id_column or a stream's `where` filter.
            if sm.patient_id_column:
                entry["patient_id_column"] = sm.patient_id_column
            if sm.encounter_id_column:
                entry["encounter_id_column"] = sm.encounter_id_column
            if sm.date_column:
                entry["date_column"] = sm.date_column
            if sm.layout != "long":
                entry["layout"] = sm.layout
            if sm.analyte_columns:
                entry["analyte_columns"] = sm.analyte_columns
            if sm.columns:
                entry["columns"] = sm.columns
            if sm.clinical_extra:
                entry["clinical_extra"] = sm.clinical_extra
            if sm.value_maps:
                entry["value_maps"] = sm.value_maps
            if sm.where:
                entry["where"] = sm.where
            out["streams"][name] = entry
        return out

    def to_yaml(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False, allow_unicode=True)


# --------------------------------------------------------------------------- #
# Auto-detection (a proposal, to be reviewed).
# --------------------------------------------------------------------------- #
# Keyword hints per canonical stream: a table matches if its name contains any.
_STREAM_HINTS: Dict[str, List[str]] = {
    "demographics": ["patient", "demograph"],
    "encounters": ["encounter", "visit", "consult", "admission"],
    "triage_vitals": ["triage", "vital"],
    "history_notes": ["history", "note", "anamnesis"],
    "physical_exam": ["exam", "physical"],
    "region_findings": ["region", "finding"],
    "impression_notes": ["impression", "assessment"],
    "diagnoses": ["diagnos"],
    "lab_requests": ["lab_request", "lab_order", "order"],
    "lab_results": ["lab", "result", "analyte"],
    "radiology": ["radiolog", "imaging", "image"],
    "prescriptions": ["prescription", "medication", "drug", "rx"],
    "pharmacy_requests": ["pharmacy", "dispense"],
    "procedures": ["procedure"],
    "immunizations": ["immuniz", "vaccin"],
    "allergies": ["allerg"],
    "referrals": ["referral", "refer"],
}

# Canonical field -> candidate physical column name fragments.
_FIELD_HINTS: Dict[str, List[str]] = {
    "age_years": ["age_years", "age"],
    "gender": ["sex", "gender"],
    "home_region": ["region"],
    "registered_date": ["registered", "enroll"],
    "facility_id": ["facility", "care_center", "center", "site"],
    "specialty_id": ["specialty_code", "specialty"],
    "visit_type": ["care_setting", "visit_type", "encounter_type"],
    "diagnosis_name": ["diagnosis", "diagnosis_description"],
    "icd10_code": ["diagnosis_code", "icd10", "icd_10", "icd"],
    "analyte_name": ["test_description", "analyte", "test_name"],
    "value": ["value", "result"],
    "unit": ["unit"],
    "reference_range": ["reference", "ref_range"],
    "test_name": ["test_description", "test_name", "study_name"],
    "study_name": ["study_name", "study"],
    "modality": ["modality"],
    "report_text": ["report_text", "report"],
    "generic_name": ["medication_name", "generic", "drug_name", "product"],
    "brand_name": ["brand"],
    "dose": ["dose"],
    "route": ["admin_route", "route"],
    "frequency": ["frequency"],
    "duration": ["duration"],
    "text": ["note_full", "text", "observation", "report"],
}

_DATE_HINTS = ["datetime", "_date", "date", "_start", "start", "_at"]


def _pick(cols: List[str], fragments: List[str]) -> Optional[str]:
    low = {c.lower(): c for c in cols}
    for frag in fragments:
        for lc, orig in low.items():
            if frag == lc:
                return orig
    for frag in fragments:
        for lc, orig in low.items():
            if frag in lc:
                return orig
    return None


def autodetect(engine, schema: Optional[str] = None,
               patient_id: str = "patient_id",
               encounter_id: str = "encounter_id") -> Mapping:
    """Propose a mapping from live schema reflection. Review before the full run."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = insp.get_table_names(schema=schema)
    cols_by_table = {t: [c["name"] for c in insp.get_columns(t, schema=schema)]
                     for t in tables}

    keys = Keys(patient_id=patient_id, encounter_id=encounter_id)
    streams: Dict[str, StreamMap] = {}

    for stream, hints in _STREAM_HINTS.items():
        table = _match_table(tables, hints)
        if not table:
            streams[stream] = StreamMap(stream=stream, present=False)
            continue
        cols = cols_by_table[table]
        sm = StreamMap(stream=stream, table=table, present=True)
        if patient_id not in cols:
            alt = _pick(cols, ["patient_id", "patient", "pat_id"])
            if alt:
                sm.patient_id_column = alt
        if encounter_id in cols:
            sm.encounter_id_column = encounter_id
        date_col = _pick(cols, _DATE_HINTS)
        if date_col:
            sm.date_column = date_col
        for cfield, frags in _FIELD_HINTS.items():
            phys = _pick(cols, frags)
            if phys:
                sm.columns[cfield] = phys
        streams[stream] = sm

    return Mapping(keys=keys, streams=streams, schema=schema)


def _match_table(tables: List[str], hints: List[str]) -> Optional[str]:
    best = None
    for t in tables:
        tl = t.lower()
        for h in hints:
            if h in tl:
                # prefer the shortest matching name (most specific)
                if best is None or len(t) < len(best):
                    best = t
                break
    return best

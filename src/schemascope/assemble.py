"""Assemble one worked patient (Part B) into the canonical nested record.

Runs for a single patient only, so clarity beats cleverness. Physical rows for
each stream are translated to canonical fields via the mapping, encounter-scoped
streams are nested under their encounter, and the few schema-constrained fields
(``gender`` enum, ``region_findings.outcome`` enum) are normalised so the record
validates as-is.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .mapping import Mapping, StreamMap
from .model import gender_bucket, to_date_str

_ENCOUNTER_SCALAR = ["facility_id", "region", "specialty_id", "visit_type", "chief_complaint"]
_LIST_STREAMS = [
    "history_notes", "physical_exam", "region_findings", "impression_notes",
    "diagnoses", "lab_requests", "lab_results", "radiology", "procedures",
    "prescriptions", "pharmacy_requests", "referrals",
]


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _val(row: Dict[str, Any], sm: StreamMap, field: str) -> Any:
    col = sm.columns.get(field)
    return row.get(col) if col else None


def _str_row(d: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce every value of a translated row to str|null (schema string fields)."""
    return {k: _s(v) for k, v in d.items()}


def _num_or_str(v: Any) -> Any:
    """A lab value is string|number|null: keep real numbers as numbers (Decimals
    become float so JSON-schema's 'number' accepts them), coerce anything else to
    a string. A non-finite float (nan/inf) becomes null — it can't be valid JSON."""
    if v is None:
        return None
    if isinstance(v, bool):
        return _s(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            f = float(v)
            return f if math.isfinite(f) else None
    except Exception:
        pass
    return _s(v)


def _norm_gender(v: Any, value_map: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    if not (_s(v) or ""):
        return "Unknown"
    return {"female": "Female", "male": "Male"}.get(gender_bucket(v, value_map), "Other")


def _norm_outcome(v: Any) -> str:
    s = (_s(v) or "").lower()
    if "abnorm" in s or "anorm" in s:
        return "Abnormal"
    if "normal" in s:
        return "Normal"
    return "Not examined"


def _translate_row(row: Dict[str, Any], sm: StreamMap, fields: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields:
        out[f] = _val(row, sm, f)
    return out


def _lab_rows(rows: List[Dict[str, Any]], sm: StreamMap) -> List[Dict[str, Any]]:
    """lab_results in either long (one row per analyte) or wide (analyte columns)."""
    out: List[Dict[str, Any]] = []
    if sm.layout == "wide" and sm.analyte_columns:
        for row in rows:
            for col in sm.analyte_columns:
                if col in row and row[col] is not None and _s(row[col]):
                    out.append({"analyte_name": col, "value": _num_or_str(row[col]),
                                "unit": _s(_val(row, sm, "unit")),
                                "reference_range": _s(_val(row, sm, "reference_range")),
                                "flag": _s(_val(row, sm, "flag")),
                                "order_id": _s(_val(row, sm, "order_id")),
                                "resulted_date": _s(_val(row, sm, "resulted_date"))})
    else:
        for row in rows:
            r = _translate_row(row, sm, ["order_id", "analyte_name", "value", "unit",
                                         "reference_range", "flag", "resulted_date"])
            value = _num_or_str(r.get("value"))            # string|number|null
            r = _str_row(r)                                # everything else string|null
            r["value"] = value
            if r.get("analyte_name") is None:
                r["analyte_name"] = "result"
            out.append(r)
    return out


def build_patient(pid: str, bucket: Dict[str, List[Dict[str, Any]]], mapping: Mapping) -> Dict[str, Any]:
    keys = mapping.keys
    patient: Dict[str, Any] = {"patient_id": str(pid), "demographics": {}, "encounters": []}

    # demographics (first row of the stream)
    dsm = mapping.get("demographics")
    if dsm and bucket.get("demographics"):
        drow = bucket["demographics"][0]
        patient["demographics"] = {
            "age": _s(_val(drow, dsm, "age")),
            "age_years": _to_int(_val(drow, dsm, "age_years")),
            "age_group": _s(_val(drow, dsm, "age_group")),
            "gender": _norm_gender(_val(drow, dsm, "gender"), dsm.gender_map()),
            "home_region": _s(_val(drow, dsm, "home_region")),
            "home_facility_id": _s(_val(drow, dsm, "facility_id")),
            "registered_date": to_date_str(_val(drow, dsm, "registered_date")),
        }

    # encounters keyed by encounter_id
    esm = mapping.get("encounters")
    enc_by_id: Dict[Optional[str], Dict[str, Any]] = {}
    order: List[Optional[str]] = []
    if esm and bucket.get("encounters"):
        eid_col = esm.eid(keys)
        synth = 0
        for row in bucket["encounters"]:
            raw = _s(row.get(eid_col)) if eid_col else None
            if raw is not None and raw in enc_by_id:
                continue                       # duplicate encounter_id -> first row wins
            if raw is None:                    # NULL id -> its own synthesized encounter
                synth += 1
                eid = f"{pid}-e{synth}"
            else:
                eid = raw
            enc = {"encounter_id": eid}
            enc.update(_str_row(_translate_row(row, esm, _ENCOUNTER_SCALAR)))
            enc["encounter_date"] = to_date_str(row.get(esm.date_column)) if esm.date_column else None
            enc_by_id[eid] = enc
            order.append(eid)
    if not enc_by_id:  # synthesize one encounter so the record is valid
        enc_by_id[None] = {"encounter_id": f"{pid}-e1"}
        order.append(None)

    def _resolve_enc(eid: Optional[str]) -> Dict[str, Any]:
        """The encounter a nested row belongs to. A row whose link key matches no
        encounter gets its OWN encounter (keyed by that value) rather than being
        silently folded into the first one — so a mismatched link column (e.g.
        notes keyed by admission_id vs encounters keyed by encounter_id) shows up
        in Part B as unlinked rows instead of quietly corrupting encounter #1."""
        if eid in enc_by_id:
            return enc_by_id[eid]
        if eid is not None:
            enc = {"encounter_id": eid}
            enc_by_id[eid] = enc
            order.append(eid)
            return enc
        return enc_by_id[order[0]]

    # nested clinical streams -> attach to their encounter (unmatched key -> own)
    for stream in _LIST_STREAMS:
        sm = mapping.get(stream)
        if not sm or not bucket.get(stream):
            continue
        eid_col = sm.eid(keys)
        for row in bucket[stream]:
            eid = _s(row.get(eid_col)) if eid_col else None
            enc = _resolve_enc(eid)
            if stream == "lab_results":
                enc.setdefault(stream, []).extend(_lab_rows([row], sm))
            else:
                enc.setdefault(stream, []).append(
                    _canonical_stream_row(stream, row, sm))

    # triage_vitals (one object per encounter) — coerced to the schema's types so
    # text-stored vitals (temperature_c='38.2', pulse_rate='88') still validate.
    tsm = mapping.get("triage_vitals")
    if tsm and bucket.get("triage_vitals"):
        eid_col = tsm.eid(keys)
        _num = ["temperature_c", "weight_kg", "height_cm", "bmi", "random_blood_sugar"]
        _int = ["bp_systolic", "bp_diastolic", "pulse_rate", "respiratory_rate", "oxygen_saturation"]
        for row in bucket["triage_vitals"]:
            eid = _s(row.get(eid_col)) if eid_col else None
            enc = _resolve_enc(eid)
            v: Dict[str, Any] = {"blood_pressure": _s(_val(row, tsm, "blood_pressure"))}
            for f in _num:
                v[f] = _to_num(_val(row, tsm, f))
            for f in _int:
                v[f] = _to_int(_val(row, tsm, f))
            enc["triage_vitals"] = v

    # patient-level streams (not nested under an encounter) — allergies &
    # immunizations are canonical streams; render them so a dataset that carries
    # them isn't silently dropped from the worked patient.
    asm = mapping.get("allergies")
    if asm and bucket.get("allergies"):
        patient["allergies"] = [_str_row(_translate_row(r, asm, ["substance", "reaction", "severity"]))
                                for r in bucket["allergies"]]
    ism = mapping.get("immunizations")
    if ism and bucket.get("immunizations"):
        patient["immunizations"] = [_str_row(_translate_row(r, ism, ["vaccine", "date", "dose"]))
                                    for r in bucket["immunizations"]]

    # Drop the synthesized empty placeholder encounter if real encounters
    # materialized from nested streams (e.g. labs carrying an encounter_id when no
    # encounters stream is mapped) and nothing attached to the placeholder.
    if None in enc_by_id and len(order) > 1 and set(enc_by_id[None]) <= {"encounter_id"}:
        order.remove(None)
        del enc_by_id[None]

    patient["encounters"] = [enc_by_id[e] for e in order]
    return patient


_STREAM_FIELDS = {
    "history_notes": ["text"],
    "physical_exam": ["text"],
    "impression_notes": ["text"],
    "region_findings": ["region", "outcome", "finding_text"],
    "diagnoses": ["icd10_code", "icd10_chapter", "diagnosis_name", "diagnosis_text", "is_primary"],
    "lab_requests": ["order_id", "test_name", "panel", "ordered_date"],
    "radiology": ["modality", "study_name", "report_text", "image_ref", "performed_date"],
    "procedures": ["name", "code", "performed_date"],
    "prescriptions": ["brand_name", "generic_name", "dose", "route", "frequency",
                      "duration", "sig", "prescribed_date"],
    "pharmacy_requests": ["request_id", "drug_name", "quantity", "status", "requested_date"],
    "referrals": ["to_facility_id", "specialty", "reason", "date"],
}


# Part B fields that are NOT string-typed in the schema (everything else is
# string|null and is stringified, so a numeric DB column validates).
_NONSTRING_FIELDS = {
    "diagnoses": {"is_primary"},
    "pharmacy_requests": {"quantity"},
}


def _canonical_stream_row(stream: str, row: Dict[str, Any], sm: StreamMap) -> Dict[str, Any]:
    r = _translate_row(row, sm, _STREAM_FIELDS[stream])
    if stream == "region_findings":
        r["outcome"] = _norm_outcome(r.get("outcome"))
    elif stream == "diagnoses":
        r["is_primary"] = _to_bool(r.get("is_primary"))
    elif stream == "pharmacy_requests":
        r["quantity"] = _to_num(r.get("quantity"))
    # Coerce every remaining string-typed field to str|null, so numeric DB columns
    # (facility_id 101, specialty_id 7, an integer icd10/order id, a decimal dose)
    # validate against the schema instead of failing Part B.
    skip = _NONSTRING_FIELDS.get(stream, set())
    for k in list(r):
        if k not in skip:
            r[k] = _s(r[k])
    if stream == "region_findings":
        r["region"] = r.get("region") or "unspecified"   # required non-null string
    return r


def _to_int(v: Any) -> Optional[int]:
    try:
        f = float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None
    # int(float('inf')) raises OverflowError; junk numerics must coerce to None.
    return int(f) if (f is not None and math.isfinite(f)) else None


def _to_num(v: Any) -> Optional[float]:
    try:
        f = float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None
    # a non-finite (nan/inf) would serialize as an invalid-JSON NaN/Infinity token.
    return f if (f is not None and math.isfinite(f)) else None


def _to_bool(v: Any) -> Optional[bool]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "primary"):
        return True
    if s in ("0", "false", "f", "no", "n"):
        return False
    return None

"""The canonical corpus model.

Two responsibilities, kept in one place so they never drift apart:

1. **The canonical streams** — the 17 clinical streams the intake profile is
   described in (``stream_inventory`` in the schema). Every physical table in a
   client database is mapped onto one of these.

2. **The clinical-content contract** — for a canonical *patient record*, which
   leaf values are **clinical content** (what a model learns medicine from:
   diagnoses, results, medications, narrative, vitals, findings) versus
   **structure** (keys, ids, dates, flags, JSON syntax). The token engine uses
   exactly this split to report the full-record vs clinical-content token counts
   the profile headlines.

Keeping the split declarative — one list per record section — means the
definition is auditable and testable, not buried in the tokeniser.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

# A profile "date" is a real calendar value at year, year-month, or full-date
# granularity — full dates today, and year-only after HIPAA generalization both
# validate. A leading date prefix is extracted (so a stored datetime like
# "2023-01-05 10:30:00" reduces to "2023-01-05"); anything else is not a date.
_DATE_PREFIX = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def to_date_str(value: Any) -> Optional[str]:
    """Normalize a stored date/datetime/year to ``YYYY`` / ``YYYY-MM`` /
    ``YYYY-MM-DD``, or ``None`` if it isn't a date at all. Keeps whatever
    granularity the source carries, and **degrades** an out-of-range finer part
    rather than dropping the whole value — so ``2023-99-99`` becomes ``2023`` (the
    year is real). This keeps the headline first/last dates consistent with the
    year histogram, which also keys off the year."""
    if value is None:
        return None
    s = str(value).strip()
    m = _DATE_PREFIX.match(s)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if mo and d:
        try:
            date(int(y), int(mo), int(d))
            return f"{y}-{mo}-{d}"
        except ValueError:
            pass
    if mo:
        try:
            date(int(y), int(mo), 1)      # valid year-month?
            return f"{y}-{mo}"
        except ValueError:
            pass
    return y                              # a 4-digit year is always valid on its own


def is_profile_date(value: Any) -> bool:
    """Format predicate for the schema's ``format: date`` fields (accepts null;
    non-null must normalize to a real year / year-month / full date)."""
    return value is None or to_date_str(value) is not None

# Canonical streams, in the order the intake template lists them. This is the
# closed vocabulary for ``stream_inventory`` and for schema mapping.
STREAM_ORDER: List[str] = [
    "demographics",
    "encounters",
    "triage_vitals",
    "history_notes",
    "physical_exam",
    "region_findings",
    "impression_notes",
    "diagnoses",
    "lab_requests",
    "lab_results",
    "radiology",
    "prescriptions",
    "pharmacy_requests",
    "procedures",
    "immunizations",
    "allergies",
    "referrals",
]

# --------------------------------------------------------------------------- #
# Clinical-content contract.
#
# For each *section* of a canonical patient record, the leaf fields whose VALUES
# are clinical content. Anything not listed here (ids, dates, codes-as-keys,
# flags, and every JSON key/brace) is structure. A field absent from a record is
# simply skipped, so partial records are handled without special-casing.
# --------------------------------------------------------------------------- #
CLINICAL_LEAVES: Dict[str, List[str]] = {
    # patient-level
    "demographics": ["age", "age_group"],
    "problem_list": ["problem"],
    "allergies": ["substance", "reaction", "severity"],
    "immunizations": ["vaccine"],
    # encounter-level scalar
    "encounter": ["chief_complaint"],
    # nested clinical streams (under an encounter)
    "triage_vitals": [
        "temperature_c", "blood_pressure", "bp_systolic", "bp_diastolic",
        "pulse_rate", "respiratory_rate", "oxygen_saturation", "weight_kg",
        "height_cm", "bmi", "random_blood_sugar",
    ],
    "history_notes": ["text"],
    "physical_exam": ["text"],
    "region_findings": ["outcome", "finding_text"],
    "impression_notes": ["text"],
    "diagnoses": ["icd10_code", "diagnosis_name", "diagnosis_text"],
    "lab_requests": ["test_name", "panel"],
    "lab_results": ["analyte_name", "value", "unit", "reference_range", "flag"],
    "radiology": ["modality", "study_name", "report_text"],
    "procedures": ["name", "code"],
    "prescriptions": [
        "brand_name", "generic_name", "dose", "route", "frequency", "duration", "sig",
    ],
    "pharmacy_requests": ["drug_name", "quantity"],
    "referrals": ["reason"],
    "clinical_notes": ["text"],
    "documents": ["title"],
}

_DEFAULT_GENDER = {
    "female": ("f", "female", "femenino", "femenina", "mujer"),
    "male": ("m", "male", "masculino", "hombre"),
}


def gender_bucket(v: Any, value_map: Optional[Dict[str, List[str]]] = None) -> str:
    """Bucket a raw gender value into female / male / other_unknown.

    ``value_map`` (from the stream mapping, e.g. ``{"female": ["m","mujer"],
    "male": ["h","hombre"], "other": ["i"]}``) lets a dataset declare its own
    coding — critical because single-letter codes conflict across sources (``m`` is
    *male* in one dataset, *mujer/female* in another). When a map is given it is
    authoritative; otherwise the built-in Spanish/English heuristic applies.
    """
    s = "" if v is None else str(v).strip().lower()
    if not s:
        return "other_unknown"
    if value_map:
        for bucket, values in value_map.items():
            if s in {str(x).strip().lower() for x in (values or [])}:
                b = bucket.strip().lower()
                return "female" if b == "female" else "male" if b == "male" else "other_unknown"
        return "other_unknown"
    for bucket, values in _DEFAULT_GENDER.items():
        if s in values:
            return bucket
    return "other_unknown"

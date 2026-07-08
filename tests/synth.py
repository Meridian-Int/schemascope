"""A small synthetic clinical database with arbitrary client-style table names,
plus the mapping that binds it to canonical streams. Used by the tests (and handy
as a live example). Deterministic — no randomness — so assertions are stable.
"""

from __future__ import annotations

from sqlalchemy import text

from schemascope.mapping import Mapping

_DDL = [
    "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years INTEGER, home_region TEXT, registered_date TEXT)",
    "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT, care_center_code TEXT, specialty_code TEXT, care_setting TEXT, region TEXT)",
    "CREATE TABLE notes (patient_id TEXT, encounter_id TEXT, note_full TEXT)",
    "CREATE TABLE dx (patient_id TEXT, encounter_id TEXT, diagnosis_code TEXT, diagnosis TEXT)",
    "CREATE TABLE lab (patient_id TEXT, encounter_id TEXT, hemoglobin REAL, hba1c REAL, creatinine REAL)",
    "CREATE TABLE med (patient_id TEXT, encounter_id TEXT, medication_name TEXT, dose TEXT, admin_route TEXT, frequency TEXT, duration TEXT)",
    "CREATE TABLE triage (patient_id TEXT, encounter_id TEXT, temperature_c REAL, blood_pressure TEXT, pulse_rate INTEGER, weight_kg REAL)",
    "CREATE TABLE region (patient_id TEXT, encounter_id TEXT, region TEXT, outcome TEXT)",
]

# 6 patients; patient 6 appears only via labs+meds (no encounter) to exercise the
# merge across streams and the demographics-vs-stream patient count.
_PATIENTS = [
    ("P001", "F", 34, "Central", "2019-03-01"),
    ("P002", "M", 8,  "Coast",   "2020-06-15"),
    ("P003", "F", 61, "North",   "2018-01-20"),
    ("P004", "M", 2,  "Central", "2021-09-05"),
    ("P005", "F", 45, "Coast",   "2017-11-30"),
    ("P006", "M", 27, "North",   "2022-02-10"),
]
_ENC = [
    ("P001", "E1", "2023-01-05", "CC1", "MED", "Outpatient", "Central"),
    ("P001", "E2", "2023-06-10", "CC1", "MED", "Outpatient", "Central"),
    ("P002", "E3", "2024-02-01", "CC2", "PED", "Outpatient", "Coast"),
    ("P003", "E4", "2022-04-12", "CC1", "CAR", "Inpatient",  "North"),
    ("P004", "E5", "2024-03-03", "CC2", "PED", "Emergency",  "Coast"),
    ("P005", "E6", "2023-08-19", "CC3", "MED", "Outpatient", "Coast"),
]
_NOTES = [
    ("P001", "E1", "Paciente refiere cefalea y fiebre. Control en dos semanas."),
    ("P001", "E2", "Mejoria clinica. Continua tratamiento."),
    ("P002", "E3", "Nino con tos y rinorrea, afebril."),
    ("P003", "E4", "Hipertension no controlada, ajustar dosis."),
    ("P005", "E6", "Diabetes tipo 2, revision de esquema."),
]
_DX = [
    ("P001", "E1", "J06", "Acute upper respiratory infection"),
    ("P002", "E3", "J06", "Acute upper respiratory infection"),
    ("P003", "E4", "I10", "Essential (primary) hypertension"),
    ("P005", "E6", "E11", "Type 2 diabetes mellitus"),
    ("P003", "E4", "bad", "Uncoded free text problem"),   # exercises shape-match < 100%
]
_LAB = [
    ("P001", "E1", 13.2, None, 0.9),
    ("P003", "E4", 11.0, 7.8, 1.4),
    ("P005", "E6", 12.5, 9.1, 1.0),
    ("P006", None, 14.0, None, None),                      # patient with no encounter
]
_MED = [
    ("P001", "E1", "Paracetamol", "500 mg", "Oral", "TID", "5 d"),
    ("P003", "E4", "Losartan", "50 mg", "Oral", "OD", "30 d"),
    ("P005", "E6", "Metformin", "850 mg", "Oral", "BID", "90 d"),
    ("P006", None, "Ibuprofen", "400 mg", "Oral", None, None),
]
_TRIAGE = [
    ("P001", "E1", 38.2, "120/80", 88, 60.0),
    ("P002", "E3", 37.0, "100/70", 96, 22.0),
    ("P003", "E4", 36.8, "160/95", 78, 72.0),
    ("P005", "E6", 37.1, "130/85", 80, 68.0),
]
_REGION = [
    ("P001", "E1", "Head", "Normal"),
    ("P001", "E1", "Chest", "Abnormal"),
    ("P003", "E4", "Cardiovascular", "Abnormal"),
    ("P003", "E4", "Abdomen", "Not examined"),
]


def build(engine) -> Mapping:
    with engine.begin() as c:
        for ddl in _DDL:
            c.execute(text(ddl))
        c.execute(text("INSERT INTO pt VALUES (:a,:b,:c,:d,:e)"),
                  [dict(zip("abcde", r)) for r in _PATIENTS])
        c.execute(text("INSERT INTO enc VALUES (:a,:b,:c,:d,:e,:f,:g)"),
                  [dict(zip("abcdefg", r)) for r in _ENC])
        c.execute(text("INSERT INTO notes VALUES (:a,:b,:c)"),
                  [dict(zip("abc", r)) for r in _NOTES])
        c.execute(text("INSERT INTO dx VALUES (:a,:b,:c,:d)"),
                  [dict(zip("abcd", r)) for r in _DX])
        c.execute(text("INSERT INTO lab VALUES (:a,:b,:c,:d,:e)"),
                  [dict(zip("abcde", r)) for r in _LAB])
        c.execute(text("INSERT INTO med VALUES (:a,:b,:c,:d,:e,:f,:g)"),
                  [dict(zip("abcdefg", r)) for r in _MED])
        c.execute(text("INSERT INTO triage VALUES (:a,:b,:c,:d,:e,:f)"),
                  [dict(zip("abcdef", r)) for r in _TRIAGE])
        c.execute(text("INSERT INTO region VALUES (:a,:b,:c,:d)"),
                  [dict(zip("abcd", r)) for r in _REGION])
    return mapping()


def mapping() -> Mapping:
    return Mapping.from_dict({
        "corpus": {"name": "Synthetic Test Corpus", "provider": "schemascope tests",
                   "country": "Testland", "source_system": "SyntheticEMR"},
        "keys": {"patient_id": "patient_id", "encounter_id": "encounter_id"},
        "streams": {
            "demographics": {"table": "pt", "columns": {
                "age_years": "age_years", "gender": "sex",
                "home_region": "home_region", "registered_date": "registered_date"}},
            "encounters": {"table": "enc", "date_column": "encounter_start", "columns": {
                "facility_id": "care_center_code", "specialty_id": "specialty_code",
                "visit_type": "care_setting", "region": "region"}},
            "triage_vitals": {"table": "triage", "columns": {
                "temperature_c": "temperature_c", "blood_pressure": "blood_pressure",
                "pulse_rate": "pulse_rate", "weight_kg": "weight_kg"}},
            "history_notes": {"table": "notes", "columns": {"text": "note_full"}},
            "region_findings": {"table": "region", "columns": {
                "region": "region", "outcome": "outcome"}},
            "diagnoses": {"table": "dx", "columns": {
                "icd10_code": "diagnosis_code", "diagnosis_name": "diagnosis",
                "diagnosis_text": "diagnosis"}},
            "lab_results": {"table": "lab", "layout": "wide",
                            "analyte_columns": ["hemoglobin", "hba1c", "creatinine"]},
            "prescriptions": {"table": "med", "columns": {
                "generic_name": "medication_name", "dose": "dose",
                "route": "admin_route", "frequency": "frequency", "duration": "duration"}},
        },
    })

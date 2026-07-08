"""Regression tests for the senior-review findings — the adversarial data shapes
the original synthetic fixture happened to dodge.
"""

from sqlalchemy import create_engine, text

from schemascope.io import Db
from schemascope.mapping import Mapping
from schemascope.profile import build_profile
from schemascope.qa import errors, run_qa
from schemascope.validate import validate


_CORPUS = {"name": "T", "provider": "T", "country": "T", "source_system": "T"}


def _db(tmp_path, name, stmts):
    eng = create_engine(f"sqlite:///{tmp_path/name}")
    with eng.begin() as c:
        for s in stmts:
            c.execute(text(s))
    return Db(eng)


def test_integer_pk_merge_does_not_split(tmp_path):
    # #1: numeric IDs 2 and 10; patient 10 also has a lab. The text-ordered merge
    # must emit each patient exactly once and MERGE patient 10's streams (not split).
    # Keys are compared as text (collation/type-safe), so order is lexical.
    db = _db(tmp_path, "int.db", [
        "CREATE TABLE pt (patient_id INTEGER, sex TEXT)",
        "INSERT INTO pt VALUES (2,'F'),(10,'M')",
        "CREATE TABLE lab (patient_id INTEGER, hemoglobin REAL)",
        "INSERT INTO lab VALUES (10, 13.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]},
    }})
    got = list(db.iter_patients(m))
    ids = [p for p, _ in got]
    assert sorted(ids) == ["10", "2"] and len(ids) == 2     # each patient exactly once
    d = dict(got)
    assert set(d["10"]) == {"demographics", "lab_results"}   # merged, not split
    assert set(d["2"]) == {"demographics"}


def test_null_patient_id_orphan_is_skipped(tmp_path):
    # #2: an orphan row with NULL patient_id must not crash the merge.
    db = _db(tmp_path, "null.db", [
        "CREATE TABLE pt (patient_id INTEGER, sex TEXT)",
        "INSERT INTO pt VALUES (1,'F')",
        "CREATE TABLE lab (patient_id INTEGER, hemoglobin REAL)",
        "INSERT INTO lab VALUES (NULL, 9.9),(1, 13.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]},
    }})
    assert [p for p, _ in db.iter_patients(m)] == ["1"]


def test_text_typed_vitals_still_validate(tmp_path):
    # #3: TEXT-stored vitals must be coerced so Part B validates.
    db = _db(tmp_path, "vit.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','F')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01')",
        "CREATE TABLE triage (patient_id TEXT, encounter_id TEXT, temperature_c TEXT, pulse_rate TEXT, blood_pressure TEXT)",
        "INSERT INTO triage VALUES ('P1','E1','38.2','88','120/80')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"},
        "triage_vitals": {"table": "triage", "columns": {
            "temperature_c": "temperature_c", "pulse_rate": "pulse_rate",
            "blood_pressure": "blood_pressure"}},
    }})
    profile = build_profile(db, m)
    assert validate(profile) == []
    assert errors(run_qa(profile)) == []
    tv = profile["patients"][0]["encounters"][0]["triage_vitals"]
    assert tv["temperature_c"] == 38.2 and tv["pulse_rate"] == 88   # coerced from text


def test_dirty_encounter_date_does_not_crash(tmp_path):
    # #4: a non-null but unparseable date must be skipped, not crash longitudinal.
    db = _db(tmp_path, "date.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','F')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01'),('P1','E2','not-a-date')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"},
    }})
    profile = build_profile(db, m)
    assert errors(run_qa(profile)) == []
    years = [e["year"] for e in profile["longitudinal"]["encounters_by_year"]]
    assert 2023 in years


def test_patients_total_matches_bins_exactly(tmp_path):
    # #5: patients_total (token pass) and the distribution bins are one invariant.
    db = _db(tmp_path, "cnt.db", [
        "CREATE TABLE pt (patient_id INTEGER, sex TEXT)",
        "INSERT INTO pt VALUES (1,'F'),(2,'M'),(3,'F')",
        "CREATE TABLE lab (patient_id INTEGER, hemoglobin REAL)",
        "INSERT INTO lab VALUES (2,12.0),(3,11.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]},
    }})
    profile = build_profile(db, m)
    pt = profile["scale"]["patients_total"]
    bins_sum = sum(b["patients"] for b in profile["scale"]["token_distribution"]["bins"])
    assert pt == 3 and bins_sum == 3
    assert errors(run_qa(profile)) == []

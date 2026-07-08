"""Regression tests for the review findings that the real Meridian schema/mapping
triggers but the original synthetic fixture happened to dodge:

* two canonical streams on ONE physical table (encounters + diagnoses -> tbl_encounter),
* per-stream `where` filters that scope aggregates must honor,
* encounter IDs that are unique only within a patient,
* nested rows whose link column doesn't match any encounter (notes by admission_id),
* a no-encounter corpus (Part B must be real data, not an "n/a" stub),
* QA failure must leave no deliverable on disk,
* mapping link/filter overrides surviving a YAML round-trip.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text

from schemascope.cli import main
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
    return Db(eng), eng


def _find_enc(profile, encounter_id):
    for e in profile["patients"][0]["encounters"]:
        if e.get("encounter_id") == encounter_id:
            return e
    return None


# --- #1: a table shared by two streams is counted once in full-record tokens --- #
def test_shared_table_not_double_counted(tmp_path):
    # diagnosis lives as columns ON the encounter table (as in tbl_encounter).
    stmts = [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years INTEGER)",
        "INSERT INTO pt VALUES ('P1','F',40)",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT, "
        "care_setting TEXT, admission_diagnosis_code TEXT, admission_diagnosis TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-05','Outpatient','I10','Hypertension')",
    ]
    demo = {"table": "pt", "columns": {"gender": "sex", "age_years": "age_years"}}
    enc = {"table": "enc", "date_column": "encounter_start",
           "columns": {"visit_type": "care_setting"}}
    dx = {"table": "enc", "columns": {"icd10_code": "admission_diagnosis_code",
                                      "diagnosis_name": "admission_diagnosis",
                                      "diagnosis_text": "admission_diagnosis"}}

    db_a, _ = _db(tmp_path, "a.db", stmts)
    only_enc = build_profile(db_a, Mapping.from_dict(
        {"corpus": _CORPUS, "streams": {"demographics": demo, "encounters": enc}}))

    db_b, _ = _db(tmp_path, "b.db", stmts)
    enc_and_dx = build_profile(db_b, Mapping.from_dict(
        {"corpus": _CORPUS, "streams": {"demographics": demo, "encounters": enc, "diagnoses": dx}}))

    # full-record tokens serialize each physical row once -> adding a second stream
    # over the SAME table must not change the full count...
    assert enc_and_dx["scale"]["total_tokens"] == only_enc["scale"]["total_tokens"]
    # ...but the diagnosis columns ARE new clinical content, so clinical goes up.
    assert enc_and_dx["scale"]["clinical_content_tokens"] > only_enc["scale"]["clinical_content_tokens"]
    # source_rows_total already de-dups the shared table -> 1 patient row + 1 enc row.
    assert enc_and_dx["scale"]["source_rows_total"] == 2
    assert errors(run_qa(enc_and_dx)) == []


# --- #2: a stream's `where` filter is honored by scope aggregates, not just counts --- #
def test_where_filter_applied_to_demographics(tmp_path):
    db, _ = _db(tmp_path, "w.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years INTEGER, active INTEGER)",
        "INSERT INTO pt VALUES ('P1','F',30,1),('P2','F',40,1),('P3','M',50,0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "where": "active = 1",
                         "columns": {"gender": "sex", "age_years": "age_years"}},
    }})
    p = build_profile(db, m)
    assert p["scale"]["patients_total"] == 2                       # inactive excluded
    demo = p["demographics_scope"]
    assert demo["gender_split_pct"]["female"] == 100.0            # M was filtered out
    assert demo["mean_age_years"] == 35.0                         # (30+40)/2, not /3
    assert errors(run_qa(p)) == []


# --- #7: encounter IDs unique only within a patient are not collapsed --- #
def test_patient_local_encounter_ids_counted_per_patient(tmp_path):
    db, _ = _db(tmp_path, "loc.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','F'),('P2','M')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        # both patients reuse the local id 'E1'
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01'),('P2','E1','2023-02-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"},
    }})
    p = build_profile(db, m)
    assert p["scale"]["encounters_total"] == 2       # (P1,E1) and (P2,E1), not 1
    assert p["record_depth"]["visits_per_patient_mean"] == 1.0


# --- #9a: a nested row whose link key matches no encounter gets its OWN encounter --- #
def test_orphan_note_not_pinned_to_first_encounter(tmp_path):
    db, _ = _db(tmp_path, "orph.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','F')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01')",
        "CREATE TABLE notes (patient_id TEXT, admission_id TEXT, note_full TEXT)",
        "INSERT INTO notes VALUES ('P1','A9','unlinked note text')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"},
        "history_notes": {"table": "notes", "encounter_id_column": "admission_id",
                          "columns": {"text": "note_full"}},
    }})
    p = build_profile(db, m)
    e1 = _find_enc(p, "E1")
    a9 = _find_enc(p, "A9")
    assert e1 is not None and not e1.get("history_notes")   # NOT dumped onto E1
    assert a9 is not None and a9["history_notes"][0]["text"] == "unlinked note text"
    assert validate(p) == []


# --- #9b: a corpus with no encounters yields a REAL worked patient, not "n/a" --- #
def test_no_encounter_corpus_uses_real_patient(tmp_path):
    db, _ = _db(tmp_path, "noenc.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P7','M')",
        "CREATE TABLE lab (patient_id TEXT, hemoglobin REAL)",
        "INSERT INTO lab VALUES ('P7', 14.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]},
    }})
    p = build_profile(db, m)
    assert p["patients"][0]["patient_id"] == "P7"    # real patient, not the n/a stub
    assert errors(run_qa(p)) == []


# --- #5: a profile that fails QA writes no deliverable --- #
def test_failed_qa_writes_nothing(tmp_path):
    # empty patient table -> patients_total 0 -> QA error.
    _, eng = _db(tmp_path, "empty.db", ["CREATE TABLE pt (patient_id TEXT, sex TEXT)"])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}}}})
    map_path = tmp_path / "m.yaml"
    m.to_yaml(str(map_path))
    out_json = tmp_path / "out.json"

    rc = main(["profile", "--source", f"sqlite:///{tmp_path/'empty.db'}",
               "--mapping", str(map_path), "--out-json", str(out_json)])
    assert rc == 1
    assert not os.path.exists(out_json)      # deliverable NOT written on failure


# --- numeric prescription fields (dose decimal, frequency int) must validate --- #
def test_numeric_prescription_fields_validate(tmp_path):
    db, _ = _db(tmp_path, "rx.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','F')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01')",
        # dose REAL + frequency INTEGER, exactly like tbl_medication's decimal/int
        "CREATE TABLE med (patient_id TEXT, encounter_id TEXT, medication_name TEXT, "
        "dose REAL, admin_route TEXT, frequency_value INTEGER, duration_value TEXT)",
        "INSERT INTO med VALUES ('P1','E1','Losartan',50.0,'Oral',1,'30')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"},
        "prescriptions": {"table": "med", "columns": {
            "generic_name": "medication_name", "dose": "dose", "route": "admin_route",
            "frequency": "frequency_value", "duration": "duration_value"}},
    }})
    p = build_profile(db, m)
    assert validate(p) == []                       # numeric dose/frequency stringified
    rx = p["patients"][0]["encounters"][0]["prescriptions"][0]
    assert rx["dose"] == "50.0" and rx["frequency"] == "1"
    assert errors(run_qa(p)) == []


# --- #6: link/filter overrides survive a mapping YAML round-trip --- #
def test_mapping_roundtrip_preserves_overrides(tmp_path):
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "history_notes": {"table": "notes", "encounter_id_column": "admission_id",
                          "patient_id_column": "pat_id", "where": "status = 'final'",
                          "columns": {"text": "note_full"}},
    }})
    path = tmp_path / "rt.yaml"
    m.to_yaml(str(path))
    back = Mapping.from_yaml(str(path)).streams["history_notes"]
    assert back.encounter_id_column == "admission_id"
    assert back.patient_id_column == "pat_id"
    assert back.where == "status = 'final'"

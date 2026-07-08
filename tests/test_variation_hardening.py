"""Robustness tests for supporting BOTH the raw tbl_* schema and the de-identified
'variation' from one codebase — configurable gender coding, clinical free-text
capture, wide-lab analyte names, blank-value handling, Part B encounter dedup,
patient-level streams, and a type/collation-safe patient merge.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from schemascope.io import Db
from schemascope.mapping import Mapping
from schemascope.model import to_date_str
from schemascope.profile import (build_profile, _clinical_columns, _clinical_text,
                                  _clinical_cols_by_stream, _meaningful)
from schemascope.qa import errors, run_qa
from schemascope.validate import validate

_CORPUS = {"name": "T", "provider": "T", "country": "T", "source_system": "T"}


def _db(tmp_path, name, stmts):
    eng = create_engine(f"sqlite:///{tmp_path/name}")
    with eng.begin() as c:
        for s in stmts:
            c.execute(text(s))
    return Db(eng)


def _gender(profile):
    return profile["demographics_scope"]["gender_split_pct"]


# --- configurable gender: same 'm' means female here, male by default --- #
def test_gender_value_map_overrides_default(tmp_path):
    stmts = ["CREATE TABLE pt (patient_id TEXT, sex TEXT)",
             "INSERT INTO pt VALUES ('P1','m'),('P2','m'),('P3','h')"]
    # variation coding: m = mujer = female, h = hombre = male
    mapped = Mapping.from_dict({"corpus": _CORPUS, "streams": {"demographics": {
        "table": "pt", "columns": {"gender": "sex"},
        "value_maps": {"gender": {"female": ["m", "mujer"], "male": ["h", "hombre"]}}}}})
    g = _gender(build_profile(_db(tmp_path, "vm.db", stmts), mapped))
    assert g["female"] == 66.7 and g["male"] == 33.3

    # no map -> built-in heuristic keeps m = male (raw tbl_* datasets unaffected)
    default = Mapping.from_dict({"corpus": _CORPUS, "streams": {"demographics": {
        "table": "pt", "columns": {"gender": "sex"}}}})
    g2 = _gender(build_profile(_db(tmp_path, "def.db", stmts), default))
    assert g2["male"] == 66.7 and g2["other_unknown"] == 33.3   # 'h' is unknown to the heuristic


# --- clinical tokens: valuable values only — no field names, no junk --- #
def test_clinical_text_is_strict_values_only(tmp_path):
    sm = Mapping.from_dict({"streams": {"lab_results": {
        "table": "labs", "layout": "wide", "analyte_columns": ["hemoglobin", "hba1c"],
        "clinical_extra": ["result_interpretation"]}}}).streams["lab_results"]
    cols = _clinical_columns(sm)
    assert "hemoglobin" in cols and "result_interpretation" in cols
    bucket = {"lab_results": [{
        "hemoglobin": 13.2,               # real value -> counted
        "hba1c": "  ",                    # blank -> dropped
        "result_interpretation": "anemia leve",   # free-text -> counted
    }, {"hemoglobin": "-", "hba1c": None, "result_interpretation": "N/A"}]}  # junk/null/placeholder
    txt = _clinical_text(bucket, {"lab_results": cols})
    lines = txt.split("\n")
    assert "hemoglobin" not in txt         # field/column NAME is NOT counted
    assert "13.2" in lines                 # value counted
    assert "anemia leve" in lines          # free-text value counted
    assert lines == ["13.2", "anemia leve"]  # blanks, '-', and 'N/A' all excluded


def test_meaningful_predicate():
    for good in ("13.2", "0", "anemia", "no alergias conocidas", "I10", "3+"):
        assert _meaningful(good), good
    for junk in ("", "   ", "-", "--", ".", "//", "N/A", "null", "None", "sin dato"):
        assert not _meaningful(junk), junk


# --- blank/whitespace values are not counted as populated --- #
def test_blank_values_not_counted_as_coverage(tmp_path):
    db = _db(tmp_path, "blank.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years TEXT)",
        "INSERT INTO pt VALUES ('P1','m','40'),('P2','m','  '),('P3','m','')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dxcode TEXT, dxname TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','I10','HTA'),('P2','E2','',''),('P3','E3','   ','  ')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex", "age_years": "age_years"},
                         "value_maps": {"gender": {"male": ["m"]}}},
        "encounters": {"table": "enc"},
        "diagnoses": {"table": "enc", "columns": {"icd10_code": "dxcode", "diagnosis_name": "dxname"}},
    }})
    p = build_profile(db, m)
    assert p["demographics_scope"]["age_parse_rate_pct"] == 33.3     # 1 of 3, blanks excluded
    assert p["diagnoses_scope"]["coded_records"] == 1               # only the real code
    assert p["diagnoses_scope"]["icd10_shape_match_pct"] == 100.0


# --- duplicate & NULL encounter IDs don't corrupt Part B --- #
def test_duplicate_and_null_encounter_ids(tmp_path):
    db = _db(tmp_path, "dup.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        # duplicate 'E1' and two NULLs
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01'),('P1','E1','2023-02-01'),"
        "('P1',NULL,'2023-03-01'),('P1',NULL,'2023-04-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    encs = p["patients"][0]["encounters"]
    ids = [e["encounter_id"] for e in encs]
    assert len(ids) == len(set(ids))          # no duplicate encounter objects
    assert ids.count("E1") == 1               # duplicate id collapsed to one
    assert len(encs) == 3                     # E1 + 2 synthesized for the NULLs
    assert validate(p) == []


# --- allergies & immunizations are rendered in Part B --- #
def test_patient_level_allergies_immunizations(tmp_path):
    db = _db(tmp_path, "ai.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE alg (patient_id TEXT, substance TEXT, reaction TEXT)",
        "INSERT INTO alg VALUES ('P1','Penicillin','rash')",
        "CREATE TABLE imm (patient_id TEXT, vaccine TEXT)",
        "INSERT INTO imm VALUES ('P1','COVID-19')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "allergies": {"table": "alg", "columns": {"substance": "substance", "reaction": "reaction"}},
        "immunizations": {"table": "imm", "columns": {"vaccine": "vaccine"}}}})
    p = build_profile(db, m)
    patient = p["patients"][0]
    assert patient["allergies"][0]["substance"] == "Penicillin"
    assert patient["immunizations"][0]["vaccine"] == "COVID-19"
    assert validate(p) == []


# --- mixed INTEGER/TEXT patient keys merge instead of crashing --- #
def test_mixed_type_patient_keys_merge(tmp_path):
    db = _db(tmp_path, "mix.db", [
        "CREATE TABLE pt (patient_id INTEGER, sex TEXT)",   # INTEGER key
        "INSERT INTO pt VALUES (10,'m')",
        "CREATE TABLE lab (patient_id TEXT, hemoglobin REAL)",  # TEXT key, same id
        "INSERT INTO lab VALUES ('10', 13.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]}}})
    got = dict(db.iter_patients(m))          # must not raise TypeError
    assert set(got) == {"10"}                                  # one patient, not two
    assert set(got["10"]) == {"demographics", "lab_results"}   # streams merged


# --- dates: full dates / datetimes / year-only all valid; garbage rejected --- #
def test_date_normalization_units():
    assert to_date_str("2023-01-05") == "2023-01-05"          # full date
    assert to_date_str("2023-01-05 10:30:00") == "2023-01-05"  # datetime -> date prefix
    assert to_date_str("2023-01") == "2023-01"                # year-month
    assert to_date_str("2023") == "2023"                      # HIPAA year-only
    assert to_date_str(2023) == "2023"                        # integer year
    assert to_date_str("not-a-date") is None                  # garbage -> not a date
    assert to_date_str("2023-13-01") == "2023"                # impossible month -> degrade to year
    assert to_date_str("2023-02-30") == "2023-02"             # impossible day -> degrade to month
    assert to_date_str(None) is None


def test_full_dates_and_year_only_both_validate(tmp_path):
    for label, start in (("full", "2023-06-15 08:00:00"), ("year_only", "2023")):
        db = _db(tmp_path, f"d_{label}.db", [
            "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
            "INSERT INTO pt VALUES ('P1','m')",
            "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
            f"INSERT INTO enc VALUES ('P1','E1','{start}')",
        ])
        m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
            "demographics": {"table": "pt", "columns": {"gender": "sex"}},
            "encounters": {"table": "enc", "date_column": "encounter_start"}}})
        p = build_profile(db, m)
        assert validate(p) == [], label
        enc_date = p["patients"][0]["encounters"][0]["encounter_date"]
        assert enc_date == ("2023-06-15" if label == "full" else "2023")


def test_validation_rejects_bad_date(tmp_path):
    db = _db(tmp_path, "bad.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert validate(p) == []
    p["patients"][0]["encounters"][0]["encounter_date"] = "not-a-date"   # inject garbage
    errs = validate(p)
    assert any("encounter_date" in e or "not-a-date" in e for e in errs)  # now caught


# --- blank ages don't corrupt mean_age or age-band percentages --- #
def test_blank_age_excluded_from_mean_and_bands(tmp_path):
    db = _db(tmp_path, "age.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years TEXT)",
        "INSERT INTO pt VALUES ('P1','m','40'),('P2','m',''),('P3','m','   ')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {"demographics": {
        "table": "pt", "columns": {"gender": "sex", "age_years": "age_years"},
        "value_maps": {"gender": {"male": ["m"]}}}}})
    d = build_profile(db, m)["demographics_scope"]
    assert d["mean_age_years"] == 40.0                     # not 13.3 (blanks-as-0)
    assert d["age_parse_rate_pct"] == 33.3
    bands = d["age_bands_pct"]
    assert round(sum(b["share_pct"] for b in bands), 1) == 100.0   # not 300%
    assert [b["band"] for b in bands] == ["35-44"]         # only the real age


# --- wide-lab blank cells don't inflate analyte counts/results --- #
def test_wide_lab_blank_cells_excluded(tmp_path):
    db = _db(tmp_path, "wl.db", [
        "CREATE TABLE lab (patient_id TEXT, hemoglobin TEXT, hba1c TEXT)",
        "INSERT INTO lab VALUES ('P1','13.2',''),('P2','','   ')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "lab", "columns": {}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin", "hba1c"]}}})
    L = build_profile(db, m)["laboratory_scope"]
    assert L["analyte_results"] == 1        # one real value, blanks excluded
    assert L["distinct_analytes"] == 1      # only hemoglobin has data


# --- file-SQLite fan-out doesn't depend on the caller engine's pool --- #
def test_file_sqlite_tiny_pool_profiles(tmp_path):
    # caller supplies a deliberately tiny pool; the merge uses its own NullPool
    # engine so opening one cursor per stream can't time out.
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{tmp_path/'fan.db'}", pool_size=1, max_overflow=0)
    with eng.begin() as c:
        for t in ("a", "b", "c", "d", "e"):
            c.execute(text(f"CREATE TABLE {t} (patient_id TEXT, v TEXT)"))
            c.execute(text(f"INSERT INTO {t} VALUES ('P1','x')"))
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "a", "columns": {}},
        "encounters": {"table": "b"},
        "diagnoses": {"table": "c", "columns": {"icd10_code": "v"}},
        "radiology": {"table": "d", "columns": {"report_text": "v"}},
        "history_notes": {"table": "e", "columns": {"text": "v"}}}})
    ids = [p for p, _ in Db(eng).iter_patients(m)]   # 5 streams, pool_size=1 -> must not hang
    assert ids == ["P1"]


# --- case-insensitive collation: A/a merge into one patient (original case shown) --- #
def test_case_insensitive_ids_merge(tmp_path):
    db = _db(tmp_path, "coll.db", [
        "CREATE TABLE pt (patient_id TEXT COLLATE NOCASE, sex TEXT)",
        "INSERT INTO pt VALUES ('A','m'),('a','m'),('B','h'),('b','h')",
        "CREATE TABLE lab (patient_id TEXT, hemoglobin REAL)",
        "INSERT INTO lab VALUES ('a', 13.0)",   # different case than 'A' — must still merge
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]}}})
    got = dict(db.iter_patients(m))
    assert len(got) == 2                                   # A/a and B/b, not 4
    # merged on folded key -> the 'A' demographics and 'a' lab land together
    key = [k for k in got if k.lower() == "a"][0]
    assert set(got[key]) == {"demographics", "lab_results"}


# --- blank/whitespace values don't inflate distinct counts --- #
def test_blank_distinct_counts(tmp_path):
    db = _db(tmp_path, "bd.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dxcode TEXT, dxname TEXT, spec TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','I10','HTA','CAR'),('P2','E2','','',''),('P3','E3','  ','  ','  ')",
        "CREATE TABLE lab (patient_id TEXT, encounter_id TEXT, aname TEXT)",
        "INSERT INTO lab VALUES ('P1','E1','Hemoglobin'),('P2','E2',''),('P3','E3','   ')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "columns": {"specialty_id": "spec"}},
        "diagnoses": {"table": "enc", "columns": {"icd10_code": "dxcode", "diagnosis_name": "dxname"}},
        "lab_results": {"table": "lab", "layout": "long", "columns": {"analyte_name": "aname"}}}})
    p = build_profile(db, m)
    assert p["diagnoses_scope"]["distinct_codes"] == 1        # only 'I10'
    assert p["laboratory_scope"]["distinct_analytes"] == 1    # only 'Hemoglobin'
    assert p["laboratory_scope"]["analyte_results"] == 1      # blank-name rows aren't results
    assert p["specialties_scope"]["distinct_specialties"] == 1  # only 'CAR'


# --- patient_id: trimmed, and blank/whitespace ids are not patients --- #
def test_patient_id_trim_and_blank(tmp_path):
    db = _db(tmp_path, "pid.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m'),(' P1 ','m'),('   ','m'),(NULL,'m')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}}}})
    ids = [p for p, _ in db.iter_patients(m)]
    assert ids == ["P1"]                       # trimmed to one; blank/null excluded


# --- scope patient/encounter counts skip orphan (null/blank pid) rows --- #
def test_orphan_pid_rows_excluded_from_counts(tmp_path):
    db = _db(tmp_path, "orphan.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01'),(NULL,'E2','2023-01-01'),('  ','E3','2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert p["scale"]["patients_total"] == 1
    assert p["scale"]["encounters_total"] == 1          # only P1/E1, not the orphans
    by = p["longitudinal"]["encounters_by_year"]
    assert by == [{"year": 2023, "encounters": 1, "new_patients": 1}]


# --- year-only / year-month dates bucket correctly in longitudinal --- #
def test_year_only_dates_longitudinal(tmp_path):
    db = _db(tmp_path, "yr.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023'),('P1','E2','2023-01'),('P2','E3','2024-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    L = build_profile(db, m)["longitudinal"]
    assert L["years_covered"] == 2                       # 2023..2024, not 6731
    assert [x["year"] for x in L["encounters_by_year"]] == [2023, 2024]


# --- numeric encounter fields (facility_id, specialty_id) validate in Part B --- #
def test_numeric_encounter_fields_validate(tmp_path):
    db = _db(tmp_path, "ne.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, fac INTEGER, spec INTEGER, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1',101,7,'2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start",
                       "columns": {"facility_id": "fac", "specialty_id": "spec"}}}})
    p = build_profile(db, m)
    assert validate(p) == []
    enc = p["patients"][0]["encounters"][0]
    assert enc["facility_id"] == "101" and enc["specialty_id"] == "7"


# --- non-numeric ages are not treated as parseable --- #
def test_nonnumeric_age_not_parsed(tmp_path):
    db = _db(tmp_path, "na.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age TEXT)",
        "INSERT INTO pt VALUES ('P1','m','40'),('P2','m','unknown'),('P3','m','90+')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {"demographics": {
        "table": "pt", "columns": {"gender": "sex", "age_years": "age"},
        "value_maps": {"gender": {"male": ["m"]}}}}})
    d = build_profile(db, m)["demographics_scope"]
    assert d["age_parse_rate_pct"] == 33.3               # only '40' is a number
    assert [b["band"] for b in d["age_bands_pct"]] == ["35-44"]   # 'unknown' not in 0-4


# --- nested Part B objects reject unknown fields --- #
def test_nested_partb_rejects_unknown_fields(tmp_path):
    db = _db(tmp_path, "strict.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)",
        "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert validate(p) == []
    p["patients"][0]["demographics"]["surprise"] = "x"
    assert any("surprise" in e for e in validate(p))
    p2 = build_profile(db, m)
    p2["patients"][0]["encounters"][0]["surprise_enc"] = "x"
    assert any("surprise_enc" in e for e in validate(p2))


# --- CLI output is all-or-nothing (bad path leaves no partial file) --- #
def test_atomic_outputs_rollback(tmp_path):
    from schemascope.render import write_outputs
    good_yaml = tmp_path / "ok.yaml"
    bad_json = tmp_path / "nope" / "out.json"      # parent dir doesn't exist
    profile = {"corpus": {}, "scale": {}, "patients": []}
    try:
        write_outputs(profile, str(good_yaml), str(bad_json))
        assert False, "expected an OSError"
    except OSError:
        pass
    assert not good_yaml.exists()                   # rolled back — no partial output


# === adversarial agent findings ============================================ #

# BUG1 — mapping referencing a missing column fails fast with a clear error
def test_preflight_missing_column(tmp_path):
    db = _db(tmp_path, "pf.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)", "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE lab (pid TEXT, hemoglobin REAL)", "INSERT INTO lab VALUES ('P1',13.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]}}})
    try:
        build_profile(db, m)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "lab" in str(e) and "patient_id" in str(e)   # actionable, not a raw SQL error


# BUG2 — paired_free_text_pct cannot exceed 100%
def test_paired_free_text_bounded(tmp_path):
    db = _db(tmp_path, "paired.db", [
        "CREATE TABLE dx (patient_id TEXT, encounter_id TEXT, code TEXT, name TEXT)",
        "INSERT INTO dx VALUES ('P1','E1','I10','HTA'),('P1','E1','','a'),('P1','E1',NULL,'b'),"
        "('P1','E1','','c'),('P1','E1','','d'),('P1','E1','','e')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "dx", "columns": {}},
        "diagnoses": {"table": "dx", "columns": {"icd10_code": "code", "diagnosis_name": "name"}}}})
    dx = build_profile(db, m)["diagnoses_scope"]
    assert dx["coded_records"] == 1
    assert dx["paired_free_text_pct"] == 100.0           # was 600%


# BUG3 — patient-local encounter ids: by-year encounters agree with encounters_total
def test_encounters_by_year_consistent(tmp_path):
    db = _db(tmp_path, "eby.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01'),('P2','E1','2023-05-01'),"
        "('P3','E1','2023-06-01'),('P4','E1','2023-07-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert p["scale"]["encounters_total"] == 4
    by = p["longitudinal"]["encounters_by_year"]
    assert by == [{"year": 2023, "encounters": 4, "new_patients": 4}]   # not 1 encounter / 4 new


# BUG4 — a garbage date can't corrupt first/last (they agree with the histogram)
def test_date_endpoints_agree_with_histogram(tmp_path):
    db = _db(tmp_path, "de.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-05'),('P2','E2','2024-06-01'),('P3','E3','not-a-date')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    L = build_profile(db, m)["longitudinal"]
    assert L["first_encounter_date"] == "2023-01-05"
    assert L["last_encounter_date"] == "2024-06-01"       # not None
    assert [x["year"] for x in L["encounters_by_year"]] == [2023, 2024]


# BUG5 — case-variant patient ids fold in encounter/visit counts, not just the merge
def test_case_variant_encounter_counts_fold(tmp_path):
    db = _db(tmp_path, "cv.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)", "INSERT INTO pt VALUES ('A','m')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('A','E1','2023-01-01'),('a','E1','2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert p["scale"]["patients_total"] == 1
    assert p["scale"]["encounters_total"] == 1            # A/a -> one encounter, was 2
    assert p["record_depth"]["visits_per_patient_mean"] == 1.0


# SE#2 — an all-null contact template block validates (dropped, not rejected)
def test_empty_contact_block_validates(tmp_path):
    db = _db(tmp_path, "ct.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)", "INSERT INTO pt VALUES ('P1','m')"])
    m = Mapping.from_dict({"corpus": {**_CORPUS, "contact": {"name": None, "email": None, "role": None}},
                           "streams": {"demographics": {"table": "pt", "columns": {"gender": "sex"}}}})
    p = build_profile(db, m)
    assert validate(p) == []
    assert "contact" not in p["corpus"]                  # empty block dropped


# M1 — a physical column shared by two streams on one table counts once (clinical)
def test_clinical_columns_deduped_across_shared_table():
    m = Mapping.from_dict({"streams": {
        "encounters": {"table": "t", "columns": {"chief_complaint": "note"}},
        "diagnoses": {"table": "t", "columns": {"diagnosis_text": "note"}}}})
    cc = _clinical_cols_by_stream(m.present_streams())
    assert sum(v.count("note") for v in cc.values()) == 1


# M2 — no phantom empty encounter when nested streams supply the real ones
def test_no_phantom_empty_encounter(tmp_path):
    db = _db(tmp_path, "ph.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT)", "INSERT INTO pt VALUES ('P1','m')",
        "CREATE TABLE lab (patient_id TEXT, encounter_id TEXT, hemoglobin REAL)",
        "INSERT INTO lab VALUES ('P1','E1',13.0)",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"}},
        "lab_results": {"table": "lab", "layout": "wide", "analyte_columns": ["hemoglobin"]}}})
    encs = build_profile(db, m)["patients"][0]["encounters"]
    assert [e["encounter_id"] for e in encs] == ["E1"]   # no synthesized 'P1-e1' placeholder


# === round-3 adversarial findings ========================================== #

# BUG1 — a region's facility count never exceeds facilities_total
def test_geography_region_facilities_bounded(tmp_path):
    db = _db(tmp_path, "r3g.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, fac TEXT, reg TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','CC1','North'),('P2','E2','','North'),"
        "('P3','E3','   ','North'),('P4','E4','CC2','South')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "encounters": {"table": "enc", "columns": {"facility_id": "fac", "region": "reg"}}}})
    g = build_profile(db, m)["geography"]
    assert g["facilities_total"] == 2
    assert all(d["facilities"] <= g["facilities_total"] for d in g["distribution"])
    assert {d["region"]: d["facilities"] for d in g["distribution"]} == {"North": 1, "South": 1}


# BUG2 — a non-ASCII patient_id merges to one patient (SQL/Python key parity)
def test_non_ascii_patient_id_merges(tmp_path):
    turk = "İ"  # Turkish dotted capital I; str.lower() != ASCII lower
    db = _db(tmp_path, "r3u.db", [
        "CREATE TABLE pt (patient_id TEXT, age_years INTEGER)",
        f"INSERT INTO pt VALUES ('{turk}', 40)",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        f"INSERT INTO enc VALUES ('Z','EZ','2023-01-01'),('{turk}','EI','2023-02-02')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"age_years": "age_years"}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    p = build_profile(db, m)
    assert p["scale"]["patients_total"] == 2          # not 3 (İ was split)


# BUG3 — an 'inf'/'1e400' numeric doesn't crash (coerces to None)
def test_non_finite_numeric_no_crash(tmp_path):
    from schemascope.assemble import _to_int, _to_num
    for bad in ("inf", "-inf", "1e400", "nan", "Infinity"):
        assert _to_int(bad) is None and _to_num(bad) is None
    db = _db(tmp_path, "r3i.db", [
        "CREATE TABLE pt (patient_id TEXT, age_years TEXT)", "INSERT INTO pt VALUES ('P1','1e400')",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT)", "INSERT INTO enc VALUES ('P1','E1')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"age_years": "age_years"}},
        "encounters": {"table": "enc"}}})
    p = build_profile(db, m)                            # must not raise OverflowError
    assert validate(p) == []


# BUG4 — a nan/inf value never ships as an invalid-JSON NaN/Infinity token
def test_output_is_valid_json_for_nan(tmp_path):
    import json
    from schemascope.render import render_json
    db = _db(tmp_path, "r3n.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT)", "INSERT INTO enc VALUES ('P1','E1')",
        "CREATE TABLE triage (patient_id TEXT, encounter_id TEXT, temperature_c TEXT, weight_kg TEXT)",
        "INSERT INTO triage VALUES ('P1','E1','nan','inf')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "encounters": {"table": "enc"},
        "triage_vitals": {"table": "triage",
                          "columns": {"temperature_c": "temperature_c", "weight_kg": "weight_kg"}}}})
    p = build_profile(db, m)
    js = render_json(p)
    assert "NaN" not in js and "Infinity" not in js
    assert json.loads(js) is not None                  # strict RFC parse succeeds
    v = p["patients"][0]["encounters"][0]["triage_vitals"]
    assert v["temperature_c"] is None and v["weight_kg"] is None


# === round-2 adversarial findings: internal-consistency invariants ========= #

# BUG1 — record_depth dedups a physical table shared by two streams
def test_record_depth_dedups_shared_table(tmp_path):
    db = _db(tmp_path, "rd.db", [
        "CREATE TABLE visit (patient_id TEXT, encounter_id TEXT, dt TEXT, region TEXT, dxcode TEXT, dxname TEXT)",
        "INSERT INTO visit VALUES ('P1','E1','2023-01-01','North','J06','URI'),"
        "('P2','E2','2023-02-01','North','I10','HTN')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "encounters": {"table": "visit", "date_column": "dt", "columns": {"region": "region"}},
        "diagnoses": {"table": "visit", "columns": {"icd10_code": "dxcode", "diagnosis_name": "dxname"}}}})
    p = build_profile(db, m)
    assert p["record_depth"]["fields_per_encounter_avg"] == 6.0     # not 12.0 (table counted once)


# BUG2 — a NULL encounter_id counts as a visit in the median (matches encounters_total)
def test_null_encounter_id_visit_counted(tmp_path):
    db = _db(tmp_path, "n2.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dt TEXT)",
        "INSERT INTO enc VALUES ('P1', NULL, '2023-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "dt"}}})
    rd = build_profile(db, m)["record_depth"]
    assert rd["visits_per_patient_mean"] == 1.0 and rd["visits_per_patient_median"] == 1.0


# BUG3 — a calendar-invalid date degrades to its year, agreeing with the histogram
def test_calendar_invalid_date_degrades_to_year(tmp_path):
    db = _db(tmp_path, "n3.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dt TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-05'),('P2','E2','2023-99-99')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "dt"}}})
    L = build_profile(db, m)["longitudinal"]
    assert L["last_encounter_date"] == "2023"          # not None
    assert [x["year"] for x in L["encounters_by_year"]] == [2023]


# BUG4 — visits mean and median describe the same patient population
def test_visits_mean_median_same_population(tmp_path):
    db = _db(tmp_path, "n4.db", [
        "CREATE TABLE pt (patient_id TEXT, age_years INTEGER)",
        "INSERT INTO pt VALUES ('P1',30),('P2',30),('P3',30),('P4',30),('P5',30)",
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dt TEXT)",
        "INSERT INTO enc VALUES ('P5','E1','2023-01-01'),('P5','E2','2023-02-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"age_years": "age_years"}},
        "encounters": {"table": "enc", "date_column": "dt"}}})
    rd = build_profile(db, m)["record_depth"]
    # 5 patients, 2 visits (both P5): mean 0.4, median 0 — both over ALL patients,
    # so the median is no longer 2.0 (which described only the visiting patient).
    assert rd["visits_per_patient_mean"] == 0.4 and rd["visits_per_patient_median"] == 0.0


# BUG5 — geography distribution rows match regions_covered (trimmed)
def test_geography_distribution_matches_regions_covered(tmp_path):
    db = _db(tmp_path, "n5.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dt TEXT, region TEXT, fac TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2023-01-01','North','F1'),"
        "('P2','E2','2023-01-01',' North','F1'),('P3','E3','2023-01-01','north','F2')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "encounters": {"table": "enc", "date_column": "dt",
                       "columns": {"region": "region", "facility_id": "fac"}}}})
    g = build_profile(db, m)["geography"]
    assert g["regions_covered"] == 2
    assert len(g["distribution"]) == 2                 # ' North' merged with 'North'
    assert sorted(d["region"] for d in g["distribution"]) == ["North", "north"]


# BUG6 — a scalar value_maps value is one code (not char-split; int doesn't crash)
def test_scalar_value_map_is_one_code():
    m = Mapping.from_dict({"streams": {"demographics": {
        "table": "pt", "gender_map": {"female": "mujer", "male": "hombre"}}}})
    gm = m.streams["demographics"].gender_map()
    assert gm == {"female": ["mujer"], "male": ["hombre"]}
    from schemascope.model import gender_bucket
    assert gender_bucket("mujer", gm) == "female"
    assert gender_bucket("m", gm) == "other_unknown"   # 'm' is NOT a female char now
    m2 = Mapping.from_dict({"streams": {"demographics": {"table": "pt", "gender_map": {"female": 5}}}})
    assert m2.streams["demographics"].gender_map() == {"female": ["5"]}   # no crash


# BUG7 — an encounter spanning a year boundary is counted once (by its earliest year)
def test_multiyear_encounter_counted_once(tmp_path):
    db = _db(tmp_path, "n7.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, dt TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2022-12-30'),('P1','E1','2023-01-02')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "dt"}}})
    p = build_profile(db, m)
    by = p["longitudinal"]["encounters_by_year"]
    assert p["scale"]["encounters_total"] == 1
    assert sum(r["encounters"] or 0 for r in by) == 1          # not 2
    assert by[0]["year"] == 2022                                # earliest year


# --- dialect SQL emission (Fabric/T-SQL + Postgres) — guards the untested branches --- #
def _db_for(dialect):
    from schemascope.io import Db
    class _Eng:
        pass
    eng = _Eng()
    eng.dialect = dialect
    d = Db.__new__(Db)
    d.engine = eng
    d.schema = None
    d._prep = dialect.identifier_preparer
    return d


def test_dialect_sql_emission_mssql_and_postgres():
    from sqlalchemy.dialects.mssql import dialect as mssql
    from sqlalchemy.dialects.postgresql import dialect as postgresql
    from schemascope.scope import ScopeProfiler

    m = _db_for(mssql())
    # Fabric Warehouse default collation, NOT the legacy Latin1_General_BIN2
    assert "COLLATE Latin1_General_100_BIN2_UTF8" in m.fold('"p"')
    assert "Latin1_General_BIN2 " not in m.fold('"p"') + " "
    assert m.pid_order_sql('"p"') == m.fold('"p"')            # merge order == group key
    assert m.safe_real('"a"') == 'TRY_CAST("a" AS REAL)'      # no hard CAST that can error
    assert "NOT LIKE '%.%.%'" in m.numeric_predicate('"a"')   # rejects '4.5.6'
    assert "NVARCHAR(4000)" in m.text_cast('"c"')
    spm = ScopeProfiler.__new__(ScopeProfiler); spm.db = m
    assert "CONVERT(NVARCHAR(30)" in spm._year('"d"') and ", 126)" in spm._year('"d"')  # ISO

    p = _db_for(postgresql())
    assert 'COLLATE "C"' in p.fold('"p"')
    assert "CASE WHEN" in p.safe_real('"a"') and "~ '^[0-9]" in p.safe_real('"a"')  # guarded cast
    spp = ScopeProfiler.__new__(ScopeProfiler); spp.db = p
    assert "substring(btrim(" in spp._year('"d"')


def test_default_dialect_text_cast_not_char():
    # an unlisted dialect must not blank-pad / truncate to CHAR(1) (Oracle)
    from sqlalchemy.dialects.postgresql import dialect as postgresql
    class _Dia:
        name = "firebird"
        identifier_preparer = postgresql().identifier_preparer
    db = _db_for(_Dia())
    assert "VARCHAR(4000)" in db.text_cast('"c"') and "AS CHAR)" not in db.text_cast('"c"')


# --- value_maps / clinical_extra survive a mapping round-trip --- #
def test_variation_fields_roundtrip(tmp_path):
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "pt", "columns": {"gender": "sex"},
                         "value_maps": {"gender": {"female": ["m"]}}},
        "lab_results": {"table": "labs", "clinical_extra": ["result_interpretation"]}}})
    path = tmp_path / "v.yaml"
    m.to_yaml(str(path))
    back = Mapping.from_yaml(str(path))
    assert back.streams["demographics"].gender_map() == {"female": ["m"]}
    assert back.streams["lab_results"].clinical_extra == ["result_interpretation"]


# --- implausible-but-numeric ages (150, 999, negative) don't skew mean/bands --- #
def test_implausible_age_excluded_from_mean_and_bands(tmp_path):
    db = _db(tmp_path, "age_plaus.db", [
        "CREATE TABLE pt (patient_id TEXT, sex TEXT, age_years TEXT)",
        # only 40 and 50 are plausible; 150/999 are out of range, -3 is negative
        "INSERT INTO pt VALUES ('P1','m','40'),('P2','m','50'),('P3','m','150'),('P4','m','999'),('P5','m','-3')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {"demographics": {
        "table": "pt", "columns": {"gender": "sex", "age_years": "age_years"},
        "value_maps": {"gender": {"male": ["m"]}}}}})
    d = build_profile(db, m)["demographics_scope"]
    assert d["mean_age_years"] == 45.0                       # (40+50)/2, not skewed by 150/999
    assert d["age_parse_rate_pct"] == 40.0                   # 2 of 5 rows are plausible ages
    assert [b["band"] for b in d["age_bands_pct"]] == ["35-44", "45-54"]  # no 65+ from 150
    assert round(sum(b["share_pct"] for b in d["age_bands_pct"]), 1) == 100.0


# --- a future junk date (2099) is dropped, not counted as the last encounter --- #
def test_future_date_excluded_from_longitudinal(tmp_path):
    db = _db(tmp_path, "future.db", [
        "CREATE TABLE enc (patient_id TEXT, encounter_id TEXT, encounter_start TEXT)",
        "INSERT INTO enc VALUES ('P1','E1','2020-03-01'),('P2','E2','2023-11-15'),('P3','E3','2099-01-01')",
    ])
    m = Mapping.from_dict({"corpus": _CORPUS, "streams": {
        "demographics": {"table": "enc", "columns": {}},
        "encounters": {"table": "enc", "date_column": "encounter_start"}}})
    L = build_profile(db, m)["longitudinal"]
    assert L["last_encounter_date"] == "2023-11-15"          # not 2099
    assert L["years_covered"] == 4                            # 2020..2023, not 80
    assert 2099 not in [x["year"] for x in L["encounters_by_year"]]

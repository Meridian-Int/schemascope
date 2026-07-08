"""End-to-end: profile the synthetic DB and assert the known-answer + all gates."""

from schemascope.qa import errors, run_qa
from schemascope.validate import validate


def test_profile_validates_against_schema(profile):
    assert validate(profile) == []


def test_qa_has_no_errors(profile):
    errs = errors(run_qa(profile))
    assert errs == [], [f"{e.check}: {e.message}" for e in errs]


def test_scale_counts(profile):
    s = profile["scale"]
    assert s["patients_total"] == 6                 # pt has 6 distinct patients
    assert s["encounters_total"] == 6               # 6 distinct encounter_ids
    assert s["linked_tables"] == 8
    assert s["source_rows_total"] > 0


def test_tokens_full_and_clinical(profile):
    s = profile["scale"]
    assert s["total_tokens"] > 0
    assert 0 < s["clinical_content_tokens"] <= s["total_tokens"]
    assert s["structure_tokens"] == s["total_tokens"] - s["clinical_content_tokens"]
    assert s["tokeniser"] == "tiktoken o200k_base"
    # independent cross-check encoder ran too
    assert s["total_tokens_cross_check"] > 0


def test_token_distribution_bins_cover_all_patients(profile):
    dist = profile["scale"]["token_distribution"]
    assert sum(b["patients"] for b in dist["bins"]) == 6
    p = dist["percentiles"]
    assert p["p50"] <= p["p90"] <= p["p99"]


def test_stream_inventory_present_flags(profile):
    inv = {row["stream"]: row for row in profile["stream_inventory"]}
    assert inv["demographics"]["present"] and inv["demographics"]["row_count"] == 6
    assert inv["lab_results"]["present"]
    assert inv["radiology"]["present"] is False          # not mapped
    assert inv["radiology"]["row_count"] is None


def test_diagnoses_scope(profile):
    d = profile["diagnoses_scope"]
    assert d["coding_system"] == "ICD-10"
    assert d["coded_records"] == 5
    # J06, I10, E11, bad -> 4 distinct codes; 4 of 5 records match ICD-10 shape (one "bad")
    assert d["distinct_codes"] == 4
    assert d["icd10_shape_match_pct"] == 80.0
    chapters = {c["chapter"] for c in d["by_chapter"]}
    assert {"J", "I", "E"} <= chapters
    assert "Acute upper respiratory infection" in d["top_conditions"]


def test_laboratory_wide_layout(profile):
    lab = profile["laboratory_scope"]
    assert lab["distinct_analytes"] == 3             # hemoglobin, hba1c, creatinine
    # non-null analyte cells: P001(2) + P003(3) + P005(3) + P006(1) = 9
    assert lab["analyte_results"] == 9
    assert "hemoglobin" in lab["top_analytes"]


def test_part_b_worked_patient(profile):
    p = profile["patients"][0]
    assert p["patient_id"]
    assert p["encounters"] and p["encounters"][0]["encounter_id"]
    # the worked patient (P001) carries nested clinical streams
    enc = p["encounters"][0]
    assert enc.get("history_notes") or enc.get("diagnoses") or enc.get("triage_vitals")


def test_demographics_and_geography(profile):
    demo = profile["demographics_scope"]
    assert abs(sum(demo["gender_split_pct"].values()) - 100.0) < 0.5
    assert demo["mean_age_years"] is not None
    geo = profile["geography"]
    assert geo["regions_covered"] == 3               # Central, Coast, North (in encounters)

import copy

from schemascope.qa import errors, run_qa


def test_clean_profile_passes(profile):
    assert errors(run_qa(profile)) == []


def test_clinical_exceeds_full_is_error(profile):
    bad = copy.deepcopy(profile)
    bad["scale"]["clinical_content_tokens"] = bad["scale"]["total_tokens"] + 1
    checks = {e.check for e in errors(run_qa(bad))}
    assert "tokens" in checks


def test_non_monotonic_percentiles_is_error(profile):
    bad = copy.deepcopy(profile)
    bad["scale"]["token_distribution"]["percentiles"] = {"p50": 100, "p90": 10, "p99": 5}
    checks = {e.check for e in errors(run_qa(bad))}
    assert "tokens" in checks


def test_missing_required_is_schema_error(profile):
    bad = copy.deepcopy(profile)
    del bad["scale"]["patients_total"]
    checks = {e.check for e in errors(run_qa(bad))}
    assert "schema" in checks or "patients" in checks


def test_negative_count_is_error(profile):
    bad = copy.deepcopy(profile)
    bad["scale"]["source_rows_total"] = -5
    checks = {e.check for e in errors(run_qa(bad))}
    assert "counts" in checks

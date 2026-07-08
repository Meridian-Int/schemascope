from sqlalchemy import create_engine, text

from schemascope.mapping import Mapping, autodetect


def test_yaml_round_trip(tmp_path):
    import synth
    m = synth.mapping()
    p = tmp_path / "map.yaml"
    m.to_yaml(str(p))
    m2 = Mapping.from_yaml(str(p))
    assert m2.get("lab_results").layout == "wide"
    assert m2.get("lab_results").analyte_columns == ["hemoglobin", "hba1c", "creatinine"]
    assert m2.get("demographics").columns["gender"] == "sex"
    assert m2.get("radiology") is None            # absent stream stays absent


def test_autodetect_maps_named_tables(tmp_path):
    url = f"sqlite:///{tmp_path/'named.db'}"
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE demographics (patient_id TEXT, sex TEXT, age_years INTEGER)"))
        c.execute(text("CREATE TABLE encounters (patient_id TEXT, encounter_id TEXT, encounter_date TEXT, specialty_code TEXT)"))
        c.execute(text("CREATE TABLE diagnoses (patient_id TEXT, encounter_id TEXT, diagnosis_code TEXT, diagnosis TEXT)"))
        c.execute(text("CREATE TABLE radiology (patient_id TEXT, modality TEXT, report_text TEXT)"))
    m = autodetect(engine)
    assert m.get("demographics").table == "demographics"
    assert m.get("encounters").table == "encounters"
    assert m.get("diagnoses").table == "diagnoses"
    assert m.get("radiology").table == "radiology"
    # a column hint was picked up
    assert m.get("demographics").columns.get("gender") == "sex"
    assert m.get("diagnoses").columns.get("icd10_code") == "diagnosis_code"
    # a stream with no matching table is marked absent
    assert m.get("immunizations") is None

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from sqlalchemy import create_engine

import synth
from schemascope.io import Db


@pytest.fixture()
def db_and_mapping(tmp_path):
    url = f"sqlite:///{tmp_path/'synth.db'}"
    engine = create_engine(url)
    mapping = synth.build(engine)
    return Db(engine), mapping


@pytest.fixture()
def profile(db_and_mapping):
    from schemascope.profile import build_profile
    db, mapping = db_and_mapping
    return build_profile(db, mapping)

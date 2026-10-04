"""Shared pytest fixtures.

Several pipeline tests retrain the pass-ranking model or re-collect
per-pass profiles.  Without isolation those runs overwrite the tracked
``profiles/per_pass.json`` and the cached model with machine-noise from the
test run, so both artifacts are redirected to a per-test temp directory
(seeded with a copy of the tracked profile so reads still see real data).
"""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_artifacts(tmp_path, monkeypatch):
    import stage2.pass_gating as gating
    import stage3_ml_model as s3

    tracked = gating.PROFILE_PATH
    local = tmp_path / "per_pass.json"
    if tracked.exists():
        shutil.copy(tracked, local)
    monkeypatch.setattr(gating, "PROFILE_PATH", local)
    monkeypatch.setattr(s3, "MODEL_PATH", tmp_path / "model.pkl")
    yield

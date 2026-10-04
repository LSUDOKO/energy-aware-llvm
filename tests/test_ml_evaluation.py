"""Ranker decision rule and leave-one-benchmark-out evaluation."""
import numpy as np
import pandas as pd
import pytest

import stage3_ml_model as s3
from ml_models import evaluate as ev


def test_best_positive_index_picks_highest_positive():
    assert s3.best_positive_index(np.array([0.0, 0.2, 0.7, -0.1])) == 2


@pytest.mark.parametrize("preds", [[-0.3, -0.1], [0.0, 0.0], [-1.0]])
def test_no_positive_prediction_means_no_passes(preds):
    assert s3.best_positive_index(np.array(preds)) is None


def test_rank_passes_falls_back_to_empty_when_all_predicted_losses():
    class AlwaysLoses:
        def predict(self, X):
            return -np.ones(len(X))

    seq, benefit = s3.rank_passes({}, AlwaysLoses())
    assert seq == [] and benefit == 0.0


def synthetic_frame():
    """Two kinds of programs: 'heavy' ones where optimizing pays, 'tiny' ones
    where it does not.  A feature (total_instructions) separates them."""
    rows = []
    for i, (n_instr, t_run) in enumerate([(300, 2e-4), (320, 2.2e-4), (310, 2.1e-4),
                                          (20, 1e-7), (22, 1.1e-7), (21, 1e-7)]):
        for seq, speed, t_c in (("(none)", 1.0, 0.0), ("-O2", 0.4, 6e-3)):
            key = s3.sequence_key([] if seq == "(none)" else [seq])
            row = s3.row_from({"total_instructions": n_instr}, [] if seq == "(none)" else [seq])
            row.update({"benchmark": f"b{i}.c", "sequence": key,
                        "t_compile_s": t_c, "t_shared_s": 5e-3,
                        "runtime_s_median": t_run * speed})
            rows.append(row)
    return pd.DataFrame(rows)


def test_evaluate_reports_realized_oracle_and_regret():
    res = ev.evaluate(synthetic_frame(), n_runs=10_000)
    assert res["n_benchmarks"] == 6
    for r in res["benchmarks"]:
        assert r["regret"] == pytest.approx(r["oracle"] - r["realized"])
        assert r["oracle"] >= r["realized"] - 1e-12
    assert res["mean_oracle"] >= res["mean_realized"] - 1e-12


def test_evaluate_holds_the_benchmark_out_of_training(monkeypatch):
    seen = []
    real_fit = ev._fit

    def spy(frame):
        seen.append(set(frame["benchmark"]))
        return real_fit(frame)

    monkeypatch.setattr(ev, "_fit", spy)
    ev.evaluate(synthetic_frame(), n_runs=10_000)
    assert len(seen) == 6
    assert all(len(s) == 5 for s in seen)            # one benchmark left out each time
    assert len(set().union(*seen)) == 6              # but every one is held out once


def test_markdown_lists_every_policy_and_the_ranker():
    res = ev.evaluate(synthetic_frame(), n_runs=10_000)
    md = ev.to_markdown(res)
    assert "ranker (leave-one-out)" in md and "never optimize" in md
    assert "oracle" in md

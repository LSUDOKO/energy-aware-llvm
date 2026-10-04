"""-Mbalanced labels are lifecycle-EDP savings, recomputed from timings."""
import pandas as pd
import pytest

import stage3_ml_model as s3

P = 25.0


def frame(rows):
    return pd.DataFrame(rows, columns=[
        "benchmark", "sequence", "t_compile_s", "t_shared_s", "runtime_s_median"])


ROWS = [
    ("k", "(none)", 0.0, 0.010, 100e-6),
    ("k", "-O2", 0.004, 0.010, 40e-6),
]


def test_baseline_row_is_zero_savings():
    out = s3.relabel_savings(frame(ROWS), n_runs=1000, power_w=P)
    base = out[out["sequence"] == "(none)"][s3.TARGET_COL].iloc[0]
    assert base == pytest.approx(0.0)


def test_savings_follow_the_lifecycle_formula():
    n = 1000
    out = s3.relabel_savings(frame(ROWS), n_runs=n, power_w=P)
    t_c0, t_r0 = 0.010, 100e-6
    t_c1, t_r1 = 0.014, 40e-6
    edp0 = (P * t_c0 + n * P * t_r0) * (t_c0 + n * t_r0)
    edp1 = (P * t_c1 + n * P * t_r1) * (t_c1 + n * t_r1)
    got = out[out["sequence"] == "-O2"][s3.TARGET_COL].iloc[0]
    assert got == pytest.approx((edp0 - edp1) / edp0)


def test_run_count_flips_the_label_sign():
    few = s3.relabel_savings(frame(ROWS), n_runs=1, power_w=P)
    many = s3.relabel_savings(frame(ROWS), n_runs=10 ** 6, power_w=P)
    pick = lambda df: df[df["sequence"] == "-O2"][s3.TARGET_COL].iloc[0]
    assert pick(few) < 0 < pick(many)


def test_rows_without_runtime_are_dropped():
    rows = ROWS + [("k", "-O1", 0.002, 0.010, "")]
    out = s3.relabel_savings(frame(rows), n_runs=1000, power_w=P)
    assert set(out["sequence"]) == {"(none)", "-O2"}


def test_benchmarks_are_labelled_independently():
    rows = ROWS + [("j", "(none)", 0.0, 0.020, 10e-6),
                   ("j", "-O2", 0.004, 0.020, 12e-6)]
    out = s3.relabel_savings(frame(rows), n_runs=1000, power_w=P)
    assert out[(out.benchmark == "j") & (out.sequence == "-O2")][s3.TARGET_COL].iloc[0] < 0
    assert out[(out.benchmark == "k") & (out.sequence == "-O2")][s3.TARGET_COL].iloc[0] > 0


def test_frames_without_timing_columns_pass_through():
    f = pd.DataFrame({"a": [1, 2], s3.TARGET_COL: [0.1, 0.2]})
    assert s3.relabel_savings(f).equals(f)

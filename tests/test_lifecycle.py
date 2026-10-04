"""Lifecycle (compile-once, run-n-times) energy-delay accounting."""
import pytest

from energy.lifecycle import (Cost, break_even_runs, edp_savings_pct,
                              summarize_lifecycle)

BASE = Cost(e_compile_j=1.0, t_compile_s=0.10, e_run_j=0.010, t_run_s=0.001)
FAST = Cost(e_compile_j=3.0, t_compile_s=0.30, e_run_j=0.004, t_run_s=0.0004)


def test_totals_are_linear_in_runs():
    assert BASE.total_energy(0) == pytest.approx(1.0)
    assert BASE.total_energy(100) == pytest.approx(1.0 + 100 * 0.010)
    assert BASE.total_time(100) == pytest.approx(0.10 + 100 * 0.001)
    assert BASE.edp(100) == pytest.approx(BASE.total_energy(100) * BASE.total_time(100))


def test_break_even_is_the_energy_crossover():
    n = break_even_runs(BASE, FAST)
    # extra compile 2.0 J repaid at 0.006 J/run -> 333.3 runs
    assert n == pytest.approx(2.0 / 0.006)
    assert FAST.total_energy(n) == pytest.approx(BASE.total_energy(n))
    assert FAST.total_energy(n + 1) < BASE.total_energy(n + 1)
    assert FAST.total_energy(n - 1) > BASE.total_energy(n - 1)


def test_never_repaid_when_run_is_not_cheaper():
    slower_run = Cost(5.0, 0.5, 0.010, 0.001)
    assert break_even_runs(BASE, slower_run) is None
    worse_run = Cost(5.0, 0.5, 0.020, 0.002)
    assert break_even_runs(BASE, worse_run) is None


def test_zero_when_no_costlier_and_no_slower():
    cheaper = Cost(0.9, 0.09, 0.009, 0.0009)
    assert break_even_runs(BASE, cheaper) == 0.0
    assert break_even_runs(BASE, BASE) == 0.0


def test_cheaper_build_but_costlier_run_has_no_break_even():
    cheap_build_slow_run = Cost(0.5, 0.05, 0.020, 0.002)
    assert break_even_runs(BASE, cheap_build_slow_run) is None


def test_edp_savings_sign_flips_with_run_count():
    assert edp_savings_pct(BASE, FAST, 1) < 0          # compile cost dominates
    assert edp_savings_pct(BASE, FAST, 1_000_000) > 50  # run cost dominates


def test_summary_rows():
    s = summarize_lifecycle(BASE, FAST, run_counts=(1, 1000))
    assert [r["runs"] for r in s["by_runs"]] == [1, 1000]
    assert s["by_runs"][0]["edp_savings_pct"] < 0 < s["by_runs"][1]["edp_savings_pct"]
    assert s["break_even_runs"] == pytest.approx(2.0 / 0.006)

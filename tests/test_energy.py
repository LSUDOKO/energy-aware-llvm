"""Unit tests for the energy harness statistics and counter math."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from energy.rapl_reader import EnergyReading
from energy.stats import (
    bootstrap_ci, interleave_schedules, iqr, mean, median,
    relative_savings, schedule_balance_check, summarize,
)


class TestWrapHandling:
    """Plan step 5: counter wrap must add the documented max range."""

    def _reader(self, max_range=1_000_000):
        from energy.rapl_reader import RAPLDomain, RAPLReader
        d = RAPLDomain(name="package-0", path=Path("/nonexistent"), max_range_uj=max_range)
        return RAPLReader([d], strategy="direct")

    def test_normal_delta(self):
        r = self._reader()
        a = EnergyReading(500_000, 0.0, "package-0", 1_000_000)
        b = EnergyReading(600_000, 1.0, "package-0", 1_000_000)
        assert r.read_delta(a, b) == 100_000

    def test_wrap_delta(self):
        r = self._reader()
        a = EnergyReading(900_000, 0.0, "package-0", 1_000_000)
        b = EnergyReading(100_000, 1.0, "package-0", 1_000_000)  # wrapped
        assert r.read_delta(a, b) == 200_000

    def test_negative_without_range_raises(self):
        r = self._reader(max_range=None)
        a = EnergyReading(900_000, 0.0, "package-0", None)
        b = EnergyReading(100_000, 1.0, "package-0", None)
        with pytest.raises(ValueError, match="negative energy delta"):
            r.read_delta(a, b)


class TestStats:
    def test_median_odd_even(self):
        assert median([3.0, 1.0, 2.0]) == 2.0
        assert median([4.0, 1.0, 2.0, 3.0]) == 2.5

    def test_mean(self):
        assert mean([2.0, 4.0, 6.0]) == 4.0

    def test_iqr_interpolated(self):
        lo, hi = iqr(list(range(1, 101)))  # 1..100
        assert lo == pytest.approx(25.75)
        assert hi == pytest.approx(75.25)

    def test_bootstrap_ci_contains_median(self):
        samples = [10.0, 10.5, 11.0, 10.2, 10.8, 10.1, 10.9]
        lo, hi = bootstrap_ci(samples, confidence=0.95, seed=7)
        assert lo <= median(samples) <= hi

    def test_bootstrap_ci_deterministic(self):
        samples = [5.0, 6.0, 7.0, 8.0, 9.0]
        a = bootstrap_ci(samples, seed=42)
        b = bootstrap_ci(samples, seed=42)
        assert a == b

    def test_relative_savings(self):
        assert relative_savings(100.0, 85.0) == pytest.approx(15.0)
        assert relative_savings(100.0, 110.0) == pytest.approx(-10.0)

    def test_summarize_fields(self):
        s = summarize("cfg", [1.0, 2.0, 3.0, 4.0], "J")
        assert s.n == 4 and s.unit == "J"
        assert s.iqr_low <= s.median <= s.iqr_high


class TestInterleaving:
    def test_equal_counts(self):
        sched = interleave_schedules(["A", "B"], 20)
        counts = schedule_balance_check(sched)
        assert counts == {"A": 20, "B": 20}
        assert len(sched) == 40

    def test_rotation_alternates(self):
        sched = interleave_schedules(["A", "B"], 4)
        # Round 0: A,B | Round 1: B,A | Round 2: A,B | Round 3: B,A
        assert sched[:4] == ["A", "B", "B", "A"]
        assert sched[4:8] == ["A", "B", "B", "A"]

    def test_three_configs(self):
        sched = interleave_schedules(["A", "B", "C"], 3)
        counts = schedule_balance_check(sched)
        assert all(v == 3 for v in counts.values())

    def test_no_immediate_adjacent_repeat_rounds(self):
        sched = interleave_schedules(["A", "B"], 10)
        pairs = [(sched[i], sched[i + 1]) for i in range(0, len(sched), 2)]
        # every round contains both configs
        assert all(a != b for a, b in pairs)

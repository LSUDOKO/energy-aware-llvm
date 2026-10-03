"""Harness behavior tests using a deterministic fake energy counter."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from energy.harness import EnergyHarness, HarnessConfig
from energy.rapl_reader import EnergyReading


class FakeCounter:
    """Simulates a RAPL counter: energy accrues at a per-config wattage."""

    def __init__(self, watts_by_config: dict[str, float] | None = None):
        self.watts = watts_by_config or {}
        self._uj = 5_000_000
        self._max = 10_000_000
        self._clock = 0.0
        self.available_flag = True

    def available(self):
        return self.available_flag

    def domain_name(self):
        return "fake-package"

    def read(self) -> EnergyReading:
        return EnergyReading(self._uj, self._clock, "fake-package", self._max)

    # test hook: advance simulated time/energy
    def burn(self, seconds: float, config: str | None = None):
        self._clock += seconds
        watts = self.watts.get(config, 0.0)
        self._uj = int((self._uj + watts * seconds * 1e6) % self._max)


class ConfigNames:
    """Stubs the RAPLReader interface for the harness."""

    def __init__(self, fake: FakeCounter):
        self.fake = fake
        self.strategy = "direct"

    def available(self):
        return self.fake.available()

    def domain_name(self):
        return self.fake.domain_name()

    def read(self):
        return self.fake.read()

    def read_delta(self, before: EnergyReading, after: EnergyReading) -> int:
        delta = after.package_uj - before.package_uj
        if delta < 0 and after.max_range_uj:
            delta += after.max_range_uj
        return delta

    def describe(self):
        return "fake counter"


def make_workloads(fake: FakeCounter):
    """Each config 'compiles' by sleeping (measurable real time) and
    accruing simulated counter energy at its config wattage."""

    def make(name: str, seconds: float, watts: float):
        fake.watts[name] = watts

        def work():
            time.sleep(seconds)
            fake.burn(seconds, name)

        return work

    return {
        "conventional": make("conventional", 0.010, 8.0),
        "merged": make("merged", 0.006, 7.0),
    }


def test_idle_correction_and_savings():
    fake = FakeCounter()
    reader = ConfigNames(fake)
    # Idle power baseline: 5 W -> idle sampling records that rate.
    idle_watts_holder = {"w": 5.0}
    fake.watts["__idle__"] = idle_watts_holder["w"]

    harness = EnergyHarness(reader, HarnessConfig(n_trials=6, warmup_runs=1, idle_sample_seconds=0.05))
    # Monkeypatch idle measurement to a fixed 5W profile to keep the test fast/deterministic.
    from energy.harness import IdleProfile
    harness.measure_idle_power = lambda: IdleProfile(power_w=5.0, sample_count=5, duration_s=0.05)

    results = harness.run_experiment(make_workloads(fake))
    summaries = harness.summarize(results)

    assert len(results) == 12  # 6 rounds x 2 configs
    counts = {}
    for r in results:
        counts[r.config] = counts.get(r.config, 0) + 1
    assert counts == {"conventional": 6, "merged": 6}

    # merged should show positive energy savings vs conventional
    savings = summaries["savings"]["merged"]
    assert savings["energy_savings_pct"] > 0
    assert savings["time_savings_pct"] > 0

    # idle correction: net < raw for every trial
    for r in results:
        assert r.net_energy_j is not None
        assert r.idle_energy_j is not None
        assert r.net_energy_j < r.raw_energy_j


def test_time_only_mode():
    fake = FakeCounter()
    fake.available_flag = False
    reader = ConfigNames(fake)
    harness = EnergyHarness(reader, HarnessConfig(n_trials=3, warmup_runs=0, idle_sample_seconds=0.01))

    def work_a():
        fake.burn(0.001)

    results = harness.run_experiment({"only": work_a})
    assert all(r.net_energy_j is None for r in results)
    summaries = harness.summarize(results)
    assert summaries["energy"] is None
    assert summaries["savings"] is None
    assert summaries["time"]["only"].n == 3


def test_batching_divides():
    fake = FakeCounter()
    reader = ConfigNames(fake)
    harness = EnergyHarness(
        reader,
        HarnessConfig(n_trials=2, warmup_runs=0, idle_sample_seconds=0.01, batch_repeats=4),
    )
    harness.measure_idle_power = lambda: None  # time-only

    calls = {"n": 0}

    def work():
        calls["n"] += 1
        fake.burn(0.001)

    results = harness.run_experiment({"solo": work})
    assert calls["n"] == 2 * 4
    for r in results:
        assert r.time_per_invocation_s == pytest.approx(r.duration_s / 4)


def test_warmup_runs_are_unrecorded():
    fake = FakeCounter()
    reader = ConfigNames(fake)
    calls = {"n": 0}

    def work():
        calls["n"] += 1
        fake.burn(0.001)

    harness = EnergyHarness(reader, HarnessConfig(n_trials=2, warmup_runs=2, idle_sample_seconds=0.01))
    harness.measure_idle_power = lambda: None
    harness.run_experiment({"cfg": work})
    # 2 warmups + 2 trials
    assert calls["n"] == 4


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        HarnessConfig(n_trials=1).validated()
    with pytest.raises(ValueError):
        HarnessConfig(batch_repeats=0).validated()

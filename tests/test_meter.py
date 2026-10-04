"""EnergyMeter: batching, idle correction, and honest time-only fallback."""
import pytest

from energy.meter import EnergyMeter
from energy.rapl_reader import EnergyReading, UnavailableRAPL


class FakeReader:
    """Counter that advances by watts * (simulated seconds) on each burn()."""

    def __init__(self, watts=10.0, max_uj=2_000_000_000):
        self.watts, self.max_uj = watts, max_uj
        self.uj, self.clock = 1_000_000, 0.0

    def available(self):
        return True

    def burn(self, seconds):
        self.clock += seconds
        self.uj = int((self.uj + self.watts * seconds * 1e6) % self.max_uj)

    def read(self):
        return EnergyReading(self.uj, self.clock, "fake", self.max_uj)

    def read_delta(self, a, b):
        d = b.package_uj - a.package_uj
        return d + self.max_uj if d < 0 else d


def test_time_only_when_counter_unavailable():
    meter = EnergyMeter(reader=UnavailableRAPL(reason="test"))
    assert meter.source == "time-only" and not meter.available
    m = meter.measure(lambda: sum(range(100)), calls=5)
    assert m.energy_j is None and m.raw_energy_j is None
    assert m.calls == 5 and m.seconds > 0 and m.source == "time-only"


def test_energy_per_call_with_idle_correction(monkeypatch):
    rd = FakeReader(watts=10.0)
    meter = EnergyMeter(reader=rd, idle_seconds=0.0)
    meter._idle_w = 4.0  # known idle power

    clock = {"t": 0.0}
    monkeypatch.setattr("energy.meter.time.perf_counter", lambda: clock["t"])

    def work():
        clock["t"] += 0.002       # each call lasts 2 ms ...
        rd.burn(0.002)            # ... drawing 10 W

    m = meter.measure(work, calls=10)
    assert m.seconds == pytest.approx(0.002)
    assert m.raw_energy_j == pytest.approx(10.0 * 0.002)
    assert m.energy_j == pytest.approx((10.0 - 4.0) * 0.002)
    assert m.source == "rapl"


def test_counter_wrap_is_handled(monkeypatch):
    rd = FakeReader(watts=10.0, max_uj=1_050_000)   # wraps within one batch
    rd.uj = 1_000_000
    meter = EnergyMeter(reader=rd, idle_seconds=0.0)
    meter._idle_w = 0.0
    clock = {"t": 0.0}
    monkeypatch.setattr("energy.meter.time.perf_counter", lambda: clock["t"])

    def work():
        clock["t"] += 0.001
        rd.burn(0.001)

    m = meter.measure(work, calls=2)
    assert m.raw_energy_j == pytest.approx(10.0 * 0.001, rel=1e-3)


def test_auto_batch_reaches_min_window(monkeypatch):
    rd = FakeReader()
    meter = EnergyMeter(reader=rd, min_window_s=0.05, idle_seconds=0.0)
    meter._idle_w = 0.0
    clock = {"t": 0.0}
    monkeypatch.setattr("energy.meter.time.perf_counter", lambda: clock["t"])

    def work():
        clock["t"] += 0.001
        rd.burn(0.001)

    m = meter.measure(work)
    assert m.calls >= 50
    assert m.calls * m.seconds >= 0.05 - 1e-9


def test_energy_never_negative(monkeypatch):
    rd = FakeReader(watts=1.0)
    meter = EnergyMeter(reader=rd, idle_seconds=0.0)
    meter._idle_w = 50.0          # idle estimate above actual draw
    clock = {"t": 0.0}
    monkeypatch.setattr("energy.meter.time.perf_counter", lambda: clock["t"])

    def work():
        clock["t"] += 0.01
        rd.burn(0.01)

    assert meter.measure(work, calls=3).energy_j == 0.0

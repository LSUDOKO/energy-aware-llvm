"""Single-call energy meter built on the RAPL reader.

``EnergyHarness`` answers "which of these configurations is better" with
interleaved paired trials.  The compile pipeline also needs a plain
"how many joules did *this* step cost" primitive (per-stage ``E_compile``,
``E_run`` of one native program) without the full experiment machinery.

``EnergyMeter.measure(fn)`` batches ``fn`` until the window is long enough
for the counter to resolve (RAPL updates roughly every millisecond), applies
the plan's idle correction ``E = E_raw - P_idle * T`` and returns *per call*
numbers.  Without a readable counter every energy field is ``None`` and the
caller must label its numbers as time-only or estimated -- the meter never
invents joules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from energy.rapl_reader import RAPLReader


@dataclass(frozen=True)
class Measurement:
    """Per-call result of one metered batch."""

    seconds: float                 # wall time per call
    energy_j: float | None         # idle-corrected joules per call (None: no counter)
    raw_energy_j: float | None     # uncorrected joules per call
    calls: int                     # batch size actually used
    source: str                    # "rapl" | "time-only"


class EnergyMeter:
    def __init__(self, reader=None, min_window_s: float = 0.05,
                 idle_seconds: float = 0.5, max_calls: int = 1_000_000):
        self.reader = reader if reader is not None else RAPLReader.create()
        self.min_window_s = min_window_s
        self.idle_seconds = idle_seconds
        self.max_calls = max_calls
        self._idle_w: float | None = None

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return bool(self.reader.available())

    @property
    def source(self) -> str:
        return "rapl" if self.available else "time-only"

    def idle_power_w(self) -> float | None:
        """Mean package power while this process sleeps (cached)."""
        if not self.available:
            return None
        if self._idle_w is None:
            before = self.reader.read()
            time.sleep(self.idle_seconds)
            after = self.reader.read()
            dt = after.timestamp - before.timestamp
            if dt <= 0:
                return None
            self._idle_w = (self.reader.read_delta(before, after) / 1e6) / dt
        return self._idle_w

    # ------------------------------------------------------------------ #
    def measure(self, fn: Callable[[], object], calls: int | None = None
                ) -> Measurement:
        """Run ``fn`` in a batch and return per-call time and energy.

        ``calls=None`` auto-sizes the batch so the window is at least
        ``min_window_s`` long (one calibration call is made first).
        """
        if calls is None:
            t0 = time.perf_counter()
            fn()
            one = max(time.perf_counter() - t0, 1e-9)
            calls = int(min(self.max_calls, max(1, self.min_window_s / one)))
        calls = max(1, calls)

        idle_w = self.idle_power_w()
        before = self.reader.read() if self.available else None
        t0 = time.perf_counter()
        for _ in range(calls):
            fn()
        elapsed = time.perf_counter() - t0
        after = self.reader.read() if self.available else None

        if before is None or after is None:
            return Measurement(elapsed / calls, None, None, calls, "time-only")

        raw_j = self.reader.read_delta(before, after) / 1e6
        net_j = raw_j - (idle_w or 0.0) * elapsed
        return Measurement(
            seconds=elapsed / calls,
            energy_j=max(0.0, net_j) / calls,
            raw_energy_j=raw_j / calls,
            calls=calls,
            source="rapl",
        )

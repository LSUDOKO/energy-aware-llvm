"""Energy measurement harness (plan Part I, steps 3-8).

Implements the project plan's measurement methodology literally:

  step 3  measure idle power P_idle over T_idle seconds
  step 4  warm-up run (optional, policy-controlled)
  step 5  record counter values before/after each trial, handle wrap
  step 6  E_compile = E_raw - P_idle * T   (idle correction; raw kept too)
  step 7  >=20 interleaved paired trials via balanced interleaving

The harness is energy-source agnostic: with a working RAPLReader it records
true joules; without one it degrades to time-only mode (energy=None) so the
rest of the pipeline (statistics, reports, regression tracking) still runs.

Design note on batching (plan step 6): "Compile a source file repeatedly in
one measured batch when a single run is too short." A trial here can wrap
several invocations of the measured callable; the recorded duration and
energy cover the whole batch, and the report divides by batch count.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from energy.rapl_reader import EnergyReading, RAPLReader


@dataclass(frozen=True)
class HarnessConfig:
    n_trials: int = 20                    # plan: "at least 20 interleaved trials"
    warmup_runs: int = 1                  # plan step 4: one unrecorded run
    idle_sample_seconds: float = 2.0      # plan step 3: T_idle
    idle_sample_interval: float = 0.05    # sampling granularity for idle power
    batch_repeats: int = 1                # invocations per trial (batching)
    cooldown_between_trials: float = 0.0  # seconds; keeps thermal policy explicit
    confidence: float = 0.95

    def validated(self) -> "HarnessConfig":
        if self.n_trials < 2:
            raise ValueError("need at least 2 trials for interleaving")
        if self.batch_repeats < 1:
            raise ValueError("batch_repeats must be >= 1")
        if self.idle_sample_seconds <= 0:
            raise ValueError("idle_sample_seconds must be positive")
        return self


@dataclass
class TrialResult:
    config: str
    trial_index: int                # which interleaved trial
    duration_s: float
    raw_energy_j: float | None      # uncorrected counter delta
    idle_energy_j: float | None     # P_idle * T component
    net_energy_j: float | None      # E_raw - P_idle*T (plan step 6)
    batch_repeats: int = 1          # invocations packed into this trial

    @property
    def energy_per_invocation_j(self) -> float | None:
        if self.net_energy_j is None:
            return None
        return self.net_energy_j / self.batch_repeats

    @property
    def time_per_invocation_s(self) -> float:
        return self.duration_s / self.batch_repeats


@dataclass
class IdleProfile:
    power_w: float                  # P_idle in watts
    sample_count: int
    duration_s: float

    def describe(self) -> str:
        return f"P_idle = {self.power_w:.3f} W (from {self.sample_count} samples over {self.duration_s:.1f}s)"


class EnergyHarness:
    """Runs interleaved paired trials of named configurations."""

    def __init__(self, reader: RAPLReader, config: HarnessConfig | None = None):
        self.reader = reader
        self.config = (config or HarnessConfig()).validated()

    # ------------------------------------------------------------------ #
    # Step 3: idle power
    # ------------------------------------------------------------------ #

    def measure_idle_power(self) -> IdleProfile | None:
        """Sample the counter while the machine does nothing; P = dE/dT.

        Returns None when no energy source is available (time-only mode).
        """
        if not self.reader.available():
            return None
        cfg = self.config
        samples: list[int] = []
        stamps: list[float] = []
        deadline = time.monotonic() + cfg.idle_sample_seconds
        last = self.reader.read()
        samples.append(last.package_uj)
        stamps.append(last.timestamp)
        while time.monotonic() < deadline:
            time.sleep(cfg.idle_sample_interval)
            cur = self.reader.read()
            samples.append(cur.package_uj)
            stamps.append(cur.timestamp)
        first, last = samples[0], samples[-1]
        before = EnergyReading(first, stamps[0], "idle", None)
        after = EnergyReading(last, stamps[-1], "idle", None)
        # Wrap handling reuses the reader's delta logic; max range unknown here
        # is acceptable because idle windows are short relative to the wrap.
        delta_uj = after.package_uj - before.package_uj
        if delta_uj < 0:
            raise RuntimeError("energy counter wrapped during idle sampling; increase idle_sample_seconds")
        elapsed = stamps[-1] - stamps[0]
        if elapsed <= 0:
            raise RuntimeError("no time elapsed during idle sampling")
        return IdleProfile(power_w=(delta_uj / 1e6) / elapsed, sample_count=len(samples), duration_s=elapsed)

    # ------------------------------------------------------------------ #
    # Steps 4-7: paired trials
    # ------------------------------------------------------------------ #

    def run_experiment(
        self,
        configs: dict[str, Callable[[], object]],
        progress: Callable[[str], None] | None = None,
    ) -> list[TrialResult]:
        """Run interleaved paired trials for all configurations.

        `configs` maps config name -> zero-arg callable that performs the
        measured work once. Trials are scheduled in a balanced interleaved
        order (see energy.stats.interleave_schedules).

        Warm-up (step 4) runs each config once, unrecorded, before trials.
        """
        cfg = self.config
        names = list(configs.keys())
        if len(names) == 0:
            raise ValueError("no configurations given")

        # Step 4: warm-up
        if cfg.warmup_runs > 0:
            if progress:
                progress(f"warm-up: {cfg.warmup_runs} unrecorded run(s) per config")
            for _ in range(cfg.warmup_runs):
                for name in names:
                    configs[name]()

        # Step 7: interleaved schedule
        from energy.stats import interleave_schedules
        schedule = interleave_schedules(names, cfg.n_trials)

        idle = self.measure_idle_power()
        if progress:
            if idle:
                progress(idle.describe())
            else:
                progress("no energy source: running in time-only mode")

        results: list[TrialResult] = []
        reader_available = self.reader.available()
        for i, name in enumerate(schedule):
            if cfg.cooldown_between_trials > 0:
                time.sleep(cfg.cooldown_between_trials)

            fn = configs[name]
            # Step 5: record counters immediately before/after the work.
            before = self.reader.read() if reader_available else None
            t0 = time.monotonic()
            for _ in range(cfg.batch_repeats):
                fn()
            t1 = time.monotonic()
            after = self.reader.read() if reader_available else None

            duration = t1 - t0
            raw_j = None
            idle_j = None
            net_j = None
            if before is not None and after is not None:
                delta_uj = self.reader.read_delta(before, after)
                raw_j = delta_uj / 1e6
                if idle is not None:
                    idle_j = idle.power_w * duration
                    net_j = max(0.0, raw_j - idle_j)  # clamp tiny negative noise

            results.append(TrialResult(
                config=name,
                trial_index=i,
                duration_s=duration,
                raw_energy_j=raw_j,
                idle_energy_j=idle_j,
                net_energy_j=net_j,
                batch_repeats=cfg.batch_repeats,
            ))
            if progress:
                e_txt = f"{net_j:.4f} J" if net_j is not None else "n/a"
                progress(f"trial {i + 1:2d}/{len(schedule):2d} [{name:>9}] {duration * 1000:8.2f} ms  net E: {e_txt}")

        return results

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #

    def summarize(self, results: list[TrialResult]) -> dict:
        """Summaries per config for time and net energy, plus savings.

        Returns {"time": {config: Summary}, "energy": {...} | None,
                 "savings": {...} | None} with savings computed on medians
        relative to the FIRST configuration (treated as baseline).
        """
        from energy.stats import summarize, relative_savings

        names = sorted({r.config for r in results})
        by_config_time: dict[str, list[float]] = {n: [] for n in names}
        by_config_energy: dict[str, list[float]] = {n: [] for n in names}

        for r in results:
            by_config_time[r.config].append(r.time_per_invocation_s)
            if r.energy_per_invocation_j is not None:
                by_config_energy[r.config].append(r.energy_per_invocation_j)

        confidence = self.config.confidence
        time_summaries = {
            n: summarize(n, samples, "s", confidence)
            for n, samples in by_config_time.items()
        }

        energy_summaries = None
        savings = None
        if all(by_config_energy[n] for n in names):
            energy_summaries = {
                n: summarize(n, samples, "J", confidence)
                for n, samples in by_config_energy.items()
            }
            baseline = names[0]
            baseline_med = energy_summaries[baseline].median
            savings = {}
            for n in names[1:]:
                savings[n] = {
                    "energy_savings_pct": relative_savings(baseline_med, energy_summaries[n].median),
                    "time_savings_pct": relative_savings(time_summaries[baseline].median, time_summaries[n].median),
                }

        return {
            "time": time_summaries,
            "energy": energy_summaries,
            "savings": savings,
        }

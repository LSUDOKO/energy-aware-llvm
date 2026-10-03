"""Statistics for the energy harness (plan Part I, steps 7-8).

Trial data is noisy (CPU frequency scaling, temperature drift, cache state),
so every reported number follows the plan's methodology:

  * interleaved paired trials to cancel drift bias,
  * median + interquartile range as the primary summary,
  * arithmetic mean and a bootstrap confidence interval for stability.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass


def interleave_schedules(config_names: list[str], n_trials: int) -> list[str]:
    """Build a balanced interleaved schedule: A,B,A,B,... then B,A,B,A,...

    Alternating the rotation between rounds cancels first/last-in-round bias
    from temperature and cache state (plan step 7: "interleaving limits bias
    from temperature and system drift").

    Returns a list of length n_trials * len(config_names).
    """
    if not config_names:
        return []
    schedule: list[str] = []
    for round_idx in range(n_trials):
        rotation = config_names[round_idx % len(config_names):] + config_names[:round_idx % len(config_names)]
        schedule.extend(rotation)
    return schedule


def median(samples: list[float]) -> float:
    ordered = sorted(samples)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of empty sample list")
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mean(samples: list[float]) -> float:
    if not samples:
        raise ValueError("mean of empty sample list")
    return sum(samples) / len(samples)


def iqr(samples: list[float]) -> tuple[float, float]:
    """Interquartile range (Q1, Q3) via linear interpolation (numpy style)."""
    ordered = sorted(samples)
    n = len(ordered)
    if n == 0:
        raise ValueError("iqr of empty sample list")
    if n == 1:
        return ordered[0], ordered[0]

    def percentile(p: float) -> float:
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        if f == c:
            return ordered[f]
        return ordered[f] + (ordered[c] - ordered[f]) * (k - f)

    return percentile(0.25), percentile(0.75)


def bootstrap_ci(
    samples: list[float],
    confidence: float = 0.95,
    n_boot: int = 10_000,
    seed: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap CI for the median.

    Deterministic given the seed so reports are reproducible.
    """
    if len(samples) < 3:
        raise ValueError("bootstrap CI needs at least 3 samples")
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_boot):
        resample = [rng.choice(samples) for _ in range(len(samples))]
        stats.append(median(resample))
    stats.sort()
    alpha = 1.0 - confidence
    lo_idx = int((alpha / 2) * n_boot)
    hi_idx = int((1 - alpha / 2) * n_boot)
    lo_idx = min(lo_idx, n_boot - 1)
    hi_idx = min(hi_idx, n_boot - 1)
    return stats[lo_idx], stats[hi_idx]


@dataclass(frozen=True)
class Summary:
    """Summary of one metric across trials for one configuration."""

    config: str
    n: int
    median: float
    mean: float
    iqr_low: float
    iqr_high: float
    ci_low: float
    ci_high: float
    unit: str

    def format(self, precision: int = 4) -> str:
        spread = self.iqr_high - self.iqr_low
        return (
            f"{self.config:>14}: median={self.median:.{precision}f} "
            f"(IQR {self.iqr_low:.{precision}f}-{self.iqr_high:.{precision}f}, "
            f"spread {spread:.{precision}f}) "
            f"mean={self.mean:.{precision}f} "
            f"95% CI [{self.ci_low:.{precision}f}, {self.ci_high:.{precision}f}] "
            f"n={self.n} {self.unit}"
        )


def summarize(config: str, samples: list[float], unit: str, confidence: float = 0.95) -> Summary:
    med = median(samples)
    q1, q3 = iqr(samples)
    lo, hi = bootstrap_ci(samples, confidence=confidence)
    return Summary(
        config=config,
        n=len(samples),
        median=med,
        mean=mean(samples),
        iqr_low=q1,
        iqr_high=q3,
        ci_low=lo,
        ci_high=hi,
        unit=unit,
    )


def relative_savings(baseline_median: float, merged_median: float) -> float:
    """Plan step 8: Saving% = (E_base_med - E_merged_med) / E_base_med * 100."""
    if baseline_median == 0:
        raise ValueError("baseline median is zero; savings undefined")
    return (baseline_median - merged_median) / baseline_median * 100.0


def schedule_balance_check(schedule: list[str]) -> dict[str, int]:
    """Sanity helper for tests: per-config trial counts in a schedule."""
    return dict(Counter(schedule))

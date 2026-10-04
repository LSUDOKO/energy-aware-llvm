"""Lifecycle energy-delay accounting: compile once, run many times.

The project plan reports ``E_compile`` and ``E_run`` separately and asks for
the "EDP vs compile cost" trade-off.  Multiplying a one-off compile energy by
a single run's delay (what a naive ``E_compile x T_run`` does) mixes units of
different frequency and says nothing about whether extra optimization pays
off.  The honest question is:

    How many times must the program run before the energy spent on extra
    optimization is repaid by cheaper executions?

For a program compiled once and executed ``n`` times::

    E_total(n) = E_compile + n * E_run
    T_total(n) = T_compile + n * T_run
    EDP(n)     = E_total(n) * T_total(n)

``break_even_runs`` solves ``E_total_opt(n) == E_total_base(n)`` for ``n``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cost:
    """Measured (or estimated) cost of one build configuration."""

    e_compile_j: float
    t_compile_s: float
    e_run_j: float
    t_run_s: float

    def total_energy(self, n_runs: float) -> float:
        return self.e_compile_j + n_runs * self.e_run_j

    def total_time(self, n_runs: float) -> float:
        return self.t_compile_s + n_runs * self.t_run_s

    def edp(self, n_runs: float) -> float:
        return self.total_energy(n_runs) * self.total_time(n_runs)


def break_even_runs(base: Cost, opt: Cost) -> float | None:
    """Smallest ``n`` such that ``opt`` uses no more total energy than ``base``
    for every run count ``>= n``; ``None`` if no such ``n`` exists.

    * ``opt`` no costlier to build and no costlier to run -> ``0.0``
    * ``opt`` costlier to run per execution                -> ``None``
    * otherwise the crossover ``extra_compile / saved_per_run``
    """
    extra_compile = opt.e_compile_j - base.e_compile_j
    saved_per_run = base.e_run_j - opt.e_run_j
    if saved_per_run < 0 or (saved_per_run == 0 and extra_compile > 0):
        return None
    if extra_compile <= 0:
        return 0.0
    return extra_compile / saved_per_run


def edp_savings_pct(base: Cost, opt: Cost, n_runs: float) -> float:
    """``(EDP_base - EDP_opt) / EDP_base * 100`` at a given run count."""
    base_edp = base.edp(n_runs)
    if base_edp <= 0:
        return 0.0
    return (base_edp - opt.edp(n_runs)) / base_edp * 100.0


def summarize_lifecycle(base: Cost, opt: Cost,
                        run_counts: tuple[int, ...] = (1, 100, 10_000, 1_000_000)
                        ) -> dict:
    """Break-even point plus EDP savings across run counts (for reports/UI)."""
    return {
        "break_even_runs": break_even_runs(base, opt),
        "by_runs": [
            {"runs": n,
             "edp_base": base.edp(n),
             "edp_opt": opt.edp(n),
             "edp_savings_pct": edp_savings_pct(base, opt, n)}
            for n in run_counts
        ],
    }

"""Stage 2 — energy-aware pass gating.

Implements the plan's decision rule verbatim:

    run optional pass p  iff   B̂_p  >  λ·Ê_p  +  µ·T̂_p

where

  * ``B̂_p``   predicted benefit of running p: the compile-time-independent
              runtime energy saved over the program's expected lifetime,
              derived from the static cost model
              ``B̂ = amortization × E_run_est × ΔC/C`` (joules);
  * ``Ê_p``   predicted compile-time energy cost of p.  From the measured
              per-pass profile (``profiles/per_pass.json``) when present,
              otherwise the documented software estimate ``P̂ × T̂_p`` with
              ``P̂`` = configured package power (default 25 W — a TDP-class
              placeholder for the Ryzen 7 5700U, replaced by RAPL-derived
              power on the lab machine);
  * ``T̂_p``   predicted compile-time cost of p in seconds — measured
              directly by timing the pass on a copy of the module (or taken
              from the measured profile);
  * ``λ, µ``  budget weights chosen per mode: ``eco`` prices compile
              energy/time very high (skip most passes), ``balanced`` is the
              default trade-off, ``perf`` prices them low (run almost every
              pass that helps).

Every decision carries a human-readable reason, which is the auditable
"why was this pass run/skipped" log required by the plan.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from stage2.extractor import static_cost
from backend.passes import apply_pass, AVAILABLE_PASSES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "profiles" / "per_pass.json"

# (lambda, mu) per budget.  Units: lambda [J utility / J compile],
# mu [J utility / s compile].
#   eco     — prices compile energy/time very high: only passes whose
#             lifetime benefit easily repays the compile cost are run.
#   balanced— default trade-off (plan's default mode).
#   perf    — prices compile cost low: run almost every pass that helps.
BUDGETS: dict[str, tuple[float, float]] = {
    "eco": (1000.0, 100.0),
    "balanced": (10.0, 1.0),
    "perf": (1.0, 0.1),
}

# Estimate constants (documented software-estimate fallbacks).
DEFAULT_POWER_W = 25.0        # package power placeholder until RAPL available
DEFAULT_RUN_J_PER_UNIT = 1e-6  # E_run estimate: joules per static-cost unit
DEFAULT_AMORTIZATION = 1_000_000  # expected lifetime executions of the binary


@dataclass
class PassDecision:
    name: str
    run: bool
    benefit: float        # B̂ [J]
    energy_cost: float    # Ê [J]
    time_cost: float      # T̂ [s]
    threshold: float      # λ·Ê + µ·T̂ [J]
    static_delta: float   # ΔC (positive = cost reduced)
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_profiles(path: str | Path | None = None) -> dict:
    """Load measured per-pass profiles, or {} when none exist yet.

    Expected JSON schema::

        {"meta": {"power_w": 12.3, "run_j_per_unit": ..., "amortization": ...},
         "passes": {"-sroa": {"t_seconds": 1.2e-4, "energy_j": 1.5e-3}, ...}}
    """
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_profiles(profiles: dict, path: str | Path | None = None) -> Path:
    """Persist a measured per-pass profile (used by ml_models/collect_dataset)."""
    p = Path(path) if path else PROFILE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profiles, indent=2, sort_keys=True))
    return p


def measure_pass_cost(ir_text: str, name: str, repeats: int = 3) -> tuple[float, float]:
    """Measure (T̂ seconds, Ê joules) of one pass on a copy of the module.

    T̂ is the median wall time of ``repeats`` real executions of the pass.
    Ê = P̂ × T̂ (software estimate, documented limitation when RAPL is
    unavailable), overridden by a measured profile energy when present.
    """
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        apply_pass(ir_text, name)
        times.append(time.perf_counter() - t0)
    times.sort()
    t = times[len(times) // 2]
    return t, t * DEFAULT_POWER_W


class CostBenefitGate:
    """Sequential gated scheduling of optional passes."""

    def __init__(
        self,
        budget: str = "balanced",
        profiles: dict | None = None,
        power_w: float | None = None,
        run_j_per_unit: float | None = None,
        amortization: int | None = None,
        lambda_: float | None = None,
        mu: float | None = None,
    ):
        if budget not in BUDGETS:
            raise ValueError(f"unknown budget {budget!r}; expected one of {list(BUDGETS)}")
        self.budget = budget
        self.lambda_ = lambda_ if lambda_ is not None else BUDGETS[budget][0]
        self.mu = mu if mu is not None else BUDGETS[budget][1]

        self.profiles = profiles if profiles is not None else load_profiles()
        meta = self.profiles.get("meta", {}) if isinstance(self.profiles, dict) else {}
        self.power_w = power_w if power_w is not None else float(
            meta.get("power_w", DEFAULT_POWER_W))
        self.run_j_per_unit = run_j_per_unit if run_j_per_unit is not None else float(
            meta.get("run_j_per_unit", DEFAULT_RUN_J_PER_UNIT))
        self.amortization = amortization if amortization is not None else int(
            meta.get("amortization", DEFAULT_AMORTIZATION))

    # -- one pass ------------------------------------------------------------
    def _costs(self, ir_text: str, name: str) -> tuple[float, float]:
        """(T̂ seconds, Ê joules) for pass `name`, profile-preferred."""
        prof = (self.profiles.get("passes", {}) or {}).get(name, {})
        if "t_seconds" in prof:
            t = float(prof["t_seconds"])
        else:
            t, _ = measure_pass_cost(ir_text, name)
        if "energy_j" in prof:
            e = float(prof["energy_j"])
        else:
            e = t * self.power_w
        return t, e

    def evaluate(self, ir_text: str, passes=None) -> list[PassDecision]:
        """Gate `passes` (default: all atomic passes) sequentially.

        Passes that are scheduled mutate the working module, so later
        decisions see the already-optimized IR (no double counting of the
        same benefit).
        """
        passes = list(passes) if passes is not None else list(AVAILABLE_PASSES)
        current = ir_text
        decisions: list[PassDecision] = []
        c_before_all = static_cost(current)

        for name in passes:
            c_before = static_cost(current)
            t_hat, e_hat = self._costs(current, name)
            threshold = self.lambda_ * e_hat + self.mu * t_hat

            try:
                after_text, _ = apply_pass(current, name)
                c_after = static_cost(after_text)
            except (RuntimeError, ValueError) as exc:
                decisions.append(PassDecision(
                    name=name, run=False, benefit=0.0, energy_cost=e_hat,
                    time_cost=t_hat, threshold=threshold, static_delta=0.0,
                    reason=f"skip: pass failed ({exc})"))
                continue

            delta = c_before - c_after
            rel = delta / c_before if c_before > 0 else 0.0
            benefit = self.amortization * (c_before * self.run_j_per_unit) * rel

            if delta <= 0:
                decisions.append(PassDecision(
                    name=name, run=False, benefit=benefit, energy_cost=e_hat,
                    time_cost=t_hat, threshold=threshold, static_delta=delta,
                    reason=f"skip: static cost does not improve (ΔC={delta:+.1f})"))
            elif benefit > threshold:
                decisions.append(PassDecision(
                    name=name, run=True, benefit=benefit, energy_cost=e_hat,
                    time_cost=t_hat, threshold=threshold, static_delta=delta,
                    reason=(f"run: B̂={benefit:.3e} J > λÊ+µT̂={threshold:.3e} J "
                            f"(ΔC={delta:+.1f}, {rel * 100:.1f}% cheaper to run)")))
                current = after_text
            else:
                decisions.append(PassDecision(
                    name=name, run=False, benefit=benefit, energy_cost=e_hat,
                    time_cost=t_hat, threshold=threshold, static_delta=delta,
                    reason=(f"skip: B̂={benefit:.3e} J ≤ λÊ+µT̂={threshold:.3e} J "
                            f"(ΔC={delta:+.1f}, budget too tight)")))

        self.last_decisions = decisions
        self.last_ir = current
        self.last_baseline_cost = c_before_all
        return decisions

    # -- convenience ---------------------------------------------------------
    def scheduled_passes(self, ir_text: str, passes=None) -> tuple[list[str], list[PassDecision]]:
        decisions = self.evaluate(ir_text, passes)
        return [d.name for d in decisions if d.run], decisions


def gate_passes(ir_text: str, budget: str = "balanced", passes=None):
    """Module-level convenience: returns (scheduled_names, decisions)."""
    return CostBenefitGate(budget=budget).scheduled_passes(ir_text, passes)


def format_log(decisions: list[PassDecision]) -> list[str]:
    """Render decisions as the auditable gating log lines."""
    lines = []
    for d in decisions:
        tag = "RUN " if d.run else "SKIP"
        lines.append(f"[{tag}] {d.name}: {d.reason}")
    return lines

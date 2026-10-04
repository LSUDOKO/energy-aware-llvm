"""Phase 3 tests: Stage-2 extractor (40+ metrics) and cost-benefit gating."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from stage2_extractor import IRFeatureExtractor, static_cost, count_instructions
from stage2.pass_gating import (
    BUDGETS, CostBenefitGate, format_log, gate_passes, load_profiles,
)

# Hand-written module with an outer loop containing an inner loop:
#   entry -> h1; h1 -> b|ex; b -> h2; h2 -> c|d; c -> h2 (inner back edge);
#   d -> h1 (outer back edge); ex -> ret
NESTED_LOOP_IR = """
define i32 @main() {
entry:
  br label %h1
h1:
  %c1 = icmp slt i32 0, 1
  br i1 %c1, label %b, label %ex
b:
  br label %h2
h2:
  %c2 = icmp slt i32 0, 2
  br i1 %c2, label %c, label %d
c:
  br label %h2
d:
  br label %h1
ex:
  ret i32 0
}
"""

CALL_IR = """
define i32 @helper(i32 %x) {
entry:
  %s = add i32 %x, 1
  ret i32 %s
}

define i32 @main() {
entry:
  %v = call i32 @helper(i32 41)
  %f = sitofp i32 %v to float
  %g = fadd float %f, 1.000000e+00
  %r = fptosi float %g to i32
  ret i32 %r
}
"""


def build_alloca_ir() -> str:
    """A small alloca-heavy function that -sroa genuinely optimizes."""
    from llvmlite import ir
    m = ir.Module(name="mem")
    i32 = ir.IntType(32)
    fn = ir.Function(m, ir.FunctionType(i32, []), name="main")
    b = ir.IRBuilder(fn.append_basic_block("entry"))
    p = b.alloca(i32)
    b.store(ir.Constant(i32, 7), p)
    v = b.load(p)
    r = b.add(v, ir.Constant(i32, 0))
    b.ret(r)
    return str(m)


def extract(src_ir: str) -> dict:
    return IRFeatureExtractor(src_ir).extract_features()


class TestExtractorMetrics:
    """Plan Stage 2: a real typed walk over LLVM IR, 35+ metrics."""

    def test_returns_35_plus_metrics(self):
        f = extract(NESTED_LOOP_IR)
        assert len(f) >= 35, f"only {len(f)} metrics"

    def test_legacy_aliases_present(self):
        f = extract(NESTED_LOOP_IR)
        for key in ("total_instructions", "num_functions", "num_basic_blocks",
                    "num_loads", "num_stores", "num_branches", "num_calls",
                    "num_alu_ops", "num_fp_ops", "num_allocas", "num_icmp",
                    "num_fcmp", "cyclomatic_complexity_estimate"):
            assert key in f, f"missing legacy metric {key}"

    def test_counts_on_nested_loop_cfg(self):
        f = extract(NESTED_LOOP_IR)
        assert f["num_functions"] == 1
        assert f["num_basic_blocks"] == 7
        assert f["num_cond_branches"] == 2
        assert f["num_uncond_branches"] == 4
        assert f["num_ret"] == 1
        assert f["num_icmp"] == 2
        # dominator-based loop analysis
        assert f["num_back_edges"] == 2
        assert f["num_natural_loops"] == 2
        assert f["max_loop_depth"] == 2, "inner loop must nest inside outer"
        # E - N + 2P = 8 - 7 + 2 = 3
        assert f["cyclomatic_complexity"] == 3

    def test_call_and_float_metrics(self):
        f = extract(CALL_IR)
        assert f["num_functions"] == 2
        assert f["num_calls"] == 1
        assert f["num_fp_arith"] == 1
        assert f["uses_float"] == 1
        assert f["num_casts"] == 2  # sitofp + fptosi
        assert f["num_arguments"] == 1

    def test_accepts_ir_module_object(self):
        from llvmlite import ir
        m = ir.Module(name="m")
        fnty = ir.FunctionType(ir.IntType(32), [])
        fn = ir.Function(m, fnty, name="main")
        b = ir.IRBuilder(fn.append_basic_block("entry"))
        b.ret(ir.Constant(ir.IntType(32), 0))
        f = IRFeatureExtractor(m).extract_features()
        assert f["num_functions"] == 1
        assert f["total_instructions"] == 1

    def test_static_cost_positive_and_count_matches(self):
        assert static_cost(NESTED_LOOP_IR) > 0
        assert count_instructions(NESTED_LOOP_IR) == 9

    def test_every_metric_is_numeric(self):
        f = extract(CALL_IR)
        for k, v in f.items():
            assert isinstance(v, (int, float)), f"{k} is {type(v)}"


def _gate(**kw) -> CostBenefitGate:
    """Gate with injected deterministic per-pass profiles (no live timing)."""
    from backend.passes import AVAILABLE_PASSES
    profiles = {
        "meta": {"power_w": 10.0, "run_j_per_unit": 1e-6,
                 "amortization": 1_000_000},
        "passes": {p: {"t_seconds": 1e-4, "energy_j": 1e-3}
                   for p in AVAILABLE_PASSES},
    }
    return CostBenefitGate(profiles=profiles, **kw)


class TestCostBenefitGate:
    """Plan Stage 2 rule: run p iff B̂ > λ·Ê + µ·T̂."""

    def test_threshold_formula_exact(self):
        gate = _gate(lambda_=2.0, mu=3.0)
        decisions = gate.evaluate(NESTED_LOOP_IR, passes=["-sroa"])
        d = decisions[0]
        # λ·Ê + µ·T̂ = 2·1e-3 + 3·1e-4 = 2.3e-3
        assert d.threshold == pytest.approx(2.0 * 1e-3 + 3.0 * 1e-4)

    def test_run_when_benefit_exceeds_threshold(self):
        gate = _gate(lambda_=0.0, mu=0.0)  # threshold 0 → run iff ΔC>0
        decisions = gate.evaluate(build_alloca_ir(), passes=["-sroa"])
        d = decisions[0]
        assert d.run is True
        assert d.static_delta > 0
        assert d.benefit > 0
        assert d.reason.startswith("run:")

    def test_skip_when_budget_too_tight(self):
        gate = _gate(lambda_=1e9, mu=1e9)
        decisions = gate.evaluate(build_alloca_ir(), passes=["-sroa"])
        d = decisions[0]
        assert d.run is False
        assert "budget too tight" in d.reason

    def test_skip_when_static_cost_does_not_improve(self):
        # -dse on this load-free module changes nothing
        gate = _gate(lambda_=0.0, mu=0.0)
        decisions = gate.evaluate(NESTED_LOOP_IR, passes=["-dse"])
        d = decisions[0]
        assert d.run is False
        assert "does not improve" in d.reason
        assert d.static_delta == 0.0

    def test_budget_ordering_eco_stricter_than_perf(self):
        eco = _gate(budget="eco")
        perf = _gate(budget="perf")
        assert BUDGETS["eco"][0] > BUDGETS["balanced"][0] > BUDGETS["perf"][0]
        n_eco, _ = eco.scheduled_passes(NESTED_LOOP_IR)
        n_perf, _ = perf.scheduled_passes(NESTED_LOOP_IR)
        assert len(n_eco) <= len(n_perf)
        assert len(n_perf) > 0, "perf mode must schedule helpful passes"

    def test_sequential_gating_reuses_earlier_scheduling(self):
        gate = _gate(budget="perf")
        sched, decisions = gate.scheduled_passes(NESTED_LOOP_IR)
        assert sched
        # the gated result equals applying exactly the scheduled passes
        from backend.passes import apply_pass_sequence
        result = apply_pass_sequence(NESTED_LOOP_IR, sched)
        assert result.verified
        assert static_cost(result.ir_text) == pytest.approx(
            static_cost(gate.last_ir))

    def test_gate_passes_convenience(self):
        names, decisions = gate_passes(
            NESTED_LOOP_IR, budget="balanced")
        assert names == [d.name for d in decisions if d.run]

    def test_format_log_has_one_line_per_decision(self):
        decisions = CostBenefitGate("eco").evaluate(
            NESTED_LOOP_IR, passes=["-sroa", "-gvn"])
        lines = format_log(decisions)
        assert len(lines) == 2
        assert all(("[RUN" in l) or ("[SKIP" in l) for l in lines)

    def test_unknown_budget_raises(self):
        with pytest.raises(ValueError):
            CostBenefitGate("turbo")

    def test_missing_profile_file_returns_empty_dict(self, tmp_path):
        assert load_profiles(tmp_path / "nope.json") == {}

    def test_reasons_are_auditable(self):
        decisions = CostBenefitGate("balanced").evaluate(NESTED_LOOP_IR)
        assert decisions, "gate must consider the full pass list"
        for d in decisions:
            assert d.reason
            assert ("B̂" in d.reason) or ("does not improve" in d.reason) \
                or ("failed" in d.reason)

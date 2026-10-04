"""End-to-end compilation pipeline (Stages 1-3 + real backend).

Single implementation shared by ``compiler_driver.py`` (CLI) and ``api.py``
(Flask), so there is one source of truth and no duplicated mock logic:

  Stage 1  fused single-pass front-end (UnifiedSemanticVisitor) — timing,
           online simplification stats, diagnostics with source locations
  Stage 2  52-metric IR feature extraction (llvmlite binding walk)
  Stage 3  mode selection, all through the real cost-benefit gate:
             -Meco      gate(budget=eco)     — strict B̂ > λÊ+µT̂
             -Mbalanced ML pass ranker (XGBoost, measured data) -> gate
             -Mperf     genetic algorithm searching 1/EDP        -> gate
           then the scheduled sequence is executed by the LLVM new pass
           manager (backend/passes.py), verified, JIT-compiled and
           emitted as a real relocatable object file.

Every number reported is measured (wall times, native runtime, object
size) or a clearly-labelled software estimate (compile energy when RAPL
is not readable).
"""
from __future__ import annotations

import time
from pathlib import Path

from backend.passes import (AVAILABLE_PASSES, apply_pass_sequence,
                           emit_object_file)
from energy.experiments import merged_pipeline, conventional_pipeline
from energy.execution import JitProgram, jit_run
from energy.lifecycle import Cost, break_even_runs, edp_savings_pct, summarize_lifecycle
from energy.meter import EnergyMeter
from stage2.extractor import IRFeatureExtractor, count_instructions
from stage2.pass_gating import (
    DEFAULT_POWER_W, CostBenefitGate, format_log, load_profiles,
)

MODES = ("-Meco", "-Mbalanced", "-Mperf")
BUILD_DIR = Path(__file__).resolve().parent / "build"
DEFAULT_RUNS = 10_000   # executions the compiled program is assumed to get

_METER: EnergyMeter | None = None


def default_meter() -> EnergyMeter:
    """Process-wide meter (RAPL discovery and idle power are measured once)."""
    global _METER
    if _METER is None:
        _METER = EnergyMeter()
    return _METER


def _stage_joules(meter: EnergyMeter, fn, seconds: float, power_w: float
                  ) -> tuple[float, str]:
    """Joules of an idempotent stage: batched RAPL reading when the counter
    is readable, else the labelled estimate ``P-hat x T``."""
    if meter.available:
        m = meter.measure(fn)
        if m.energy_j is not None:
            return m.energy_j, "rapl"
    return power_w * seconds, "estimated"


def _log(logs: list, msg: str) -> None:
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")


def _measure_runtime(ir_text: str, repeats: int = 7) -> tuple[float, int]:
    """Median native execution time [s] of main() + its return value.

    Uses compile-once/measure-many so JIT setup is not counted (that
    would corrupt T_run and every EDP derived from it).
    """
    from energy.execution import measure_runtime
    return measure_runtime(ir_text, samples=repeats)


def compile_source(
    source: str,
    mode: str = "-Mbalanced",
    emit_object: bool = True,
    name: str = "a",
    ga_kwargs: dict | None = None,
) -> dict:
    """Compile MiniC source under `mode`; returns a result dict.

    The response keeps the original API schema (ast_time_ms, ml_time_ms,
    predicted_edp, features, selected_passes, logs, llvm_ir) and adds the
    measured Stage-3 evidence (gating log, optimized IR, runtime, object
    file, EDP breakdown).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

    logs: list[str] = []
    result: dict = {"success": True, "mode": mode, "logs": logs}

    profiles = load_profiles()
    power_w = float(profiles.get("meta", {}).get("power_w", DEFAULT_POWER_W))
    result["power_w"] = power_w
    result["power_source"] = profiles.get("meta", {}).get(
        "power_source", "default_tdp_placeholder (RAPL not readable)")

    # ---------------- Stage 1: fused front-end ----------------
    _log(logs, "STAGE 1: Fused Single-Pass Front-End (Minimizing E_compile)")
    t0 = time.perf_counter()
    artifacts = merged_pipeline(source)
    t_frontend = time.perf_counter() - t0
    result["ast_time_ms"] = t_frontend * 1000.0

    if artifacts.errors:
        result["success"] = False
        result["error"] = "; ".join(artifacts.errors)
        _log(logs, f"diagnostics: {result['error']}")
        return result

    ir_text = artifacts.ir_text
    result["llvm_ir"] = ir_text
    result["frontend_stats"] = artifacts.stats
    logs.append(f"-> IR emitted in {result['ast_time_ms']:.2f} ms; "
                f"online simplifications: {artifacts.stats}")

    # ---------------- Stage 2: metrics + gating inputs ----------------
    _log(logs, "STAGE 2: Static IR Feature Extraction & Pass Gating")
    t0 = time.perf_counter()
    features = IRFeatureExtractor(ir_text).extract_features()
    t_stage2 = time.perf_counter() - t0
    result["features"] = features
    logs.append(f"-> Extracted {len(features)} IR metrics "
                f"(baseline static cost {features['static_cost']:.0f}, "
                f"{features['total_instructions']} instructions)")

    # ---------------- Stage 3: mode scheduling ----------------
    _log(logs, f"STAGE 3: Multi-Mode Pass Scheduling & Backend ({mode})")
    candidate: list[str] = []
    ml_time = 0.0
    pred_benefit = 0.0
    gate_budget = {"-Meco": "eco", "-Mbalanced": "balanced",
                   "-Mperf": "perf"}[mode]

    if mode == "-Meco":
        logs.append("-> Mode: -Meco (Minimal Compile Overhead)")
        logs.append("-> Gate evaluates every optional pass under the strict "
                    "eco budget; only passes that repay their compile cost run.")
        candidate = list(AVAILABLE_PASSES)

    elif mode == "-Mbalanced":
        logs.append("-> Mode: -Mbalanced (ML Pass Ranker on measured data)")
        from stage3_ml_model import get_model, rank_passes
        t0 = time.perf_counter()
        model = get_model()
        candidate, pred_benefit = rank_passes(features, model)
        ml_time = time.perf_counter() - t0
        source_tag = getattr(model, "training_source", "?")
        logs.append(f"-> ML Pass Ranker completed in {ml_time * 1000:.2f} ms "
                    f"(model trained on: {source_tag})")
        logs.append(f"-> Predicted EDP benefit: {pred_benefit:.4f}")
        logs.append(f"-> Candidate sequence: {candidate or '(none)'}")

    else:  # -Mperf
        logs.append("-> Mode: -Mperf (Genetic Algorithm Search Loop, 1/EDP)")
        from ml_models.ga import run_ga_search
        t0 = time.perf_counter()
        ga = run_ga_search(ir_text, **(ga_kwargs or {}))
        ml_time = time.perf_counter() - t0
        candidate = ga.sequence
        pred_benefit = ga.fitness
        result["ga"] = ga.to_dict()
        logs.append(f"-> GA: {ga.generations} generations, "
                    f"{ga.evaluations} evaluated sequences, "
                    f"best fitness {ga.fitness:.3e} in {ga.elapsed_s * 1000:.0f} ms")
        logs.append(f"-> GA selected: {candidate or '(none)'}")
        m = ga.measured
        if m.get("jit_ok") is not None:
            logs.append(f"-> Winner re-measured: verified={m.get('verified')}, "
                        f"compile={m.get('compile_time_s', 0) * 1e3:.2f} ms, "
                        f"runtime={m.get('runtime_s_median', 0) * 1e6:.0f} us")

    # ---- energy-aware gate decides, pass for pass (auditable) ----
    gate = CostBenefitGate(budget=gate_budget, profiles=profiles)
    scheduled, decisions = gate.scheduled_passes(ir_text, candidate)
    result["gating"] = {
        "budget": gate_budget,
        "lambda": gate.lambda_,
        "mu": gate.mu,
        "candidate": list(candidate),
        "scheduled": list(scheduled),
        "decisions": [d.to_dict() for d in decisions],
        "log": format_log(decisions),
    }
    logs.extend(f"   {line}" for line in result["gating"]["log"])
    logs.append(f"-> Final gated pass sequence: {', '.join(scheduled) or '(none)'}")

    result["ml_time_ms"] = ml_time * 1000.0
    result["predicted_edp"] = pred_benefit
    result["selected_passes"] = scheduled

    # ---------------- backend: execute the sequence ----------------
    t0 = time.perf_counter()
    if scheduled:
        run = apply_pass_sequence(ir_text, scheduled)
        opt_ir = run.ir_text
        passes_ms = run.total_time * 1000.0
        verified = run.verified
        instr_before, instr_after = run.instructions_before, run.instructions_after
    else:
        opt_ir = ir_text
        passes_ms = 0.0
        verified = True
        instr_before = instr_after = count_instructions(ir_text)
    t_backend = time.perf_counter() - t0

    result["optimized_ir"] = opt_ir
    result["passes_time_ms"] = passes_ms
    result["verified"] = verified
    result["instructions"] = {"before": instr_before, "after": instr_after}
    logs.append(f"-> LLVM new-PM sequence executed in {passes_ms:.2f} ms "
                f"(verified={verified}); instructions {instr_before} -> {instr_after}")

    # ---- correctness: optimized module must behave like the original ----
    try:
        base_value = jit_run(ir_text)
        opt_value = jit_run(opt_ir)
        differential_ok = (base_value == opt_value)
        result["return_value"] = opt_value
    except RuntimeError as exc:
        differential_ok = False
        result["return_value"] = None
        logs.append(f"-> JIT error: {exc}")
    result["differential_ok"] = differential_ok

    # ---- measured native runtime of the optimized program ----
    try:
        runtime_s, rt_value = _measure_runtime(opt_ir)
        result["runtime_us"] = runtime_s * 1e6
        result["return_value"] = rt_value
    except RuntimeError:
        runtime_s = None
        result["runtime_us"] = None

    # ---- real object file ----
    obj_path = None
    if emit_object:
        try:
            BUILD_DIR.mkdir(exist_ok=True)
            obj_path = emit_object_file(opt_ir, BUILD_DIR / f"{name}.o")
            result["object_file"] = str(obj_path)
            result["object_bytes"] = Path(obj_path).stat().st_size
            logs.append(f"-> Object file: {obj_path} "
                        f"({result['object_bytes']} bytes, host triple)")
        except Exception as exc:  # pragma: no cover - defensive
            logs.append(f"-> Object emission failed: {exc}")

    # ---------------- EDP breakdown (plan: report E_compile & E_run) --------
    total_compile_s = (t_frontend + t_stage2 + ml_time
                       + (passes_ms / 1000.0))
    e_compile_est = power_w * total_compile_s
    runtime_for_edp = runtime_s if runtime_s else 0.0
    edp = e_compile_est * runtime_for_edp

    # baseline: same front-end, no optional passes
    base_compile_s = t_frontend + t_stage2
    e_base = power_w * base_compile_s
    edp_baseline = e_base * (runtime_for_edp or 1.0)
    savings = ((edp_baseline - edp) / edp_baseline * 100.0
               if edp_baseline > 0 else 0.0)

    result["energy"] = {
        "source": result["power_source"],
        "e_compile_j": e_compile_est,
        "e_compile_note": ("P-hat x T (software estimate; run "
                           "energy/setup_rapl_access.sh for RAPL joules)"
                           if profiles.get("meta", {}).get("power_source")
                           != "rapl" else "RAPL-measured package power"),
        "compile_time_s": total_compile_s,
        "e_run_j": (power_w * 0.2 * runtime_for_edp) if runtime_for_edp else None,
        "e_run_note": "runtime energy proxy (fraction of package power x runtime)",
        "edp": edp,
        "edp_baseline": edp_baseline,
        "edp_savings_pct": savings,
    }
    result["differential_note"] = (
        f"optimized main() returned {result['return_value']}, "
        f"matches unoptimized: {differential_ok}")

    _log(logs, f"EDP: E_compile≈{e_compile_est * 1e3:.3f} mJ, "
               f"T_run={runtime_for_edp * 1e6 if runtime_for_edp else 0:.0f} us, "
               f"EDP={edp:.3e} ({savings:+.1f}% vs no-pass baseline)")
    _log(logs, "COMPILATION SUCCESSFUL" if (verified and differential_ok)
         else "COMPILATION FAILED VERIFICATION")
    result["success"] = bool(verified and differential_ok)
    return result


def conventional_vs_merged_ir(source: str) -> dict:
    """Both pipelines' IR + instruction counts (differential evidence)."""
    conv = conventional_pipeline(source)
    merged = merged_pipeline(source)
    return {
        "conventional_ir": conv.ir_text,
        "merged_ir": merged.ir_text,
        "conventional_instructions": count_instructions(conv.ir_text),
        "merged_instructions": count_instructions(merged.ir_text),
        "checks_ok": conv.checks_ok and merged.checks_ok,
    }

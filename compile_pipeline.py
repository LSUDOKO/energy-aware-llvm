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
from energy.execution import JitProgram, jit_run, object_bytes
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
    n_runs: int = DEFAULT_RUNS,
    meter: EnergyMeter | None = None,
) -> dict:
    """Compile MiniC source under `mode`; returns a result dict.

    The response keeps the original API schema (ast_time_ms, ml_time_ms,
    predicted_edp, features, selected_passes, logs, llvm_ir) and adds the
    measured Stage-3 evidence (gating log, optimized IR, runtime, object
    file, EDP breakdown).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

    meter = meter or default_meter()
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
    e_frontend, src_frontend = _stage_joules(
        meter, lambda: merged_pipeline(source), t_frontend, power_w)
    e_stage2, src_stage2 = _stage_joules(
        meter, lambda: IRFeatureExtractor(ir_text).extract_features(),
        t_stage2, power_w)
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
        e_search, src_search = 0.0, "none"

    elif mode == "-Mbalanced":
        logs.append("-> Mode: -Mbalanced (ML Pass Ranker on measured data)")
        from stage3_ml_model import get_model, rank_passes
        t0 = time.perf_counter()
        model = get_model()
        candidate, pred_benefit = rank_passes(features, model)
        ml_time = time.perf_counter() - t0
        e_search, src_search = _stage_joules(
            meter, lambda: rank_passes(features, model), ml_time, power_w)
        source_tag = getattr(model, "training_source", "?")
        logs.append(f"-> ML Pass Ranker completed in {ml_time * 1000:.2f} ms "
                    f"(model trained on: {source_tag})")
        logs.append(f"-> Predicted EDP benefit: {pred_benefit:.4f}")
        logs.append(f"-> Candidate sequence: {candidate or '(none)'}")

    else:  # -Mperf
        logs.append("-> Mode: -Mperf (Genetic Algorithm Search Loop, 1/EDP)")
        from ml_models.ga import run_ga_search
        holder: dict = {}

        def _search() -> None:
            holder["ga"] = run_ga_search(
                ir_text, **{"n_runs": n_runs, **(ga_kwargs or {})})

        t0 = time.perf_counter()
        # the search is stochastic and long: meter the one real execution
        # instead of repeating it
        if meter.available:
            m = meter.measure(_search, calls=1)
            e_search = m.energy_j
            src_search = "rapl"
        else:
            _search()
            e_search, src_search = None, "estimated"
        ml_time = time.perf_counter() - t0
        if e_search is None:
            e_search = power_w * ml_time
        ga = holder["ga"]
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
    t0 = time.perf_counter()
    gate = CostBenefitGate(budget=gate_budget, profiles=profiles)
    scheduled, decisions = gate.scheduled_passes(ir_text, candidate)
    t_gate = time.perf_counter() - t0
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
    if scheduled:
        e_passes, src_passes = _stage_joules(
            meter, lambda: apply_pass_sequence(ir_text, scheduled),
            passes_ms / 1000.0, power_w)
    else:
        e_passes, src_passes = 0.0, "none"

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

    # ---- measured native runtime (E_run inputs), baseline and optimized ----
    runtime_s = base_runtime_s = None
    e_run_opt = e_run_base = None
    run_source = "estimated"
    try:
        runtime_s, rt_value = _measure_runtime(opt_ir)
        result["runtime_us"] = runtime_s * 1e6
        result["return_value"] = rt_value
        if scheduled:
            base_runtime_s, _ = _measure_runtime(ir_text)
        else:
            base_runtime_s = runtime_s
        result["baseline_runtime_us"] = base_runtime_s * 1e6

        def _run_joules(ir: str, seconds: float) -> float:
            nonlocal run_source
            if meter.available:
                prog = JitProgram(ir)
                m = meter.measure(prog.call)
                if m.energy_j is not None:
                    run_source = "rapl"
                    return m.energy_j
            return power_w * seconds

        e_run_opt = _run_joules(opt_ir, runtime_s)
        e_run_base = (_run_joules(ir_text, base_runtime_s) if scheduled
                      else e_run_opt)
    except RuntimeError:
        result["runtime_us"] = None
        result["baseline_runtime_us"] = None

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

    # ---- backend (instruction selection + emission), metered separately ----
    # Optimizing the IR changes how much work code generation has to do, so
    # both builds are charged for their own machine-code generation.
    def _codegen_cost(ir: str) -> tuple[float, float]:
        t0 = time.perf_counter()
        object_bytes(ir)
        seconds = time.perf_counter() - t0
        joules, _src = _stage_joules(meter, lambda: object_bytes(ir),
                                     seconds, power_w)
        return seconds, joules

    t_cg_opt, e_cg_opt = _codegen_cost(opt_ir)
    t_cg_base, e_cg_base = ((t_cg_opt, e_cg_opt) if opt_ir == ir_text
                            else _codegen_cost(ir_text))

    # ---------------- lifecycle EDP (plan: report E_compile and E_run) ------
    # Baseline = front-end + Stage 2 + codegen of the unoptimized IR.
    # Optimized = front-end + Stage 2 + the mode's search + gate + scheduled
    #             passes + codegen of the optimized IR.
    t_base_compile = t_frontend + t_stage2 + t_cg_base
    t_opt_compile = (t_frontend + t_stage2 + ml_time + t_gate
                     + passes_ms / 1000.0 + t_cg_opt)
    e_base_compile = e_frontend + e_stage2 + e_cg_base
    e_opt_compile = e_frontend + e_stage2 + e_search + e_passes + e_cg_opt

    energy: dict = {
        "source": ("rapl" if "rapl" in (src_frontend, src_stage2, src_search,
                                         src_passes, run_source)
                   else result["power_source"]),
        "measured": meter.available,
        "n_runs": n_runs,
        "e_compile_j": e_opt_compile,
        "e_compile_baseline_j": e_base_compile,
        "e_compile_note": ("RAPL package joules, idle-corrected"
                           if meter.available else
                           "P-hat x T (software estimate; run "
                           "energy/setup_rapl_access.sh for RAPL joules)"),
        "compile_time_s": t_opt_compile,
        "compile_time_baseline_s": t_base_compile,
        "stage_j": {"frontend": e_frontend, "stage2": e_stage2,
                    "search": e_search, "passes": e_passes,
                    "codegen": e_cg_opt},
        "stage_s": {"frontend": t_frontend, "stage2": t_stage2,
                    "search": ml_time, "gate": t_gate,
                    "passes": passes_ms / 1000.0, "codegen": t_cg_opt},
    }
    if runtime_s and base_runtime_s:
        base = Cost(e_base_compile, t_base_compile, e_run_base, base_runtime_s)
        opt = Cost(e_opt_compile, t_opt_compile, e_run_opt, runtime_s)
        life = summarize_lifecycle(base, opt)
        energy.update({
            "e_run_j": e_run_opt,
            "e_run_baseline_j": e_run_base,
            "e_run_note": ("RAPL package joules per native run"
                           if run_source == "rapl" else
                           "P-hat x T_run (software estimate)"),
            "edp": opt.edp(n_runs),
            "edp_baseline": base.edp(n_runs),
            "edp_savings_pct": edp_savings_pct(base, opt, n_runs),
            "break_even_runs": life["break_even_runs"],
            "lifecycle": life["by_runs"],
        })
    else:
        energy.update({"e_run_j": None, "edp": None, "edp_baseline": None,
                       "edp_savings_pct": 0.0, "break_even_runs": None,
                       "lifecycle": []})
    result["energy"] = energy
    result["differential_note"] = (
        f"optimized main() returned {result['return_value']}, "
        f"matches unoptimized: {differential_ok}")

    be = energy["break_even_runs"]
    be_txt = ("n/a" if be is None else "0 (never costlier)" if be == 0
              else f"{be:,.0f} runs")
    _log(logs, f"EDP @ {n_runs:,} runs: E_compile={e_opt_compile * 1e3:.3f} mJ "
               f"({energy['e_compile_note'].split(',')[0]}), "
               f"T_run={(runtime_s or 0) * 1e6:.1f} us, "
               f"savings {energy['edp_savings_pct']:+.1f}% vs no-pass baseline; "
               f"break-even {be_txt}")
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

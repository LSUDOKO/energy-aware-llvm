"""Stage 3 — ML pass ranker (-Mbalanced mode).

Training data now comes from **real measurements** produced by
``ml_models/collect_dataset.py`` (``datasets/measurements.csv``): for each
benchmark x candidate pass sequence it records real Stage-2 features, real
pass-scheduler wall time, real JIT-measured program runtime, and RAPL
compile energy when available (otherwise the documented P-hat x T-hat
software estimate).  The label is *lifecycle* EDP savings relative to the
no-optional-pass baseline of the same benchmark: the program is compiled
once and executed ``BALANCED_RUNS`` times (``energy/lifecycle.py``), so a
pass sequence is only worth running when its compile cost is repaid by
cheaper executions.  Labels are recomputed from the measured compile and
run times (``relabel_savings``), so one dataset can serve any run count.

If no measured dataset exists yet, ``train_model()`` falls back to a
clearly-marked *model-labelled* set built by actually running every
candidate sequence on the bundled demo sources and labelling with the
static cost model + measured timings (``training_source`` on the model says
which path was used — never silently invents random labels as the original
skeleton did).

``rank_passes`` scores a fixed, diverse candidate pool with the trained
XGBoost regressor and returns the sequence with the highest predicted EDP
savings; it is a <50 ms inference as required by the plan.
"""
from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from backend.passes import AVAILABLE_PASSES, expand_sequence
from energy.lifecycle import Cost, edp_savings_pct
from stage2.pass_gating import DEFAULT_POWER_W

MODEL_PATH = Path(__file__).resolve().parent / "xgboost_pass_model.pkl"
DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "measurements.csv"

# Stage-2 feature keys used as model inputs (all numeric, extractor output)
FEATURE_KEYS = [
    "num_functions", "num_function_declarations", "num_global_variables",
    "num_arguments", "num_basic_blocks", "total_instructions", "num_loads",
    "num_stores", "num_alloca", "num_gep", "num_calls", "num_ret",
    "num_branches", "num_cond_branches", "num_uncond_branches", "num_switch",
    "num_icmp", "num_fcmp", "num_phi", "num_select", "num_int_arith",
    "num_fp_arith", "num_bitwise", "num_casts", "num_const_operands",
    "num_const_int_operands", "num_const_float_operands", "num_terminators",
    "num_entry_blocks", "num_exit_blocks", "num_back_edges",
    "num_natural_loops", "max_loop_depth", "num_critical_edges",
    "max_block_instructions", "avg_block_instructions",
    "max_function_instructions", "avg_instructions_per_function",
    "avg_predecessors", "max_successors", "cyclomatic_complexity", "mem_ops",
    "mem_ratio", "fp_ratio", "branch_ratio", "estimated_ir_bytes",
    "static_cost", "uses_float",
]

# Candidate pool ranked by -Mbalanced (diverse preset + hand-built sequences)
CANDIDATE_SEQUENCES: list[list[str]] = [
    [],
    ["-O1"],
    ["-O2"],
    ["-O3"],
    ["-sroa", "-simplifycfg"],
    ["-sroa", "-instcombine", "-simplifycfg", "-gvn"],
    ["-sroa", "-sccp", "-simplifycfg", "-gvn", "-dse", "-adce"],
    ["-loop-rotate", "-loop-unroll", "-lcsr"],
    list(AVAILABLE_PASSES),
]

TARGET_COL = "edp_savings"
BALANCED_RUNS = 10_000   # executions assumed by the -Mbalanced objective


def sequence_key(seq) -> str:
    """Canonical CSV representation of a pass sequence."""
    return "|".join(expand_sequence(seq)) if seq else "(none)"


def row_from(features: dict, sequence) -> dict:
    """Model feature row: Stage-2 metrics + one-hot pass flags."""
    row = {k: float(features.get(k, 0)) for k in FEATURE_KEYS}
    expanded = set(expand_sequence(sequence)) if sequence else set()
    for p in AVAILABLE_PASSES:
        row[f"pass{p}"] = 1 if p in expanded else 0
    return row


def relabel_savings(frame: pd.DataFrame, n_runs: int = BALANCED_RUNS,
                    power_w: float = DEFAULT_POWER_W) -> pd.DataFrame:
    """Recompute ``TARGET_COL`` as lifecycle-EDP savings (fraction, not %).

    Per benchmark, every row is compared with that benchmark's ``(none)``
    row:  ``Cost(P x (T_shared + T_passes), ., P x T_run, T_run)`` with
    ``T_shared`` the front-end + code-generation time paid by every build.
    Rows without a measured runtime are dropped; frames lacking the timing
    columns (e.g. hand-built test frames) are returned unchanged.
    """
    needed = {"benchmark", "sequence", "t_compile_s", "runtime_s_median"}
    if not needed <= set(frame.columns):
        return frame
    out = frame.copy()
    out["runtime_s_median"] = pd.to_numeric(out["runtime_s_median"], errors="coerce")
    out = out[out["runtime_s_median"] > 0].copy()
    shared = (pd.to_numeric(out["t_shared_s"], errors="coerce").fillna(0.0)
              if "t_shared_s" in out.columns else 0.0)
    out["_t_c"] = out["t_compile_s"].astype(float) + shared
    labels = {}
    for bench, grp in out.groupby("benchmark"):
        base_rows = grp[grp["sequence"] == "(none)"]
        if base_rows.empty:
            continue
        b = base_rows.iloc[0]
        base = Cost(power_w * b["_t_c"], b["_t_c"],
                    power_w * b["runtime_s_median"], b["runtime_s_median"])
        for idx, r in grp.iterrows():
            cost = Cost(power_w * r["_t_c"], r["_t_c"],
                        power_w * r["runtime_s_median"], r["runtime_s_median"])
            labels[idx] = edp_savings_pct(base, cost, n_runs) / 100.0
    out = out[out.index.isin(labels)].copy()
    out[TARGET_COL] = pd.Series(labels)
    return out.drop(columns=["_t_c"])


def load_dataset(path=None) -> pd.DataFrame | None:
    p = Path(path) if path else DATASET_PATH
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
    except Exception:
        return None
    return df if TARGET_COL in df.columns and len(df) >= 4 else None


def _training_frame() -> tuple[pd.DataFrame, str]:
    """(frame, source) — measured CSV if present, else model-labelled."""
    df = load_dataset()
    if df is not None:
        return df, "measured"
    return _generate_model_labels(), "model_labels"


def _demo_sources() -> list[tuple[str, str]]:
    """Small sources used only for the no-measurement fallback labelling."""
    root = Path(__file__).resolve().parent
    out = []
    for p in sorted(root.glob("benchmarks/*.c")):
        out.append((p.name, p.read_text()))
    if not out:
        out.append(("test_program.c", (root / "test_program.c").read_text()))
    return out


def _generate_model_labels() -> pd.DataFrame:
    """Fallback when no measured CSV exists: run every candidate sequence
    for real on the bundled demo sources (real pass wall time, real native
    runtime) and label with lifecycle-EDP savings (``relabel_savings``)."""
    import time

    from energy.execution import measure_runtime, object_bytes
    from stage2.extractor import IRFeatureExtractor, static_cost
    from stage2.pass_gating import load_profiles

    from backend.passes import apply_pass_sequence
    from energy.experiments import merged_pipeline

    profiles = load_profiles()
    power = float(profiles.get("meta", {}).get("power_w", DEFAULT_POWER_W))
    rows = []
    for name, src in _demo_sources():
        try:
            t0 = time.perf_counter()
            ir_text = str(merged_pipeline(src).ir_text)
            t_frontend = time.perf_counter() - t0
            features = IRFeatureExtractor(ir_text).extract_features()
            t0 = time.perf_counter()
            object_bytes(ir_text)
            t_shared = t_frontend + (time.perf_counter() - t0)
        except Exception:
            continue

        for seq in CANDIDATE_SEQUENCES:
            try:
                t0 = time.perf_counter()
                result = apply_pass_sequence(ir_text, seq)
                t_compile = time.perf_counter() - t0
            except Exception:
                continue
            if not result.verified:
                continue
            try:
                t_run, _value = measure_runtime(result.ir_text, samples=3)
            except RuntimeError:
                continue
            row = row_from(features, seq)
            row.update({
                "benchmark": name, "sequence": sequence_key(seq),
                "t_compile_s": t_compile, "t_shared_s": t_shared,
                "runtime_s_median": t_run,
                "static_cost_after": static_cost(result.ir_text),
                "energy_source": "estimated",
            })
            rows.append(row)
    frame = pd.DataFrame(rows)
    return relabel_savings(frame, power_w=power) if len(frame) else frame


def train_model(frame: pd.DataFrame | None = None,
                source: str = "provided") -> XGBRegressor:
    """Train XGBoost on measured (or honestly model-labelled) data.

    ``source`` tags the model with where ``frame`` came from ('measured' for
    ``datasets/measurements.csv``); it is ignored when ``frame`` is None.
    """
    if frame is None:
        frame, source = _training_frame()
    if frame is None or not len(frame):
        raise RuntimeError("no training data available")

    feature_cols = FEATURE_KEYS + [f"pass{p}" for p in AVAILABLE_PASSES]
    for c in feature_cols:
        if c not in frame.columns:
            frame[c] = 0
    frame = relabel_savings(frame)
    X = frame[feature_cols].astype(float)
    y = frame[TARGET_COL].astype(float)

    model = XGBRegressor(
        n_estimators=120, max_depth=4, learning_rate=0.1, subsample=0.9,
        random_state=42, objective="reg:squarederror",
    )
    model.fit(X, y)
    model.training_source = source  # 'measured' | 'model_labels' | 'provided'
    try:
        joblib.dump(model, MODEL_PATH)
    except OSError:
        pass
    return model


def get_model() -> XGBRegressor:
    expected = FEATURE_KEYS + [f"pass{p}" for p in AVAILABLE_PASSES]
    if MODEL_PATH.exists():
        try:
            model = joblib.load(MODEL_PATH)
            trained_on = getattr(model, "feature_names_in_", None)
            if trained_on is not None and list(trained_on) == expected:
                # schema must match this extractor before we reuse a cache
                if not hasattr(model, "training_source"):
                    model.training_source = "cached"
                return model
        except Exception:
            pass
    return train_model()


def rank_passes(features: dict, model) -> tuple[list[str], float]:
    """-Mbalanced: score the candidate pool (<50 ms) and pick the best."""
    feature_cols = FEATURE_KEYS + [f"pass{p}" for p in AVAILABLE_PASSES]
    rows = []
    for seq in CANDIDATE_SEQUENCES:
        row = row_from(features, seq)
        rows.append([row.get(c, 0.0) for c in feature_cols])
    preds = model.predict(pd.DataFrame(rows, columns=feature_cols))
    best = int(np.argmax(preds))
    return list(CANDIDATE_SEQUENCES[best]), float(preds[best])


if __name__ == "__main__":
    m = train_model()
    print(f"Model trained (source={getattr(m, 'training_source', '?')}) "
          f"-> {MODEL_PATH}")

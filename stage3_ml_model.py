"""Stage 3 — ML pass ranker (-Mbalanced mode).

Training data now comes from **real measurements** produced by
``ml_models/collect_dataset.py`` (``datasets/measurements.csv``): for each
benchmark x candidate pass sequence it records real Stage-2 features, real
pass-scheduler wall time, real JIT-measured program runtime, and RAPL
compile energy when available (otherwise the documented P-hat x T-hat
software estimate).  The label is EDP savings relative to the
no-optional-pass baseline of the same benchmark.

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
    """Fallback: run every candidate sequence for real on demo sources and
    label with static-cost-model EDP savings (timings are measured)."""
    import time

    from energy.execution import jit_run
    from stage2.extractor import IRFeatureExtractor, static_cost
    from stage2.pass_gating import DEFAULT_POWER_W, load_profiles

    from energy.experiments import merged_pipeline

    profiles = load_profiles()
    power = float(profiles.get("meta", {}).get("power_w", DEFAULT_POWER_W))
    rows = []
    for name, src in _demo_sources():
        try:
            ir_text = str(merged_pipeline(src).ir_text)
            features = IRFeatureExtractor(ir_text).extract_features()
        except Exception:
            continue

        baseline = None
        for seq in CANDIDATE_SEQUENCES:
            t0 = time.perf_counter()
            try:
                from backend.passes import apply_pass_sequence
                result = apply_pass_sequence(ir_text, seq)
                t_compile = time.perf_counter() - t0
                opt = result.ir_text
            except Exception:
                continue
            cost = static_cost(opt)
            try:
                t_run = 0.0
                for _ in range(3):
                    ta = time.perf_counter()
                    jit_run(opt)
                    t_run += time.perf_counter() - ta
                t_run /= 3.0
            except RuntimeError:
                t_run = cost * 1e-6  # static fallback when JIT not possible
            edp = (power * t_compile) * max(t_run, 1e-9)
            if not seq:
                baseline = edp
            row = row_from(features, seq)
            row["benchmark"] = name
            row["sequence"] = sequence_key(seq)
            row["t_compile_s"] = t_compile
            row["runtime_s_median"] = t_run
            row["edp_proxy"] = edp
            row["energy_source"] = "estimated"
            rows.append((row, edp))
        # fill savings now that the baseline of this benchmark is known
        recent = rows[-len(CANDIDATE_SEQUENCES):]
        if baseline:
            for row, edp in recent:
                row[TARGET_COL] = (baseline - edp) / baseline
        else:
            for row, _edp in recent:
                row[TARGET_COL] = 0.0
    frame = pd.DataFrame([r for r, _ in rows])
    return frame


def train_model(frame: pd.DataFrame | None = None) -> XGBRegressor:
    """Train XGBoost on measured (or honestly model-labelled) data."""
    source = "provided"
    if frame is None:
        frame, source = _training_frame()
    if frame is None or not len(frame):
        raise RuntimeError("no training data available")

    feature_cols = FEATURE_KEYS + [f"pass{p}" for p in AVAILABLE_PASSES]
    for c in feature_cols:
        if c not in frame.columns:
            frame[c] = 0
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

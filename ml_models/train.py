"""Train the -Mbalanced pass-ranker on measured data.

Steps (plan Phase 4, step 18):

  1. if ``datasets/measurements.csv`` is missing, build it first with
     ``ml_models.collect_dataset`` (real benchmark x pass-sequence runs),
  2. train XGBoost on Stage-2 features + pass-sequence flags ->
     EDP savings vs the benchmark's no-pass baseline,
  3. report train-set quality (R², top-1 agreement) so the model's
     predictions can be sanity-checked against the measurements.

Usage::

    ./venv/bin/python -m ml_models.train
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from stage3_ml_model import (
    AVAILABLE_PASSES, FEATURE_KEYS, MODEL_PATH, TARGET_COL, load_dataset,
    train_model,
)


def main() -> None:
    df = load_dataset()
    if df is None:
        print("No measured dataset found — collecting it now ...")
        from ml_models.collect_dataset import collect, MEASUREMENTS_CSV
        files = sorted((ROOT / "benchmarks").glob("*.c"))
        if not files:
            files = [ROOT / "test_program.c"]
        df = collect(files, progress=lambda m: print(m, flush=True))
        MEASUREMENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(MEASUREMENTS_CSV, index=False)
        print(f"Wrote {len(df)} rows -> {MEASUREMENTS_CSV}")

    print(f"Training on {len(df)} measured rows "
          f"({df['benchmark'].nunique()} benchmarks x sequences)")
    model = train_model(df)

    cols = FEATURE_KEYS + [f"pass{p}" for p in AVAILABLE_PASSES]
    X = df[cols].astype(float)
    y = df[TARGET_COL].astype(float)
    pred = model.predict(X)

    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0

    # top-1 agreement: does the row the model likes best per benchmark
    # match the measurement's best row?
    agree = 0
    total = 0
    for name, grp in df.groupby("benchmark"):
        idx = list(grp.index)
        pos = [list(df.columns).index(c) for c in cols]
        preds = model.predict(df.loc[idx, cols].astype(float))
        best_pred = idx[int(np.argmax(preds))]
        best_true = idx[int(np.argmax(grp[TARGET_COL].values))]
        agree += int(best_pred == best_true)
        total += 1

    print(f"Model: {MODEL_PATH}")
    print(f"  training_source : {getattr(model, 'training_source', '?')}")
    print(f"  train R²        : {r2:.3f}")
    print(f"  top-1 agreement : {agree}/{total} benchmarks")
    print(f"  target column   : {TARGET_COL} "
          f"(EDP savings vs no-pass baseline; min {y.min():.3f}, max {y.max():.3f})")


if __name__ == "__main__":
    main()

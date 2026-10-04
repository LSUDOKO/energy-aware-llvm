"""Leave-one-benchmark-out evaluation of the -Mbalanced pass ranker.

Training R^2 on the data the model was fitted to says nothing about whether
it picks good pass sequences for a program it has never seen.  For each
benchmark this script

  1. trains the XGBoost ranker on every *other* benchmark,
  2. lets it choose among the production candidate pool
     (``stage3_ml_model.CANDIDATE_SEQUENCES``) for the held-out program,
  3. looks up what that choice actually achieved in the measurements.

Reported per held-out benchmark: realized lifecycle-EDP savings of the
choice, the oracle (best candidate in hindsight), the no-pass baseline
(0 by definition) and the regret (oracle - realized).  A ranker is useful
only if it beats "never optimize" and "always -O2" on average.

Usage::

    ./venv/bin/python -m ml_models.evaluate [--runs 10000] [--out reports/ml]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

import stage3_ml_model as s3

POOL_KEYS = [s3.sequence_key(c) for c in s3.CANDIDATE_SEQUENCES]


def pool_name(key: str) -> str:
    """Readable name of a pool sequence (presets are stored expanded)."""
    for seq in s3.CANDIDATE_SEQUENCES:
        if s3.sequence_key(seq) == key:
            if not seq:
                return "never optimize"
            if seq == list(s3.AVAILABLE_PASSES):
                return "all passes"
            return "+".join(p.lstrip("-") for p in seq)
    return key


def _feature_cols() -> list[str]:
    return s3.FEATURE_KEYS + [f"pass{p}" for p in s3.AVAILABLE_PASSES]


def _fit(frame: pd.DataFrame) -> XGBRegressor:
    model = XGBRegressor(n_estimators=120, max_depth=4, learning_rate=0.1,
                         subsample=0.9, random_state=42,
                         objective="reg:squarederror")
    cols = _feature_cols()
    model.fit(frame[cols].astype(float), frame[s3.TARGET_COL].astype(float))
    return model


def evaluate(frame: pd.DataFrame, n_runs: int = s3.BALANCED_RUNS) -> dict:
    """Leave-one-benchmark-out evaluation on a measured frame."""
    labelled = s3.relabel_savings(frame, n_runs=n_runs)
    cols = _feature_cols()
    rows = []
    for held in sorted(labelled["benchmark"].unique()):
        train = labelled[labelled["benchmark"] != held]
        test = labelled[(labelled["benchmark"] == held)
                        & labelled["sequence"].isin(POOL_KEYS)]
        if train.empty or test.empty:
            continue
        model = _fit(train)
        preds = model.predict(test[cols].astype(float))
        # same decision rule as production (-Mbalanced): fall back to "no
        # passes" when nothing is predicted to help
        idx = s3.best_positive_index(preds)
        none_row = test[test["sequence"] == "(none)"]
        pick = test.iloc[idx] if idx is not None else none_row.iloc[0]
        realized = float(pick[s3.TARGET_COL])
        oracle_row = test.iloc[int(np.argmax(test[s3.TARGET_COL].values))]
        rows.append({
            "benchmark": held,
            "picked": pick["sequence"],
            "by_sequence": {r.sequence: float(r[s3.TARGET_COL])
                            for _, r in test.iterrows()},
            "predicted": float(preds[idx]) if idx is not None else 0.0,
            "realized": realized,
            "oracle_sequence": oracle_row["sequence"],
            "oracle": float(oracle_row[s3.TARGET_COL]),
            "regret": float(oracle_row[s3.TARGET_COL]) - realized,
        })
    if not rows:
        raise ValueError("no benchmark had both training data and a full pool")
    realized = [r["realized"] for r in rows]
    # fixed policies: apply the same pool sequence to every held-out program
    fixed = {}
    for key in POOL_KEYS:
        vals = [r["by_sequence"][key] for r in rows if key in r["by_sequence"]]
        if len(vals) == len(rows):
            fixed[pool_name(key)] = float(np.mean(vals))
    return {
        "n_runs": n_runs,
        "benchmarks": rows,
        "mean_realized": float(np.mean(realized)),
        "mean_oracle": float(np.mean([r["oracle"] for r in rows])),
        "fixed_policies": fixed,
        "best_fixed_policy": max(fixed, key=fixed.get) if fixed else None,
        "mean_regret": float(np.mean([r["regret"] for r in rows])),
        "never_worse_than_baseline": int(sum(r >= -1e-9 for r in realized)),
        "n_benchmarks": len(rows),
    }


def to_markdown(res: dict) -> str:
    L = [f"# -Mbalanced leave-one-benchmark-out evaluation "
         f"(lifecycle EDP at {res['n_runs']:,} runs)\n",
         "| held-out | ranker picked | realized | oracle | regret |",
         "|---|---|---:|---|---:|"]
    for r in res["benchmarks"]:
        L.append(f"| {r['benchmark']} | `{pool_name(r['picked'])}` | "
                 f"{r['realized'] * 100:+.1f}% | "
                 f"{r['oracle'] * 100:+.1f}% (`{pool_name(r['oracle_sequence'])}`) | "
                 f"{r['regret'] * 100:.1f} pp |")
    L += ["", "| policy (same choice for every program) | mean savings |",
          "|---|---:|"]
    L.append(f"| **ranker (leave-one-out)** | **{res['mean_realized'] * 100:+.1f}%** |")
    for name, val in sorted(res["fixed_policies"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {name} | {val * 100:+.1f}% |")
    L.append(f"| oracle (best pool member per program) | "
             f"{res['mean_oracle'] * 100:+.1f}% |")
    L.append("")
    L.append(f"Mean regret {res['mean_regret'] * 100:.1f} pp; the ranker is no "
             f"worse than not optimizing on {res['never_worse_than_baseline']}/"
             f"{res['n_benchmarks']} held-out programs. Best fixed policy: "
             f"`{res['best_fixed_policy']}`.")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=s3.BALANCED_RUNS)
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "ml")
    args = ap.parse_args()
    frame = s3.load_dataset(args.dataset)
    if frame is None:
        raise SystemExit("no measured dataset; run ml_models.collect_dataset")
    res = evaluate(frame, args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "loo_evaluation.json").write_text(json.dumps(res, indent=2))
    md = to_markdown(res)
    (args.out / "loo_evaluation.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()

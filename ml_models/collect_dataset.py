"""Measured dataset builder (plan Stage 3, step 17).

For each benchmark x candidate pass sequence, record:

  * Stage-2 features (real extractor output),
  * ``t_compile_s`` — real wall time of the full pass sequence,
  * ``runtime_s_median`` — real JIT-measured program runtime (median of N
    native executions of the optimised module),
  * ``energy_compile_j`` — RAPL package energy when readable on this
    machine, otherwise blank with ``energy_source='estimated'`` and the
    label built from the documented ``P-hat x T`` software estimate,
  * ``t_shared_s`` — front-end + code-generation time every build pays,
  * ``edp_savings`` — *lifecycle* EDP savings (compile once, run
    ``BALANCED_RUNS`` times) relative to the benchmark's no-optional-pass
    baseline; recomputable from the timing columns for any run count.

Outputs:

  * ``datasets/measurements.csv`` — training data for -Mbalanced,
  * ``profiles/per_pass.json``   — median per-pass times (and package
    power when measurable) consumed by the Stage-2 gate and the GA.

Usage::

    ./venv/bin/python -m ml_models.collect_dataset \\
        --benchmarks benchmarks/*.c --random-sequences 6
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from backend.passes import AVAILABLE_PASSES, apply_pass_sequence
from energy.execution import jit_run, object_bytes
from energy.experiments import merged_pipeline
from stage2.extractor import IRFeatureExtractor, static_cost
from stage2.pass_gating import (
    DEFAULT_POWER_W, PROFILE_PATH, save_profiles,
)
from stage3_ml_model import (
    BALANCED_RUNS, FEATURE_KEYS, TARGET_COL, relabel_savings, row_from,
    sequence_key,
)

DATASETS_DIR = ROOT / "datasets"
MEASUREMENTS_CSV = DATASETS_DIR / "measurements.csv"

RNG_SEED = 20261004


# ---------------------------------------------------------------------------
# energy: RAPL when readable, honest fallback otherwise
# ---------------------------------------------------------------------------
def _rapl_reader():
    """RAPL reader when the energy counters are readable, else None."""
    try:
        from energy.rapl_reader import RAPLReader
        reader = RAPLReader.create()
        return reader if reader.available() else None
    except Exception:
        return None


def _package_power_w(reader, work, repeats: int = 20) -> float | None:
    """Measure active package power [W] = batched RAPL energy / time."""
    if reader is None:
        return None
    try:
        before = reader.read()
        t0 = time.perf_counter()
        for _ in range(repeats):
            work()
        dt = time.perf_counter() - t0
        after = reader.read()
        joules = reader.read_delta(before, after) / 1e6
        if dt <= 0 or joules <= 0:
            return None
        return joules / dt
    except Exception:
        return None


def energy_probe_batch(reader, ir_text, seq, batch: int = 10) -> float | None:
    """Joules for one application of `seq` (idle-uncorrected batched RAPL)."""
    if reader is None:
        return None
    try:
        before = reader.read()
        for _ in range(batch):
            apply_pass_sequence(ir_text, seq)
        after = reader.read()
        return reader.read_delta(before, after) / 1e6 / batch
    except Exception:
        return None


# ---------------------------------------------------------------------------
# candidate sequences
# ---------------------------------------------------------------------------
def candidate_sequences(n_random: int = 6, seed: int = RNG_SEED) -> list[list[str]]:
    import random
    rng = random.Random(seed)
    seqs: list[list[str]] = [
        [],                      # baseline: no optional passes
        ["-O1"], ["-O2"], ["-O3"],
        ["-sroa", "-simplifycfg"],
        ["-sroa", "-sccp", "-simplifycfg", "-gvn", "-dse"],
        ["-loop-rotate", "-loop-unroll", "-lcsr"],
    ]
    seen = {sequence_key(s) for s in seqs}
    base = len(seqs)
    tries = 0
    while len(seqs) - base < n_random and tries < n_random * 20:
        tries += 1
        k = rng.randint(1, min(6, len(AVAILABLE_PASSES)))
        subset = rng.sample(AVAILABLE_PASSES, k)
        # keep genome order (canonical) so sequences dedupe sensibly
        seq = [p for p in AVAILABLE_PASSES if p in subset]
        key = sequence_key(seq)
        if key not in seen:
            seen.add(key)
            seqs.append(seq)
    return seqs


# ---------------------------------------------------------------------------
# dataset build
# ---------------------------------------------------------------------------
def collect(
    sources: list[Path],
    n_random: int = 6,
    runtime_repeats: int = 7,
    progress=None,
) -> pd.DataFrame:
    log = progress or (lambda m: None)
    reader = _rapl_reader()
    power_w = DEFAULT_POWER_W  # calibrated below when RAPL is readable

    rows: list[dict] = []
    pass_times: dict[str, list[float]] = {}

    # warm-up parse so the first benchmark doesn't pay JIT init costs
    for src in sources:
        source = src.read_text()
        t0 = time.perf_counter()
        ir_text = str(merged_pipeline(source).ir_text)
        t_frontend = time.perf_counter() - t0
        t0 = time.perf_counter()
        object_bytes(ir_text)
        t_shared = t_frontend + (time.perf_counter() - t0)
        features = IRFeatureExtractor(ir_text).extract_features()
        base_value = jit_run(ir_text)   # every candidate must preserve this
        seqs = candidate_sequences(n_random)
        log(f"{src.name}: {len(seqs)} sequences")

        bench_rows: list[dict] = []

        # calibrate active package power once, from real RAPL + wall time
        if reader is not None and power_w == DEFAULT_POWER_W:
            measured_w = _package_power_w(
                reader, lambda: apply_pass_sequence(ir_text, ["-O1"]), repeats=20)
            if measured_w:
                power_w = measured_w
                log(f"  calibrated package power: {power_w:.1f} W (RAPL)")

        for seq in seqs:
            # --- measured compile time (real pass execution) ---
            t0 = time.perf_counter()
            result = apply_pass_sequence(ir_text, seq)
            t_compile = time.perf_counter() - t0
            if not result.verified:
                log(f"  {sequence_key(seq)}: FAILED verification, skipped")
                continue
            for name, dt in result.pass_times.items():
                pass_times.setdefault(name, []).append(dt)

            opt = result.ir_text

            # --- measured runtime (compile once, measure many) ---
            from energy.execution import measure_runtime
            try:
                runtime, value = measure_runtime(opt, samples=runtime_repeats)
                jit_ok = True
            except RuntimeError:
                runtime = static_cost(opt) * 1e-6
                jit_ok = False
                value = None
            if jit_ok and value != base_value:
                log(f"  {sequence_key(seq)}: CHANGED main() result "
                    f"({value} != {base_value}), skipped")
                continue

            # --- energy ---
            if reader is not None:
                # batch the compile so a single RAPL delta is meaningful
                energy = energy_probe_batch(reader, ir_text, seq, batch=10)
                energy_source = "rapl" if energy is not None else "estimated"
            else:
                energy = None
                energy_source = "estimated"

            row = row_from(features, seq)
            row.update({
                "benchmark": src.name,
                "sequence": sequence_key(seq),
                "t_compile_s": t_compile,
                "t_shared_s": t_shared,
                "runtime_s_median": runtime if jit_ok else "",
                "instructions_before": result.instructions_before,
                "instructions_after": result.instructions_after,
                "static_cost_after": static_cost(opt),
                "energy_compile_j": energy if energy is not None else "",
                "energy_source": energy_source,
            })
            bench_rows.append(row)
            log(f"  {sequence_key(seq)}: T={t_compile * 1e3:.2f} ms "
                f"run={runtime * 1e6:.2f} us")
        rows.extend(bench_rows)

    df = relabel_savings(pd.DataFrame(rows), n_runs=BALANCED_RUNS,
                         power_w=power_w)

    # persist per-pass profiles (median measured times) for gate + GA
    if pass_times:
        profiles = load_or_empty()
        passes = profiles.setdefault("passes", {})
        for name, times in pass_times.items():
            med = statistics.median(times)
            passes[name] = {
                "t_seconds": med,
                "energy_j": med * power_w,
                "n_samples": len(times),
                "source": "collect_dataset",
            }
        meta = profiles.setdefault("meta", {})
        meta["power_w"] = power_w
        meta["power_source"] = "rapl" if reader is not None else "default_tdp_placeholder"
        meta["run_j_per_unit"] = 1e-6
        meta["run_s_per_unit"] = 1e-6
        meta["amortization"] = 1_000_000
        meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_profiles(profiles)
        log(f"wrote per-pass profiles -> {PROFILE_PATH}")
    return df


def load_or_empty() -> dict:
    try:
        from stage2.pass_gating import load_profiles
        return load_profiles() or {}
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--benchmarks", nargs="*", default=None,
                    help="benchmark .c files (default: benchmarks/*.c)")
    ap.add_argument("--random-sequences", type=int, default=6)
    ap.add_argument("--out", default=str(MEASUREMENTS_CSV))
    args = ap.parse_args()

    if args.benchmarks:
        files = [Path(p) for pat in args.benchmarks for p in glob.glob(pat)]
    else:
        files = sorted((ROOT / "benchmarks").glob("*.c"))
    if not files:
        raise SystemExit("no benchmark sources found")

    df = collect(files, n_random=args.random_sequences,
                 progress=lambda m: print(m, flush=True))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nWrote {len(df)} rows -> {out}")
    print(f"Columns: {len(df.columns)} "
          f"({len(FEATURE_KEYS)} Stage-2 features + pass flags + measurements)")


if __name__ == "__main__":
    main()

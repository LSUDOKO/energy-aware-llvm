#!/usr/bin/env python
"""Run the whole benchmark suite and write measured results.

For every kernel in ``benchmarks/manifest.json`` this builds the program with

  unopt        unified front-end, no optional passes   (lifecycle baseline)
  -Meco        strict-budget gated pipeline
  -Mbalanced   XGBoost pass ranker, gated
  -Mperf       genetic-algorithm search (measured 1/EDP), gated
  clang-O0 / clang-O2   the system clang on the same C source (external
                        reference; not energy-aware)

then measures, per build:

  * compile time and compile energy (E_compile)
  * correctness: the linked native executable's exit code and the timing
    binary's return value must equal the manifest value
  * native runtime and run energy (E_run) of the *object file the compiler
    emitted*, executed by ``backend/native_harness.c`` (no JIT, no Python in
    the timed loop); trials are interleaved across builds and the median
    reported
  * object size

Energy is RAPL package joules (idle-corrected) when the counter is readable;
otherwise the energy columns are labelled ``estimated`` (P-hat x T) and every
conclusion rests on time.  Nothing is simulated.

Usage::

    ./venv/bin/python benchmarks/run_benchmarks.py                 # full suite
    ./venv/bin/python benchmarks/run_benchmarks.py --only fib_rec --reps 3
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import native                                     # noqa: E402
from backend.passes import emit_object_file                    # noqa: E402
from compile_pipeline import compile_source, default_meter     # noqa: E402
from energy.lifecycle import Cost, break_even_runs, edp_savings_pct  # noqa: E402
from stage2.pass_gating import DEFAULT_POWER_W, load_profiles  # noqa: E402

BENCH_DIR = ROOT / "benchmarks"
DEFAULT_OUT = ROOT / "reports" / "benchmarks"
OURS = ("-Meco", "-Mbalanced", "-Mperf")
CLANG = {"clang-O0": "-O0", "clang-O2": "-O2"}
RUN_COUNTS = (1, 100, 10_000, 1_000_000)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------
def object_text_bytes(obj: Path) -> int | None:
    """Size of the .text section (machine code only)."""
    try:
        out = subprocess.run(["size", "-A", str(obj)], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in out.splitlines():
        if line.startswith(".text"):
            return int(line.split()[1])
    return None


def build_ours(entry: dict, src: str, mode: str, work: Path, n_runs: int,
               ga_kwargs: dict) -> dict:
    res = compile_source(src, mode=mode, emit_object=True, n_runs=n_runs,
                         name=f"{entry['name']}_{mode.lstrip('-')}",
                         ga_kwargs=ga_kwargs if mode == "-Mperf" else None)
    if not res["success"]:
        raise RuntimeError(f"{entry['name']} {mode}: {res.get('error')}")
    obj = Path(res["object_file"])
    en = res["energy"]
    return {
        "config": mode, "object": obj,
        "compile_s": en["compile_time_s"], "compile_j": en["e_compile_j"],
        "compile_j_baseline": en["e_compile_baseline_j"],
        "compile_s_baseline": en["compile_time_baseline_s"],
        "energy_source": en["source"],
        "passes": res["selected_passes"],
        "instr_before": res["instructions"]["before"],
        "instr_after": res["instructions"]["after"],
        "ga": res.get("ga"),
        "stage_j": en.get("stage_j"), "stage_s": en.get("stage_s"),
        "llvm_ir": res["llvm_ir"],
    }


def build_unopt(entry: dict, ir_text: str, baseline_s: float,
                baseline_j: float, work: Path) -> dict:
    obj = emit_object_file(ir_text, work / f"{entry['name']}_unopt.o")
    return {"config": "unopt", "object": Path(obj), "compile_s": baseline_s,
            "compile_j": baseline_j, "passes": [], "ga": None}


FLOAT_LITERAL = re.compile(r"(?<![\w.])(\d+\.\d*)(?![\w.])")


def as_single_precision_c(src: str) -> str:
    """MiniC literals like ``0.1`` are ``float``; in C they are ``double``.
    Suffix them with ``f`` so clang computes exactly the same program."""
    return FLOAT_LITERAL.sub(r"\1f", src)


def build_clang(entry: dict, flag: str, label: str, work: Path, meter,
                power_w: float) -> dict:
    obj = work / f"{entry['name']}_{label}.o"
    c_file = work / f"{entry['name']}_f32.c"
    c_file.write_text(as_single_precision_c(
        (BENCH_DIR / entry["file"]).read_text()))
    cmd = [native.find_cc(), flag, "-w", "-c", str(c_file), "-o", str(obj)]

    def once():
        subprocess.run(cmd, check=True, capture_output=True)

    m = meter.measure(once)
    joules = m.energy_j if m.energy_j is not None else power_w * m.seconds
    return {"config": label, "object": obj, "compile_s": m.seconds,
            "compile_j": joules,
            "energy_source": "rapl" if m.energy_j is not None else "estimated",
            "passes": [], "ga": None}


# ---------------------------------------------------------------------------
# native measurement
# ---------------------------------------------------------------------------
def measure_all(builds: list[dict], expected: int, reps: int, work: Path,
                meter, power_w: float, window_s: float) -> None:
    """Fill runtime/energy/correctness for each build, trials interleaved."""
    for b in builds:
        exe = native.build_timing_binary(b["object"],
                                         work / f"{b['object'].stem}.t")
        b["exe"] = exe
        b["calls"] = native.calibrate_calls(exe, target_seconds=window_s)
        b["exit_code"] = native.run_exit_code(b["object"])
        b["correct"] = b["exit_code"] == expected % 256
        b["samples_s"], b["samples_j"] = [], []

    n = len(builds)
    for rep in range(reps):
        order = builds[rep % n:] + builds[:rep % n]          # rotate each round
        for b in order:
            out: dict = {}

            def run(b=b, out=out):
                out["t"] = native.time_native(b["exe"], b["calls"])

            m = meter.measure(run, calls=1)
            t = out["t"]
            if t.return_value != expected:
                b["correct"] = False
            b["samples_s"].append(t.seconds_per_call)
            if m.energy_j is not None:
                b["samples_j"].append(m.energy_j / t.calls)
    for b in builds:
        b["run_s"] = statistics.median(b["samples_s"])
        if b["samples_j"]:
            b["run_j"] = statistics.median(b["samples_j"])
            b["run_energy_source"] = "rapl"
        else:
            b["run_j"] = power_w * b["run_s"]
            b["run_energy_source"] = "estimated"


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
def lifecycle_rows(builds: list[dict]) -> None:
    base = next(b for b in builds if b["config"] == "unopt")
    base_cost = Cost(base["compile_j"], base["compile_s"],
                     base["run_j"], base["run_s"])
    for b in builds:
        cost = Cost(b["compile_j"], b["compile_s"], b["run_j"], b["run_s"])
        b["break_even_runs"] = break_even_runs(base_cost, cost)
        b["edp_savings_pct"] = {str(n): edp_savings_pct(base_cost, cost, n)
                                for n in RUN_COUNTS}
        b["speedup_vs_unopt"] = base["run_s"] / b["run_s"]
        b["run_energy_saving_pct"] = ((base["run_j"] - b["run_j"])
                                      / base["run_j"] * 100.0)


def run_kernel(entry: dict, args, work: Path, meter, power_w: float) -> dict:
    src = (BENCH_DIR / entry["file"]).read_text()
    log(f"== {entry['name']}: {entry['description']}")
    builds: list[dict] = []
    ir_text = None
    base_s = base_j = None
    ga_kwargs = {"generations": args.ga_generations,
                 "pop_size": args.ga_population, "seed": args.seed}
    for mode in OURS:
        b = build_ours(entry, src, mode, work, args.runs, ga_kwargs)
        ir_text = ir_text or b["llvm_ir"]
        base_s = base_s if base_s is not None else b["compile_s_baseline"]
        base_j = base_j if base_j is not None else b["compile_j_baseline"]
        builds.append(b)
        log(f"   built {mode:11s} compile {b['compile_s'] * 1e3:8.2f} ms, "
            f"passes={len(b['passes'])}, instr {b['instr_before']}->{b['instr_after']}")
    builds.insert(0, build_unopt(entry, ir_text, base_s, base_j, work))
    if not args.no_clang:
        for label, flag in CLANG.items():
            builds.append(build_clang(entry, flag, label, work, meter, power_w))
    measure_all(builds, entry["expected_return"], args.reps, work, meter,
                power_w, args.window)
    lifecycle_rows(builds)

    for b in builds:
        b["object_bytes"] = Path(b["object"]).stat().st_size
        b["text_bytes"] = object_text_bytes(Path(b["object"]))
        log(f"   {b['config']:11s} run {b['run_s'] * 1e6:9.3f} us "
            f"(x{b['speedup_vs_unopt']:5.2f})  correct={b['correct']}  "
            f"break-even={b['break_even_runs']}")
    return {
        "name": entry["name"], "description": entry["description"],
        "expected_return": entry["expected_return"],
        "builds": [{k: (str(v) if isinstance(v, Path) else v)
                    for k, v in b.items()
                    if k not in ("exe", "llvm_ir", "samples_s", "samples_j")}
                   for b in builds],
    }


def machine_info(meter) -> dict:
    cpu = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"cpu": cpu, "python": platform.python_version(),
            "kernel": platform.release(),
            "energy_source": meter.source,
            "idle_power_w": meter.idle_power_w()}


def write_outputs(results: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    cols = ["kernel", "config", "correct", "run_us", "speedup_vs_unopt",
            "compile_ms", "compile_mj", "run_uj", "run_energy_source",
            "break_even_runs"] + [f"edp_savings_pct_{n}" for n in RUN_COUNTS] + \
           ["object_bytes", "text_bytes", "instr_before", "instr_after", "passes"]
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for k in results["kernels"]:
            for b in k["builds"]:
                w.writerow([
                    k["name"], b["config"], b["correct"],
                    f"{b['run_s'] * 1e6:.4f}", f"{b['speedup_vs_unopt']:.4f}",
                    f"{b['compile_s'] * 1e3:.4f}", f"{b['compile_j'] * 1e3:.4f}",
                    f"{b['run_j'] * 1e6:.4f}", b["run_energy_source"],
                    "" if b["break_even_runs"] is None else f"{b['break_even_runs']:.1f}",
                    *[f"{b['edp_savings_pct'][str(n)]:.2f}" for n in RUN_COUNTS],
                    b["object_bytes"], b["text_bytes"] or "",
                    b.get("instr_before", ""), b.get("instr_after", ""),
                    " ".join(b.get("passes") or []),
                ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="comma-separated kernel names")
    ap.add_argument("--reps", type=int, default=7,
                    help="interleaved native timing trials per build")
    ap.add_argument("--window", type=float, default=0.2,
                    help="seconds per timed native batch (energy resolution)")
    ap.add_argument("--runs", type=int, default=10_000,
                    help="program executions assumed by the lifecycle EDP")
    ap.add_argument("--ga-generations", type=int, default=8)
    ap.add_argument("--ga-population", type=int, default=16)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-clang", action="store_true")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    entries = json.loads((BENCH_DIR / "manifest.json").read_text())["entries"]
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["name"] in wanted]
        if not entries:
            raise SystemExit(f"no kernels match --only {args.only}")

    meter = default_meter()
    profiles = load_profiles()
    power_w = float(profiles.get("meta", {}).get("power_w", DEFAULT_POWER_W))
    log(f"energy source: {meter.source}"
        + ("" if meter.available else
           "  (RAPL unreadable: energy columns are P-hat x T estimates; "
           "run `sudo energy/setup_rapl_access.sh direct`)"))
    work = ROOT / "build" / "bench"
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    kernels = [run_kernel(e, args, work, meter, power_w) for e in entries]
    results = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": machine_info(meter),
        "settings": {"reps": args.reps, "window_s": args.window,
                     "n_runs": args.runs, "ga_generations": args.ga_generations,
                     "ga_population": args.ga_population, "seed": args.seed,
                     "power_w_estimate": power_w},
        "kernels": kernels,
        "wall_seconds": time.time() - t0,
    }
    write_outputs(results, args.out)
    wrong = [(k["name"], b["config"]) for k in kernels for b in k["builds"]
             if not b["correct"]]
    log(f"wrote {args.out}/results.json and results.csv "
        f"({results['wall_seconds']:.0f} s)")
    if wrong:
        log(f"INCORRECT BUILDS: {wrong}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

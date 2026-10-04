#!/usr/bin/env python
"""Turn ``results.json`` from run_benchmarks.py into charts and RESULTS.md.

Every number in the report is read from the measured results file; nothing is
typed in by hand.  Charts (PNG) are written next to the results::

    runtime_speedup.png   native speedup vs the unoptimized build
    compile_time.png      compile time per build (log scale)
    edp_vs_runs.png       lifecycle EDP savings as the run count grows
    break_even.png        executions needed to repay extra compile energy
    perfmode_ga.png       -Mperf genetic-algorithm convergence per kernel
    compile_stages.png    where each mode spends its compile time

Usage::

    ./venv/bin/python benchmarks/report_benchmarks.py [--dir reports/benchmarks]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from energy.lifecycle import Cost, edp_savings_pct              # noqa: E402

OURS = ("-Meco", "-Mbalanced", "-Mperf")
ALL = OURS + ("clang-O0", "clang-O2")
COLORS = {"unopt": "#8a8f98", "-Meco": "#2a9d8f", "-Mbalanced": "#e9a23b",
          "-Mperf": "#d1495b", "clang-O0": "#9aa5d1", "clang-O2": "#3d5a99"}
STAGE_COLORS = {"frontend": "#264653", "stage2": "#2a9d8f", "search": "#e9c46a",
                "gate": "#f4a261", "passes": "#e76f51", "codegen": "#6d597a"}

plt.rcParams.update({
    "figure.dpi": 140, "savefig.dpi": 140, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25, "axes.axisbelow": True,
    "axes.titleweight": "bold", "legend.frameon": False,
})


def geomean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def build_of(kernel: dict, config: str) -> dict | None:
    return next((b for b in kernel["builds"] if b["config"] == config), None)


def grouped_bars(ax, kernels, configs, value_fn, log=False):
    n = len(configs)
    width = 0.82 / n
    for i, cfg in enumerate(configs):
        ys = []
        for k in kernels:
            b = build_of(k, cfg)
            ys.append(value_fn(b) if b else float("nan"))
        xs = [j + (i - (n - 1) / 2) * width for j in range(len(kernels))]
        ax.bar(xs, ys, width, label=cfg, color=COLORS.get(cfg, "#999"))
    ax.set_xticks(range(len(kernels)))
    ax.set_xticklabels([k["name"] for k in kernels], rotation=35, ha="right")
    if log:
        ax.set_yscale("log")


def chart_runtime(results, out):
    ks = results["kernels"]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    grouped_bars(ax, ks, ALL, lambda b: b["speedup_vs_unopt"])
    ax.axhline(1.0, color="#444", lw=1, ls="--")
    ax.set_ylabel("native speedup vs unoptimized build (x)")
    ax.set_title("Generated-code speed: measured native runtime of each object file")
    gm = {c: geomean([build_of(k, c)["speedup_vs_unopt"] for k in ks]) for c in ALL}
    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.3),
              title="geomean speedup: " + "   ".join(f"{c} {gm[c]:.2f}x" for c in ALL),
              title_fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "runtime_speedup.png")
    plt.close(fig)


def chart_compile(results, out):
    ks = results["kernels"]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    grouped_bars(ax, ks, ("unopt",) + ALL, lambda b: b["compile_s"] * 1e3, log=True)
    ax.set_ylabel("compile time (ms, log)")
    ax.set_title("Compile cost: front-end + search + passes + code generation")
    ax.legend(ncol=6, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.3))
    fig.tight_layout()
    fig.savefig(out / "compile_time.png")
    plt.close(fig)


def lifecycle_savings(k, cfg, n):
    base, b = build_of(k, "unopt"), build_of(k, cfg)
    return edp_savings_pct(
        Cost(base["compile_j"], base["compile_s"], base["run_j"], base["run_s"]),
        Cost(b["compile_j"], b["compile_s"], b["run_j"], b["run_s"]), n)


def chart_edp(results, out):
    ks = results["kernels"]
    ns = [10 ** (e / 4) for e in range(0, 29)]          # 1 .. 1e7
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    for cfg in ALL:
        med = [statistics.median(lifecycle_savings(k, cfg, n) for k in ks)
               for n in ns]
        ax.plot(ns, med, label=cfg, color=COLORS[cfg], lw=2)
    ax.axhline(0, color="#444", lw=1)
    ax.set_xscale("log")
    ax.set_ylim(-150, 100)
    ax.set_xlabel("times the compiled program is executed")
    ax.set_ylabel("median EDP savings vs unoptimized (%)")
    ax.set_title("Lifecycle EDP: compile cost is repaid only if the program runs enough")
    ax.legend(ncol=3, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    fig.tight_layout()
    fig.savefig(out / "edp_vs_runs.png")
    plt.close(fig)


def chart_break_even(results, out):
    ks = results["kernels"]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    cfgs = OURS + ("clang-O2",)
    n = len(cfgs)
    width = 0.8 / n
    ceiling = 1e8
    for i, cfg in enumerate(cfgs):
        ys, never = [], []
        for k in ks:
            be = build_of(k, cfg)["break_even_runs"]
            never.append(be is None)
            ys.append(ceiling if be is None else max(be, 1.0))
        xs = [j + (i - (n - 1) / 2) * width for j in range(len(ks))]
        bars = ax.bar(xs, ys, width, color=COLORS[cfg], label=cfg)
        for bar, nv in zip(bars, never):
            if nv:
                bar.set_hatch("///")
                bar.set_alpha(0.35)
    ax.set_yscale("log")
    ax.set_xticks(range(len(ks)))
    ax.set_xticklabels([k["name"] for k in ks], rotation=35, ha="right")
    ax.set_ylabel("executions to repay extra compile energy (log)")
    ax.set_title("Break-even run count  (hatched = optimization never repays)")
    ax.legend(ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.3))
    fig.tight_layout()
    fig.savefig(out / "break_even.png")
    plt.close(fig)


def chart_ga(results, out):
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    drawn = 0
    for k in results["kernels"]:
        ga = (build_of(k, "-Mperf") or {}).get("ga") or {}
        hist = ga.get("history") or []
        if len(hist) >= 2 and hist[0] > 0:
            ax.plot(range(len(hist)), [h / hist[0] for h in hist], marker="o",
                    ms=3, lw=1.5, label=k["name"])
            drawn += 1
    ax.set_xlabel("generation")
    ax.set_ylabel("best fitness (1/EDP) relative to generation 0")
    ax.set_title("-Mperf: genetic-algorithm search, fitness = 1 / lifecycle EDP")
    if drawn:
        ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out / "perfmode_ga.png")
    plt.close(fig)


def chart_stages(results, out):
    ks = results["kernels"]
    stages = list(STAGE_COLORS)
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    bottoms = [0.0] * len(OURS)
    for st in stages:
        vals = []
        for cfg in OURS:
            xs = [(build_of(k, cfg).get("stage_s") or {}).get(st, 0.0) for k in ks]
            vals.append(statistics.mean(xs) * 1e3)
        ax.bar(OURS, vals, bottom=bottoms, label=st, color=STAGE_COLORS[st])
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("mean compile time per kernel (ms)")
    ax.set_title("Where the compile time goes, by mode")
    ax.set_yscale("symlog", linthresh=50)
    ax.legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "compile_stages.png")
    plt.close(fig)


def fmt_runs(be):
    if be is None:
        return "never"
    if be == 0:
        return "0"
    return f"{be:,.0f}"


def write_markdown(results, out):
    ks = results["kernels"]
    m, st = results["machine"], results["settings"]
    energy_note = ("Energy columns are **RAPL package joules** (idle-corrected)."
                   if m["energy_source"] == "rapl" else
                   "**RAPL was not readable** when this run was made, so energy "
                   "is the labelled estimate `P-hat x T` "
                   f"(P-hat = {st['power_w_estimate']:.0f} W) and every claim "
                   "below rests on measured *time*.")
    L = []
    L.append("# Benchmark results\n")
    L.append(f"Generated {results['generated']} on **{m['cpu']}** "
             f"(Linux {m['kernel']}, Python {m['python']}); "
             f"{len(ks)} kernels, {st['reps']} interleaved native trials each, "
             f"lifecycle EDP at {st['n_runs']:,} executions.\n")
    L.append(energy_note + "\n")
    L.append("All objects were linked and run natively; every build below "
             "returned the manifest value "
             f"(`correct` column). Run time is the median of {st['reps']} "
             "interleaved trials of `backend/native_harness.c` calling the "
             "emitted object's `main` in a tight loop.\n")

    L.append("## Summary\n")
    L.append("| build | geomean speedup vs unopt | median EDP savings @ "
             f"{st['n_runs']:,} runs | kernels where it repays compile cost |")
    L.append("|---|---:|---:|---:|")
    for cfg in ALL:
        sp = geomean([build_of(k, cfg)["speedup_vs_unopt"] for k in ks])
        ed = statistics.median(lifecycle_savings(k, cfg, st["n_runs"]) for k in ks)
        pays = sum(1 for k in ks if build_of(k, cfg)["break_even_runs"] is not None
                   and build_of(k, cfg)["break_even_runs"] <= st["n_runs"])
        L.append(f"| {cfg} | {sp:.2f}x | {ed:+.1f}% | {pays}/{len(ks)} |")
    L.append("")

    L.append("## Per kernel\n")
    L.append("| kernel | build | correct | run (us) | speedup | compile (ms) | "
             "break-even runs | EDP savings @ 1 / 10k / 1M runs | passes |")
    L.append("|---|---|:-:|---:|---:|---:|---:|---|---|")
    for k in ks:
        for cfg in ("unopt",) + ALL:
            b = build_of(k, cfg)
            e = b["edp_savings_pct"]
            L.append(
                f"| {k['name']} | {cfg} | {'yes' if b['correct'] else '**NO**'} | "
                f"{b['run_s'] * 1e6:.2f} | {b['speedup_vs_unopt']:.2f}x | "
                f"{b['compile_s'] * 1e3:.1f} | {fmt_runs(b['break_even_runs'])} | "
                f"{e['1']:+.0f}% / {e['10000']:+.0f}% / {e['1000000']:+.0f}% | "
                f"{' '.join(b.get('passes') or []) or '-'} |")
    L.append("")
    L.append("## Charts\n")
    for name, cap in (
            ("runtime_speedup", "Native speedup of each object file"),
            ("compile_time", "Compile time per build"),
            ("edp_vs_runs", "Lifecycle EDP vs run count"),
            ("break_even", "Break-even run counts"),
            ("perfmode_ga", "-Mperf GA convergence"),
            ("compile_stages", "Compile-time breakdown by stage")):
        L.append(f"### {cap}\n\n![{cap}]({name}.png)\n")
    L.append("## Notes on validity\n")
    L.append("- The compiler under test is written in Python (llvmlite) and the "
             "clang rows are a native C++ compiler: absolute compile times are "
             "not comparable between them, only the *shape* (what the energy-aware "
             "modes spend and what they buy) is.")
    L.append("- `unopt` is the unified front-end with no optional passes; it is the "
             "baseline for speedup, EDP savings and break-even.")
    L.append("- `-Mperf` pays for its genetic-algorithm search in `compile (ms)`; "
             "it breaks even only for programs that run many times.")
    L.append("- clang rows compile float-suffixed source so MiniC's `float` "
             "literals mean the same thing in C.")
    (out / "RESULTS.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=ROOT / "reports" / "benchmarks")
    args = ap.parse_args()
    results = json.loads((args.dir / "results.json").read_text())
    for fn in (chart_runtime, chart_compile, chart_edp, chart_break_even,
               chart_ga, chart_stages):
        fn(results, args.dir)
    write_markdown(results, args.dir)
    print(f"wrote charts and RESULTS.md to {args.dir}")


if __name__ == "__main__":
    main()

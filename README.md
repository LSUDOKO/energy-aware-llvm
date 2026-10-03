# Energy-Aware Compiler Optimization Lab

This project implements the **Energy-Aware Compiler Optimization** framework from `compiler.pdf` and the architecture diagram: a fused single-pass front-end that minimizes compile energy (`E_compile`), a static IR feature extractor with energy-aware pass gating, and multi-mode pass scheduling (`-Meco`, `-Mbalanced`, `-Mperf`) that minimizes runtime energy (`E_run` and EDP).

See **[PLAN.md](PLAN.md)** for the full analysis, gap list, and phased roadmap. Phases 0–1 (environment + energy measurement harness) are implemented; the unified semantic visitor (Phase 2) is next.

## Project Structure

```
frontend/                  Stage 1: fused front-end (MiniC subset of C)
  lexer.py                   regex lexer with line/column tracking
  ast_nodes.py               AST node definitions
  parser.py                  recursive-descent parser
  codegen.py                 llvmlite IR emission
  semantic_checker.py        standalone check-only walk (baseline pipeline stage)

stage2_extractor.py        Stage 2: static IR feature extraction (early version)
stage3_ml_model.py         Stage 3: XGBoost pass ranker (early, synthetic-data version)
compiler_driver.py         CLI driver wiring the three stages + mode flags
api.py                     Flask API used by the web app
web-app/                   React + Vite UI (editor, mode buttons, results)

energy/                    Phase 0–1: measurement infrastructure
  rapl_reader.py             RAPL counter access (direct/sudo/unavailable)
  setup_rapl_access.sh       one-time sudo rule to grant counter read access
  harness.py                 idle power, warm-up, interleaved paired trials,
                             idle-corrected energy deltas, wrap handling
  stats.py                   median/IQR/mean, bootstrap CI, savings, schedules
  report.py                  JSON + Markdown reports with methodology notes
  execution.py               MCJIT execution of emitted IR + object emission
  experiments.py             conventional-vs-merged front-end experiment CLI

tests/                     pytest suite (33 tests: stats, harness, pipelines, JIT)
reports/                   experiment outputs (JSON + Markdown)
```

## Prerequisites

- Linux (tested on AMD Ryzen; any x86-64 works), Python 3.12+
- A virtual environment with the pinned dependencies:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Enabling real energy measurements (RAPL)

Energy counters under `/sys/class/powercap` are root-owned. To let the
harness read them without a password (least-privilege sudo rule, survives
reboots):

```bash
sudo energy/setup_rapl_access.sh        # grant
sudo energy/setup_rapl_access.sh revert # revoke
```

Without this, everything still runs in **time-only mode** (energy columns
reported as `n/a`). Note: on AMD platforms the `intel-rapl` driver exposes
**package energy only** (no DRAM domain); reports state this limitation.

## Running the pipeline

```bash
source venv/bin/activate

python compiler_driver.py test_program.c -Meco        # minimal compile overhead
python compiler_driver.py test_program.c -Mbalanced   # ML pass ranker (default)
python compiler_driver.py test_program.c -Mperf       # GA search (mock in current code)

python api.py                                          # Flask backend on :5000
cd web-app && npm install && npm run dev              # UI on :5173
```

## Running the energy experiment (Phase 1 deliverable)

Compiles the same program through the **conventional** pipeline
(parse → separate check walk → emit walk) and the **merged** pipeline,
interleaved, with idle-corrected joules when RAPL is available:

```bash
# default: 20 interleaved trials x 25 compilations per trial
python energy/experiments.py test_program.c

# quick smoke run
python energy/experiments.py test_program.c --trials 5 --batch 40 --label smoke
```

Outputs `reports/<label>.json` and `reports/<label>.md` containing
median/IQR/mean, bootstrap 95% CI, savings vs baseline, peak RSS, and a
differential-correctness check (both pipelines JIT-run to the same result).

## Tests

```bash
./venv/bin/python -m pytest tests/ -v
```

Covers: RAPL wrap-around math, idle correction, interleaving balance,
time-only degradation, batching, pipeline semantics, JIT differential
execution, and object-file emission.

## Methodology (per `compiler.pdf`, Part I)

1. Fixed machine/software settings; AC power; warm-up run.
2. Hardware counters (RAPL) with explicit fallback reporting.
3. Idle power `P_idle` measured, subtracted per trial: `E = E_raw − P_idle·T`.
4. Counter wrap handled via `max_energy_range_uj`.
5. **≥20 interleaved paired trials**; balanced rotation cancels drift bias.
6. Median + IQR as primary summary; bootstrap 95% CI.
7. Savings `(E_base_med − E_merged_med)/E_base_med × 100`; time and peak memory reported alongside.
8. Output-quality gate: differential JIT execution of both pipelines must match.

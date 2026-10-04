# Measurement methodology

How the numbers in `reports/` are produced, and what they can and cannot show.

## What is measured

| quantity | how | where |
|---|---|---|
| compile time per stage | `perf_counter` around front-end, Stage 2, search, gate, passes, code generation | `compile_pipeline.py` |
| compile energy per stage | RAPL package joules, idle-corrected, batched until the window is resolvable; otherwise `P-hat x T` labelled *estimated* | `energy/meter.py` |
| native runtime | the compiler's own object file, linked with `backend/native_harness.c`, N calls per process, no JIT or Python in the loop | `backend/native.py` |
| correctness | exit status of the linked executable and the timing binary's return value must equal the manifest value | `benchmarks/run_benchmarks.py` |
| object size | file size and `.text` bytes | `benchmarks/run_benchmarks.py` |

Expected values in `benchmarks/manifest.json` come from
`benchmarks/reference.py`, independent implementations that use no compiler
code (float kernels use binary32 arithmetic in the same operation order). The
test suite checks the compiler against them in every pipeline and mode.

## Builds compared

- `unopt` - unified front-end, no optional passes. The baseline for speedup,
  EDP savings and break-even.
- `-Meco`, `-Mbalanced`, `-Mperf` - the three modes, each ending in the same
  cost-benefit gate.
- `clang-O0`, `clang-O2` - the system clang on the same kernel. MiniC float
  literals are `float`, so the C source is rewritten with `f` suffixes first
  (`as_single_precision_c`); otherwise clang computes a different program.

## Energy-delay accounting

A program is compiled once and executed `n` times (`energy/lifecycle.py`):

```
E(n) = E_compile + n E_run      T(n) = T_compile + n T_run      EDP(n) = E(n) T(n)
break-even n = (E_compile_opt - E_compile_base) / (E_run_base - E_run_opt)
```

Each build is charged for its own machine-code generation (optimized IR is
cheaper to lower), and `-Mperf` is charged for its genetic search. The
reported run count is a parameter (`--runs`, default 10,000); the curve over
all run counts is in `reports/benchmarks/edp_vs_runs.png` and in the web
Ledger.

## Statistical treatment

- Native trials are **interleaved** across builds with a rotating order each
  round, then summarized by the **median**.
- Each timed batch lasts about 0.2 s so a RAPL counter that updates every
  millisecond resolves it; idle power is measured once and subtracted.
- `energy/harness.py` implements the plan's >= 20 paired trials with
  median/IQR/mean and bootstrap confidence intervals; the benchmark runner uses
  fewer (7) by default to keep a full suite near a minute. Raise `--reps` for
  tighter intervals.

## Learned and searched components

- **`-Mbalanced`** is an XGBoost regressor over the 52 Stage-2 metrics and
  pass flags. Its label is lifecycle-EDP savings recomputed from measured
  compile and run times. It is evaluated leave-one-benchmark-out
  (`ml_models/evaluate.py`), not on its training set, against the oracle and
  every fixed pass list. If nothing is predicted to help it runs no passes.
- **`-Mperf`** is a genetic algorithm over subsets of the atomic passes. Each
  candidate is actually run and timed natively; a candidate whose `main()`
  differs from the unoptimized module is invalid. It optimizes for a hot
  program (10^7 executions); the finalists are re-timed with a larger sample
  before one is declared the winner.

## Validity threats

- **No RAPL in the committed run.** Energy there is `P-hat x T` with
  `P-hat = 25 W`, so energy columns are proportional to time. This hides any
  disagreement between energy and time (frequency effects, memory-bound code).
- **Microsecond kernels.** Several kernels run in under a microsecond; their
  speedups are within noise. They still matter for the break-even analysis,
  where "never repays" is the correct answer.
- **Python compiler vs C++ clang.** Compare shapes, not absolute compile times.
- **One machine, 11 kernels.** The ranker's leave-one-out result is
  indicative, not a general claim.
- **Thermal and frequency drift** are handled by interleaving, not eliminated;
  fix the governor and use AC power for publishable numbers.

## Numeric semantics

Constant folding must reproduce what the generated code computes. `int` wraps
modulo 2^32, `float` is binary32, NaN compares false except `!=`, and
`INT_MIN / -1` is never folded. `frontend/numeric.py` defines these once;
`tests/test_numeric_folding.py` checks folded results against both the
unfolded conventional pipeline and hand-derived C results.

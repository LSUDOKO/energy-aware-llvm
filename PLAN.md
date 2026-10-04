# Energy-Aware Compiler Optimization — Analysis & Implementation Plan

> **Status (2026-10-04): Phases 0-6 are implemented.** Gap list G1-G13 is closed
> except where noted in section 7. Results: `reports/benchmarks/RESULTS.md`,
> `reports/ml/loo_evaluation.md`; method: `docs/METHODOLOGY.md`. The sections
> below keep the original analysis; section 7 records what changed on the way.

*Generated 2026-10-03 from `compiler.pdf` (project plan), `Architecture of the Project.jpeg` (OCR), and full codebase inspection.*

---

## 1. What the Requirements Say

### 1.1 From `compiler.pdf` (formal project plan)

**Objectives**
- **Primary:** minimize *compilation energy* `E_compile` (joules to translate source → object file) while preserving correctness, diagnostics, and output quality.
- **Secondary:** verify reduced compile work does not increase *runtime energy* `E_run` of the compiled program.
- Two measured quantities, always reported separately: `E_compile` and `E_run`.

**Part I — Energy measurement methodology (9 steps)**
1. Fix machine/OS/compiler settings, AC power, stable temperature.
2. Prefer hardware counters (Intel RAPL package/DRAM); fallback external power meter; software estimate only with stated limitations.
3. Measure idle power `P_idle` over `T_idle` seconds.
4. Warm-up run; fix warm/cold cache policy.
5. Record counter deltas per trial: `E_raw = E_after − E_before`, `T = t_after − t_before` (handle counter wrap).
6. Idle correction: `E_compile = E_raw − P_idle × T` (keep raw too; batch short runs).
7. **≥20 interleaved paired trials** per config; report median, IQR, mean, CI.
8. Savings: `Saving% = (E_baseline_med − E_merged_med)/E_baseline_med × 100`; also speedup `T_base/T_merged` and peak memory.
9. Validate output quality: same tests, diagnostics, size, runtime, `E_run`. Accept saving only if correctness unchanged and runtime within tolerance.

**Part II — Merging compiler stages (8 steps)**
1. Common **typed IR**: every expression node has type, value category, source location, optional constant value; SSA-friendly; specified invariants + error recovery.
2. **Semantic environment**: nested symbol tables, scopes, type compatibility, implicit conversions, function lookup, control-flow legality; intern canonical types/identifiers.
3. **Combined check+emit**: one traversal — `lower(AST node, context) → (type, IR value, effects)`; resolve, diagnose, convert, and emit in one visit.
4. **Optimize during emission**: constant folding, algebraic simplification, local constant propagation, dead temp elimination, redundant cast removal, strength reduction, unreachable branch removal — via a simplifying IR builder.
5. **Effect tracking**: reads/writes/volatile/atomic/aliasing flags to prevent unsafe reordering.
6. **Incremental CFG/SSA**: form blocks during lowering, seal blocks, insert/simplify φ-nodes, discard unreachable blocks.
7. **Small late optimizer**: only cross-function/whole-CFG analyses (GVN, loop opts, vectorization, regalloc), measured for benefit vs. energy cost.
8. **Energy-aware pass policy**: run optional pass `p` only when
   `B̂_p > λ·Ê_p + µ·T̂_p` (predicted benefit vs. predicted energy & time; λ,µ encode the compilation budget).

**Modes (3)** — same correctness in all, only optional optimizations differ:
- `eco` — lowest `E_compile`
- `balanced` — default trade-off (default mode)
- `performance` — maximum generated-program speed

**Success criteria**
- Pass semantic/diagnostic/IR/execution regression suites.
- ≥10% median front-end energy reduction on target benchmarks.
- Reduce or preserve wall time and peak memory.
- Balanced mode: runtime, `E_run`, size within 2% of baseline.
- Statistically stable interleaved measurements.

**Deliverables**: baseline + merged pipeline, energy measurement harness, per-stage/per-pass energy/time/memory profiles, regression + benchmark suite, final evaluation.

### 1.2 From the architecture diagram (OCR)

```
SYSTEM INPUTS & ENVIRONMENT SETUP          HARDWARE EVALUATION
  C/C++ Source Code                        Intel RAPL Measurement Daemon
  Benchmarks (PolyBench, MiBench)          Paired Interleaved Trials (idle subtraction)
  Hardware setup: fixed CPU freq,          Differential Testing
  isolated cores, idle power P_idle,
  calibration
                    │
                    ▼
STAGE 1: FUSED SINGLE-PASS FRONT-END  (minimizes E_compile)
  AST Parser (Arena Allocation) → Unified Semantic Visitor
  Name/Scope Resolution → Type Check & Coercion → Emit Diagnostic/Recover
  Compact SSA LLVM IR → Incremental CFG & SSA construction
  Online Local Optimization (constant folding, algebraic simplification)
                    │
                    ▼
STAGE 2: STATIC IR FEATURE EXTRACTION & DYNAMIC PASS GATING
  35+ IR Metrics Extractor  →  E_compile/T_compile → Predicted EDP Benefit
  Cost-Benefit Decision Rule: B̂ > λ·Ê + µ·T̂  →  Optional Pass: Yes(Schedule)/No(Skip)
                    │
                    ▼
STAGE 3: MULTI-MODE PASS SCHEDULING & BACKEND (minimizes E_run & EDP)
  1: Gated Pass Sequence → -Meco  (Minimal Compile Overhead) → Backend Code Gen → Object File
  2: Gated Pass Sequence → ML Model (XGBoost/Neural) → -Mbalanced → ML Pass Ranker in 50ms
     → Optimized Pass Sequence → Backend Code Gen → Object File
  3: Gated Pass Sequence → -Mperf → Genetic Algorithm Search Loop (optimizing 1/EDP)
     → Select Top Sequences → Backend Code Gen → Object File

FINAL OUTCOMES
  minimized E_compile (median ~10%↓) · optimized E_run & EDP · % EDP savings
  · quantified EDP vs compile cost · labeled dataset
```

---

## 2. Current State Assessment

### What exists (working quality)

| Component | State | Notes |
|---|---|---|
| `frontend/lexer.py` | ✅ Works | Regex lexer; int/float, if/else/while, arithmetic + comparisons. No `-`, `!`, unary ops, `for`, `break/continue`, comments `/* */`, strings/chars, `&&`/`\|\|`, `+=` etc. |
| `frontend/parser.py` | ✅ Works | Recursive descent; `Program→FunctionDecl→Block→stmts`. Params TODO'd out. Assignment-only statements. |
| `frontend/ast_nodes.py` | ✅ Works | 11 node classes. **No types, no source locations, no const values** — violates Stage-1 Step-1 requirement of the plan. |
| `frontend/codegen.py` | ✅ Works | llvmlite IR emission, if/while lowering, symbol table. **Two separate traversals effectively (parse, then emit)** — no unified semantic visitor, no effect tracking, no online folding (llvmlite does none before opt passes). Division by zero, undeclared vars raise but diagnostics have no recovery, no param support (`fnty = FunctionType(ret, [])`). |
| `stage2_extractor.py` | ⚠️ Partial | Counts ~13 metrics via **string parsing of printed IR** — brittle (`elif` chain mislabels ops, misses `zext/phi/fp ops`, "35+" claim is false today). |
| `stage3_ml_model.py` | ⚠️ Mock | XGBoost trained on **synthetic random data with invented labels** — not measurements. `rank_passes` scores 5 fixed candidate sequences. |
| `compiler_driver.py` | ⚠️ Mock | `-Mperf` literally `time.sleep(1.5)` then a fixed pass list; backend is "simulated a.out". `-Meco/-Mbalanced/-Mperf` flags exist. |
| `api.py` | ✅ Works | Flask + CORS `/compile` endpoint, mode plumbing, timing. Same mocks inside. |
| `web-app/` | ✅ Works | React 19 + Vite editor, 3 mode buttons, metrics, logs, LLVM IR not shown (API returns it, UI ignores it). **Bug:** `RefreshCw` imported *after* JSX uses it (works due to hoisting but fragile). |
| `CMakeLists.txt` + `src/Passes/IRMetricsExtractorPass.cpp` | ⚠️ Dead | Legacy-PM FunctionPass skeleton, LLVM `find_package` commented out — won't build. Superseded by Python pipeline. |
| `ml_models/train_xgboost.py` | ⚠️ Dead | Alternate 4-feature skeleton, unused. |
| `Compiler Lab Research PDF` | ❌ Empty | 0 bytes on disk. |
| Tests | ❌ None | No regression suite at all (plan requires differential testing). |

### Environment findings (this Linux machine, AMD Ryzen 7 5700U)

- **RAPL present but root-gated**: `/sys/class/powercap/intel-rapl:0/energy_uj` exists (package-0) → *Permission denied* as user. Note: this is an **AMD** chip; the `intel-rapl` driver exposes package energy, no DRAM domain visible. Plan's "Intel RAPL" requirement maps to reading this node via `sudo` or a setcap helper.
- `clang` installed; no `opt`/`llvm-config` on PATH.
- System Python is **3.14**; `llvmlite`/`xgboost`/`pandas` **were not installed**; Flask already present. PEP 668 blocks `pip install --user` → project `venv/` now exists (created in Phase 0) with llvmlite 0.50, xgboost 3.4.1, pandas 3.0.6, pytest installed and verified.
- `.gitignore` was committed **UTF-16LE with BOM** (created in Windows Notepad) — git likely does not honor it. Must be re-encoded ASCII/UTF-8.
- Repo currently tracks `__pycache__/*.pyc` (junk committed).
- Disk: 11 GB free on `/` — sufficient for venv + toolchain.
- No `ml_models/` or `src/` on disk (deleted in a past commit) — only in the old staged state.

---

## 3. Gap Analysis → Requirements

| # | Requirement (plan/diagram) | Today | Gap severity |
|---|---|---|---|
| G1 | Real energy measurement harness: RAPL reading, idle subtraction, ≥20 interleaved trials, median/IQR/CI report | **Nothing** | 🔴 Core deliverable |
| G2 | Unified semantic visitor: one traversal, typed IR, source locations, effects | Parse + 2nd emit pass, untyped | 🔴 Core architecture |
| G3 | Online local optimization during emission (folding, algebraic simplification) | None before opt passes | 🔴 Core claim of the research |
| G4 | Effect tracking + incremental CFG/SSA control | Basic if/while lowering only | 🟡 Needed for safe local opts |
| G5 | True `35+` IR metrics extractor (typed, robust) | 13 metrics via string split | 🟡 Accuracy of Stage 2 |
| G6 | Energy-aware pass gating `B̂ > λ·Ê + µ·T̂` | Gating is a print statement | 🔴 Decision rule unimplemented |
| G7 | `-Mperf` real GA search loop optimizing 1/EDP | `sleep(1.5)` + fixed list | 🔴 Honest implementation needed |
| G8 | ML model trained on **measured** (features → pass-seq → EDP) data | Synthetic invented labels | 🔴 Scientific validity |
| G9 | Real backend: emit object file / run via `lli`/`clang`, measure `E_run` & runtime | "simulated a.out" | 🔴 Needed for E_run & correctness validation |
| G10 | Regression/differential test suite | None | 🔴 Success criterion |
| G11 | Benchmarks (PolyBench/MiBench subset) | One 12-line `test_program.c` | 🟡 Benchmark harness input |
| G12 | Environment reproducibility (venv, requirements, .gitignore fixed, no committed pyc) | Broken .gitignore, no venv | 🟡 Hygiene |
| G13 | Web UI: show LLVM IR + energy results | IR hidden | 🟢 Cosmetic |

---

## 4. Implementation Plan (phased, per plan's schedule)

### Phase 0 — Reproducible environment (half day) — **DONE 2026-10-04**
1. Recreate `.gitignore` as UTF-8 (venv/, __pycache__/, *.pyc, build/, *.o, .venv/); `git rm -r --cached __pycache__ frontend/__pycache__`.
2. Create `venv/` (Python 3.14) + `requirements.txt` (llvmlite, xgboost, pandas, numpy, scikit-learn, joblib, flask, flask-cors, matplotlib for report plots).
3. `energy/rapl_reader.py`: root-gated RAPL reader — try direct read; if EACCES, use a tiny `sudo`-installed setcap helper (`energy/rapl_helper.c` compiled with `setcap cap_dac_override+ep`) OR fallback documented "sudo cat" mode; expose `read_package_energy_uj()`. Detect AMD vs Intel domains; record DRAM if present.
4. Update README to match reality (Linux + Windows instructions).

### Phase 1 — Energy measurement harness (G1) (1–2 days) — **DONE 2026-10-04**
5. `energy/harness.py` implementing the 9 steps verbatim:
   - idle power sampling, warm-up policy flag, counter wrap handling,
   - batched short runs, **interleaved paired trials (n≥20)**,
   - median/IQR/mean/95% CI via bootstrap,
   - JSON + Markdown report output (`reports/`).
6. `energy/experiments.py`: runner comparing **baseline = separate-stage pipeline** (parse → check-only walk → emit walk) vs **merged = unified visitor** on the same inputs, producing front-end-only and end-to-end numbers, speedup, peak RSS (`resource.getrusage`), and E_run via running compiled programs.
7. Unit-test harness math with fake energy counters (wrap-around, idle subtraction, CI).

### Phase 2 — Unified semantic front-end (G2, G3, G4) (3–5 days) — **DONE 2026-10-04**

> Delivered: typed AST slots (loc/type/const_value/value_category), `UnaryOp` and `CallExpr`, function params + forward calls, `!=`/`<=`/`>=`, `/* */` comments, `SimplifyingIRBuilder` (folding, algebraic identities, strength reduction, lazy casts), `UnifiedSemanticVisitor` (single traversal, scoped symbol table with constant facts + conservative loop/branch invalidation, pure const evaluator for pruning, effects on TypedValue, statement-level diagnostics with recovery, unreachable-code elimination). Deferred to a later increment: `&&`/`||` (short-circuit), `for`, `break`/`continue`. Result on constant-heavy code: **33 → 18 IR instructions (−45.5%)** vs conventional, differential JIT check PASS.
8. **Typed AST** (`frontend/ast_nodes.py` v2): add `type`, `value_category`, `loc(line,col)`, `const_value` to expression nodes; add `UnaryOp`, function params/calls, `for`, `break/continue` to the language (brings it in line with "clearly documented C subset").
9. **Lexer/parser upgrades**: unary minus/`!`, `&&/||`, params, calls, comments `/* */`; keep diagnostics with locations.
10. **`frontend/semantic_codegen.py` — the Unified Semantic Visitor**: single traversal implementing
    `lower(node, env) → TypedValue(type, ir_value, effects, const_value)`
    - nested scope stack, type checking + implicit `int↔float` coercions,
    - **effect flags** (reads/writes/calls/volatile) per statement,
    - error recovery (record diagnostic, continue at statement boundaries),
    - arena-allocated AST + interned type objects.
11. **Simplifying IR builder** (`frontend/ir_builder.py` wrapping llvmlite): constant folding, algebraic identities (`x*1`, `x+0`, `x*0`, double negation), local const-prop within scope, strength reduction (`x*2^n → shl`), dead-temp elision — applied *before* allocating an instruction; count every eliminated instruction (this number is a paper metric).
12. **Incremental CFG/SSA control**: maintain current block, seal-on-demand, prune unreachable blocks after constant conditions (e.g., `while(1)`), and keep the block/branch counters for Stage 2.
13. **Baseline pipeline kept intact** (`frontend/legacy_pipeline.py`) for differential + energy comparison — the "conventional pipeline" of Part II.

### Phase 3 — Stage 2: real metrics + pass gating (G5, G6) (2–3 days)
14. Rewrite `stage2_extractor.py` on llvmlite `binding` (parse IR with `binding.parse_assembly` and walk functions/blocks/instructions via the C API instead of string splitting): ≥35 typed metrics — instruction histogram (all opcodes), block counts, loop depth via dominance info, branch probability hints, memory op classes, PHI count, per-function metrics, E_compile/T_compile inputs.
15. Implement the **cost-benefit gate** `stage2/pass_gating.py`: per optional pass, `B̂_p` from the ML model; `Ê_p, T̂_p` from measured per-pass energy/time profiles (Phase 4); user budget via `--budget=eco|balanced|perf` mapping to (λ,µ). Output: schedule/skip decision per pass with reasons (this becomes the auditable "why was this pass run" log).

### Phase 4 — Stage 3: honest scheduling modes (G7, G8, G9) (3–5 days)
16. **Real backend** (`backend/`): use llvmlite ORC/JIT `binding` to compile IR → machine code → object file; `lli`/direct JIT execution for E_run + correctness; `clang` fallback with measured `-O0..-O3` for baseline object comparison.
17. **Measured dataset builder** (`ml_models/collect_dataset.py`): for each benchmark × candidate pass-sequence: extract features (Stage 2), run sequence, measure E_compile, T_compile, E_run, runtime → **labels = measured EDP** = `E_compile + k·E_run` scaled by delay product; store `datasets/` as CSV/parquet. (RAPL gates this to the lab machine — code supports `--mock` replay for development.)
18. **`-Mbalanced`**: retrain XGBoost on measured data (`ml_models/train.py`); keep the <50ms candidate-scoring ranker but on the measured feature space; report predicted vs measured EDP for the chosen sequence.
19. **`-Mperf`**: real GA (`ml_models/ga.py`) — population of pass sequences, fitness = 1/EDP (predicted during search, measured for the winner), crossover/mutation over `AVAILABLE_PASSES`, time-budgeted; no sleeps.
20. **`-Meco`**: runs gated pipeline with all optional passes skipped (already trivially correct; wire through the new gate for consistency + logging).
21. Wire all modes through `compiler_driver.py` + `api.py` with real numbers (replace simulated a.out, sleep mocks); keep API response schema backward-compatible, add `llvm_ir` display + EDP breakdown in web UI (G13).

### Phase 5 — Validation & benchmarks (G10, G11) (2–3 days)
22. **Regression suite** (`tests/`): pytest for lexer/parser/semantic (diagnostics, recovery), golden IR tests, differential tests legacy-vs-unified on `tests/cases/*.c` (same runtime results via JIT), harness math tests. CI-able.
23. **Benchmark set**: vendor 8–12 small PolyBench/MiBench kernels translated into the MiniC subset under `benchmarks/` with a manifest (name, workload args, expected output hash).
24. **Run the full experiment** (needs RAPL sudo on the lab machine): 20+ interleaved trials baseline vs merged, produce `reports/energy_report.md` — median savings %, speedup, peak memory, E_run delta, per-pass joules table. This is the thesis-grade deliverable.

### Phase 6 — Cleanup & docs (1 day)
25. Delete/replace dead code: `CMakeLists.txt`, `src/Passes/IRMetricsExtractorPass.cpp`, `ml_models/train_xgboost.py` (or port the C++ pass as optional LLVM plugin appendix — decision point below).
26. README rewrite: architecture, modes, measurements, reproduction steps; diagram regenerated to match final flow.
27. Final self-check against plan's success criteria checklist.

---

## 5. Decision Points (need your call)

| # | Decision | Options |
|---|---|---|
| D1 | **Backend execution** — JIT via llvmlite only, or also emit real `.o` files via clang? | (a) JIT-only, simplest; (b) both, better paper story |
| D2 | **RAPL access** — can you run with `sudo` on this machine for real measurements? | (a) yes → real E_compile now; (b) no → mock-replay mode until lab machine |
| D3 | **Legacy C++ pass** (`src/Passes/`, CMakeLists) | (a) delete (recommended — superseded); (b) restore as a building LLVM plugin |
| D4 | **Language subset growth** — add params/calls/for/unary now (bigger benchmarks need them) or freeze subset? | (a) grow (recommended); (b) freeze |
| D5 | **Dataset** — start collecting measured data on this AMD machine (package energy only, no DRAM domain) or wait for Intel lab hardware? | (a) start here; (b) wait |

**My recommendation:** D1=(b), D2=(a), D3=(a), D4=(a), D5=(a).

---

## 6. Suggested Work Order (first commits)

1. Phase 0 (env + .gitignore + venv + RAPL reader) — unblocks everything.
2. Phase 1 harness with unit tests — the measuring stick.
3. Phase 2 unified visitor + simplifying builder — the research core.
4. Phase 3 gating — makes Stage 2 real.
5. Phases 4–6 as above.

Estimated total: ~12–19 focused working days, matching the plan's 12-week schedule compressed for solo dev at prototype scope.


---

## 7. Outcome and deviations (added 2026-10-04)

| Plan item | Outcome |
|---|---|
| G1 harness | `energy/` (RAPL reader, harness, meter, stats, report); committed results were produced **without RAPL** (counters root-only), so energy columns are labelled `P-hat x T` estimates. `energy/setup_rapl_access.sh direct` enables real joules. |
| G2-G4 unified visitor | Done (Phase 2) plus numeric-semantics fixes: i32 wraparound, binary32 folding, NaN comparisons (`frontend/numeric.py`). Still deferred: `for`, `break/continue`, `&&/||`. |
| G5-G6 metrics and gate | 52 metrics; `stage2/pass_gating.py` with auditable run/skip reasons. |
| G7 `-Mperf` | Real GA; fitness is now lifecycle EDP from measured pass time and native runtime, candidates that change `main()` are invalid, finalists are re-timed. |
| G8 measured ML | Dataset measured on this machine (`datasets/measurements.csv`, 242 rows); labels are lifecycle-EDP savings; leave-one-out evaluation added (ranker +14.1% vs best fixed list +14.9%). |
| G9 real backend | LLVM new-PM passes, `object_bytes` code generation, native link/run and a C timing harness. |
| G10-G11 tests and benchmarks | 264 tests; 11 kernels with independent references; benchmark runner and report. Writing the manifest test exposed a wrong constant in `const_fold` (fixed). |
| G13 UI | Rebuilt: ledger with run-count slider, gate table, IR before/after, measured benchmarks. |

Decisions taken: D1 = both JIT and real objects, D3 = delete the C++ skeleton,
D4 = grow the language only as far as the kernels need, D5 = collect data on
this AMD machine (package energy only).

### Change of objective

The original EDP definition `E_compile x T_run` multiplied a one-off cost by a
single run's delay, which made every optimization look bad. All components now
use the compile-once, run-`n`-times lifecycle EDP and report the break-even
run count (`energy/lifecycle.py`).

### Open items

- Re-run `benchmarks/run_benchmarks.py` with RAPL readable to replace the
  estimated energy columns.
- `for`, `break/continue`, `&&/||`, arrays; larger PolyBench/MiBench kernels
  that run for milliseconds rather than microseconds.
- More training programs for the `-Mbalanced` ranker.

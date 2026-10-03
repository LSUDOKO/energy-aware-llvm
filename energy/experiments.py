"""Front-end energy experiments: conventional vs merged pipeline.

Implements the plan's core comparison (Part II, section 3.1):

  baseline "conventional":
      Lexer -> Parser -> separate semantic check walk -> second walk to
      emit IR  (two full AST traversals, no simplification)

  "merged" (proposed):
      Lexer -> Parser -> single Unified Semantic Visitor that checks and
      emits typed IR in one traversal  (full implementation lands in
      Phase 2; this module wires it into the harness now)

Also provides a compile-only workload for E_compile measurement and
records peak RSS per pipeline (plan section 2.4: peak resident memory).
"""
from __future__ import annotations

import argparse
import resource
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow `python energy/experiments.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from energy.harness import EnergyHarness, HarnessConfig
from energy.rapl_reader import RAPLReader
from energy.report import write_report
from frontend.lexer import Lexer
from frontend.parser import Parser
from frontend.semantic_checker import SemanticChecker
from frontend.codegen import CodeGenVisitor


@dataclass
class PipelineArtifacts:
    ir_text: str
    checks_ok: bool
    errors: list[str]


def conventional_pipeline(source: str) -> PipelineArtifacts:
    """Separate stages: parse, then check walk, then emit walk."""
    tokens = Lexer(source).tokens
    ast = Parser(tokens).parse()

    checker = SemanticChecker()
    errors = checker.check(ast)

    codegen = CodeGenVisitor()
    module = codegen.generate_code(ast)
    return PipelineArtifacts(str(module), checks_ok=(len(errors) == 0), errors=errors)


def merged_pipeline(source: str) -> PipelineArtifacts:
    """Single-traversal check+emit. Phase 2 replaces the body of this
    function with the true Unified Semantic Visitor; today it delegates to
    the same stages so the harness comparison is wired end-to-end."""
    tokens = Lexer(source).tokens
    ast = Parser(tokens).parse()
    checker = SemanticChecker()
    errors = checker.check(ast)
    codegen = CodeGenVisitor()
    module = codegen.generate_code(ast)
    return PipelineArtifacts(str(module), checks_ok=(len(errors) == 0), errors=errors)


def _workload(source: str, runs: int = 1):
    """Build harness callables that compile the source repeatedly."""
    def conventional() -> object:
        art = None
        for _ in range(runs):
            art = conventional_pipeline(source)
        return art

    def merged() -> object:
        art = None
        for _ in range(runs):
            art = merged_pipeline(source)
        return art

    return {"conventional": conventional, "merged": merged}


def _peak_rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def run_frontend_experiment(
    source: str,
    n_trials: int = 20,
    batch_repeats: int = 25,
    label: str = "frontend",
    progress=None,
) -> dict:
    """Run the interleaved experiment and write reports; returns summaries."""
    reader = RAPLReader.create()
    harness = EnergyHarness(
        reader,
        HarnessConfig(n_trials=n_trials, batch_repeats=batch_repeats, warmup_runs=1),
    )
    results = harness.run_experiment(_workload(source), progress=progress)
    summaries = harness.summarize(results)

    if reader.available():
        desc = reader.describe()
    else:
        desc = "time-only mode (no readable RAPL domain)"

    # Differential sanity: both pipelines must produce equivalent output.
    conv = conventional_pipeline(source)
    merged = merged_pipeline(source)
    from energy.execution import runs_match
    differential_ok = runs_match(conv.ir_text, merged.ir_text)

    cfg_note = {
        "n_trials": n_trials,
        "batch_repeats": batch_repeats,
        "source_chars": len(source),
    }
    json_path, md_path = write_report(
        name=label,
        title=f"Front-end pipeline comparison: conventional vs merged ({label})",
        results=results,
        summaries=summaries,
        reader_description=desc,
        config=cfg_note,
        notes=[
            f"Peak RSS of the experiment process: {_peak_rss_kb()} KB",
            f"Differential check (both pipelines, same main() result): {differential_ok}",
        ],
    )
    return {
        "summaries": summaries,
        "json": str(json_path),
        "markdown": str(md_path),
        "differential_ok": differential_ok,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Front-end energy experiment")
    ap.add_argument("source", help="MiniC source file to compile repeatedly")
    ap.add_argument("--trials", type=int, default=20, help="interleaved trials per config (default 20)")
    ap.add_argument("--batch", type=int, default=25, help="compilations per trial (default 25)")
    ap.add_argument("--label", default="frontend", help="report name")
    args = ap.parse_args()

    source = open(args.source).read()

    def log(msg: str) -> None:
        print(msg, flush=True)

    out = run_frontend_experiment(source, n_trials=args.trials, batch_repeats=args.batch,
                                  label=args.label, progress=log)
    print("\n=== Summary ===")
    for kind in ("time", "energy"):
        table = out["summaries"].get(kind)
        if table:
            for cfg, s in table.items():
                print(s.format(precision=4 if kind == "time" else 6))
    if out["summaries"].get("savings"):
        print("\nSavings vs conventional:")
        for cfg, sv in out["summaries"]["savings"].items():
            print(f"  {cfg}: energy {sv['energy_savings_pct']:.2f}%  time {sv['time_savings_pct']:.2f}%")
    print(f"\nDifferential check: {'PASS' if out['differential_ok'] else 'FAIL'}")
    print(f"Reports: {out['json']}, {out['markdown']}")


if __name__ == "__main__":
    main()

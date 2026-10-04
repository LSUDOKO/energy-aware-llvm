"""Energy-Aware Compiler Optimization — command-line driver.

Three scheduling modes, all real (no simulated backend, no sleeps):

  -Meco       strict energy budget: gate skips optional passes whose
              predicted benefit does not repay λ·Ê + µ·T̂
  -Mbalanced  XGBoost pass ranker trained on measured data, then gated
  -Mperf      genetic-algorithm search maximising 1/EDP, then gated

Example::

    ./venv/bin/python compiler_driver.py benchmarks/fib_iter.c -Mperf
"""
import argparse
import sys
import time

from compile_pipeline import DEFAULT_RUNS, MODES, compile_source


def log_stage(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}]{msg}")


def main():
    parser = argparse.ArgumentParser(description="Energy Aware Compiler Optimization")
    parser.add_argument('source_file', type=str, help='Input MiniC source file')
    parser.add_argument('-Meco', action='store_true', help='Minimal Compile Overhead mode')
    parser.add_argument('-Mbalanced', action='store_true', help='ML Pass Ranker mode')
    parser.add_argument('-Mperf', action='store_true', help='Genetic Algorithm Search mode')
    parser.add_argument('--ga-generations', type=int, default=8,
                        help='GA generations for -Mperf (default 8)')
    parser.add_argument('--ga-population', type=int, default=16,
                        help='GA population size for -Mperf (default 16)')
    parser.add_argument('--no-object', action='store_true',
                        help='skip writing the object file')
    parser.add_argument('--runs', type=int, default=DEFAULT_RUNS,
                        help='assumed executions of the compiled program for '
                             f'the lifecycle EDP (default {DEFAULT_RUNS})')
    args = parser.parse_args()

    mode = '-Meco' if args.Meco else '-Mperf' if args.Mperf else '-Mbalanced'

    with open(args.source_file, 'r') as f:
        source_code = f.read()

    name = args.source_file.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    ga_kwargs = {"generations": args.ga_generations,
                 "pop_size": args.ga_population}

    t_start = time.time()
    result = compile_source(
        source_code, mode=mode, emit_object=not args.no_object, name=name,
        ga_kwargs=ga_kwargs if mode == '-Mperf' else None,
        n_runs=args.runs,
    )

    # replay the stage log the API/web UI also shows
    for line in result.get('logs', []):
        print(line)

    if not result.get('success'):
        print(f"\nCOMPILATION FAILED: {result.get('error', 'verification failed')}",
              file=sys.stderr)
        sys.exit(1)

    feats = result['features']
    print("\n--- Stage 2 metrics (52) ---")
    print(f"  instructions={feats['total_instructions']} "
          f"blocks={feats['num_basic_blocks']} "
          f"functions={feats['num_functions']} "
          f"branches={feats['num_branches']} "
          f"loops={feats['num_natural_loops']} "
          f"max_loop_depth={feats['max_loop_depth']} "
          f"cyclomatic={feats['cyclomatic_complexity']} "
          f"static_cost={feats['static_cost']:.0f}")

    energy = result['energy']
    print("\n--- Results ---")
    print(f"  mode:            {mode}")
    print(f"  gated passes:    {', '.join(result['selected_passes']) or '(none)'}")
    print(f"  IR instructions: {result['instructions']['before']} -> "
          f"{result['instructions']['after']}")
    print(f"  compile time:    {result['ast_time_ms']:.2f} ms front-end, "
          f"{result['passes_time_ms']:.2f} ms pass pipeline")
    if result.get('runtime_us') is not None:
        print(f"  native runtime:  {result['runtime_us']:.1f} us "
              f"(baseline {result['baseline_runtime_us']:.1f} us; "
              f"main returned {result['return_value']})")
    if result.get('object_file'):
        print(f"  object file:     {result['object_file']} "
              f"({result['object_bytes']} bytes)")
    print(f"  E_compile:       {energy['e_compile_j'] * 1e3:.4f} mJ "
          f"(baseline {energy['e_compile_baseline_j'] * 1e3:.4f} mJ; "
          f"{energy['e_compile_note']})")
    if energy.get('e_run_j') is not None:
        print(f"  E_run:           {energy['e_run_j'] * 1e6:.3f} uJ/run "
              f"(baseline {energy['e_run_baseline_j'] * 1e6:.3f}; "
              f"{energy['e_run_note']})")
        print(f"  EDP @ {energy['n_runs']:,} runs: {energy['edp']:.4e} "
              f"({energy['edp_savings_pct']:+.1f}% vs no-pass baseline)")
        be = energy['break_even_runs']
        print("  break-even:      " + (
            "optimization never repays its compile energy" if be is None
            else "no extra compile energy over baseline" if be == 0
            else f"{be:,.0f} runs repay the extra compile energy"))
    print(f"  differential:    optimized == unoptimized: "
          f"{result['differential_ok']}")
    print(f"\nTotal wall time: {(time.time() - t_start) * 1000:.0f} ms")


if __name__ == '__main__':
    main()

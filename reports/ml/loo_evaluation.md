# -Mbalanced leave-one-benchmark-out evaluation (lifecycle EDP at 10,000 runs)

| held-out | ranker picked | realized | oracle | regret |
|---|---|---:|---|---:|
| bit_series.c | `sroa+simplifycfg` | +40.7% | +40.7% (`sroa+simplifycfg`) | 0.0 pp |
| collatz_steps.c | `sroa+instcombine+simplifycfg+gvn` | +82.2% | +82.5% (`all passes`) | 0.3 pp |
| const_fold.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| fib_iter.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| fib_rec.c | `never optimize` | +0.0% | +75.9% (`O3`) | 75.9 pp |
| gcd_euclid.c | `sroa+simplifycfg` | -35.6% | +0.0% (`never optimize`) | 35.6 pp |
| mandelbrot.c | `O2` | +79.8% | +80.0% (`O1`) | 0.2 pp |
| matmul_trace.c | `O2` | -69.6% | +0.0% (`never optimize`) | 69.6 pp |
| newton_sqrt.c | `sroa+simplifycfg` | +71.7% | +71.7% (`sroa+simplifycfg`) | 0.0 pp |
| poly_horner.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| prime_count.c | `O3` | -13.6% | +42.0% (`loop-rotate+loop-unroll+lcsr`) | 55.5 pp |

| policy (same choice for every program) | mean savings |
|---|---:|
| **ranker (leave-one-out)** | **+14.1%** |
| sroa+simplifycfg | +14.9% |
| sroa+instcombine+simplifycfg+gvn | +2.2% |
| never optimize | +0.0% |
| O1 | -4.5% |
| sroa+sccp+simplifycfg+gvn+dse+adce | -7.8% |
| loop-rotate+loop-unroll+lcsr | -19.8% |
| O2 | -55.2% |
| all passes | -103.5% |
| O3 | -107.2% |
| oracle (best pool member per program) | +35.7% |

Mean regret 21.6 pp; the ranker is no worse than not optimizing on 8/11 held-out programs. Best fixed policy: `sroa+simplifycfg`.

# -Mbalanced leave-one-benchmark-out evaluation (lifecycle EDP at 10,000 runs)

| held-out | ranker picked | realized | oracle | regret |
|---|---|---:|---|---:|
| bit_series.c | `sroa+instcombine+simplifycfg+gvn` | +48.0% | +51.2% (`sroa+simplifycfg`) | 3.3 pp |
| collatz_long.c | `sroa+sccp+simplifycfg+gvn+dse+adce` | +7.9% | +36.2% (`O1`) | 28.3 pp |
| collatz_steps.c | `sroa+simplifycfg` | +34.4% | +81.6% (`all passes`) | 47.2 pp |
| const_fold.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| fib_iter.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| fib_rec.c | `sroa+simplifycfg` | +30.5% | +72.9% (`all passes`) | 42.4 pp |
| gcd_euclid.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| harmonic_sum.c | `O3` | +91.0% | +91.1% (`all passes`) | 0.1 pp |
| lcg_sum.c | `O2` | +71.1% | +71.4% (`O3`) | 0.3 pp |
| mandelbrot.c | `sroa+sccp+simplifycfg+gvn+dse+adce` | +80.5% | +83.8% (`O3`) | 3.3 pp |
| matmul_trace.c | `sroa+simplifycfg` | -19.4% | +0.0% (`never optimize`) | 19.4 pp |
| newton_sqrt.c | `O2` | +62.9% | +71.4% (`sroa+simplifycfg`) | 8.5 pp |
| poly_horner.c | `never optimize` | +0.0% | +0.0% (`never optimize`) | 0.0 pp |
| prime_below.c | `O3` | +19.3% | +19.3% (`O3`) | 0.0 pp |
| prime_count.c | `sroa+simplifycfg` | +8.9% | +8.9% (`sroa+simplifycfg`) | 0.0 pp |
| triple_loop.c | `sroa+instcombine+simplifycfg+gvn` | -32.0% | +41.6% (`loop-rotate+loop-unroll+lcsr`) | 73.5 pp |

| policy (same choice for every program) | mean savings |
|---|---:|
| **ranker (leave-one-out)** | **+25.2%** |
| sroa+simplifycfg | +23.5% |
| sroa+instcombine+simplifycfg+gvn | +12.8% |
| sroa+sccp+simplifycfg+gvn+dse+adce | +3.5% |
| never optimize | +0.0% |
| O1 | -0.5% |
| loop-rotate+loop-unroll+lcsr | -13.9% |
| O2 | -50.1% |
| all passes | -70.1% |
| O3 | -91.3% |
| oracle (best pool member per program) | +39.3% |

Mean regret 14.1 pp; the ranker is no worse than not optimizing on 14/16 held-out programs. Best fixed policy: `sroa+simplifycfg`.

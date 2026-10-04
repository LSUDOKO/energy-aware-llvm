# Benchmark results

Generated 2026-10-04T17:48:51 on **AMD Ryzen 7 5700U with Radeon Graphics** (Linux 7.1.11-arch1-1, Python 3.14.7); 11 kernels, 7 interleaved native trials each, lifecycle EDP at 10,000 executions.

**RAPL was not readable** when this run was made, so energy is the labelled estimate `P-hat x T` (P-hat = 25 W) and every claim below rests on measured *time*.

All objects were linked and run natively; every build below returned the manifest value (`correct` column). Run time is the median of 7 interleaved trials of `backend/native_harness.c` calling the emitted object's `main` in a tight loop.

## Summary

| build | geomean speedup vs unopt | median EDP savings @ 10,000 runs | kernels where it repays compile cost |
|---|---:|---:|---:|
| -Meco | 1.54x | -104.1% | 4/11 |
| -Mbalanced | 1.38x | -61.5% | 4/11 |
| -Mperf | 1.75x | -312212.7% | 0/11 |
| clang-O0 | 0.62x | -373.7% | 0/11 |
| clang-O2 | 2.98x | -124.2% | 4/11 |

## Per kernel

| kernel | build | correct | run (us) | speedup | compile (ms) | break-even runs | EDP savings @ 1 / 10k / 1M runs | passes |
|---|---|:-:|---:|---:|---:|---:|---|---|
| fib_iter | unopt | yes | 0.08 | 1.00x | 7.3 | 0 | +0% / +0% / +0% | - |
| fib_iter | -Meco | yes | 0.02 | 5.19x | 31.6 | 17,577 | -403% / -314% / +86% | -sroa |
| fib_iter | -Mbalanced | yes | 0.08 | 1.01x | 13.5 | 10,833,319 | -227% / -197% / -12% | - |
| fib_iter | -Mperf | yes | 0.01 | 7.08x | 1255.1 | 17,179,307 | -2923023% / -2352474% / -19025% | -sroa -instcombine |
| fib_iter | clang-O0 | yes | 0.08 | 1.02x | 24.7 | 9,134,227 | -1040% / -879% / -37% | - |
| fib_iter | clang-O2 | yes | 0.00 | 56.63x | 31.7 | 295,420 | -1776% / -1411% / +87% | - |
| fib_rec | unopt | yes | 57.66 | 1.00x | 9.6 | 0 | +0% / +0% / +0% | - |
| fib_rec | -Meco | yes | 49.77 | 1.16x | 42.3 | 11 | -342% / +20% / +25% | -sroa |
| fib_rec | -Mbalanced | yes | 34.22 | 1.69x | 111.2 | 1,883 | -6310% / +48% / +65% | -O3 |
| fib_rec | -Mperf | yes | 32.97 | 1.75x | 1554.4 | 62,020 | -2564893% / -926% / +64% | -instcombine -gvn -dse -tailcallelim |
| fib_rec | clang-O0 | yes | 64.20 | 0.90x | 25.6 | never | -608% / -30% / -24% | - |
| fib_rec | clang-O2 | yes | 35.46 | 1.63x | 29.2 | 883 | -815% / +57% / +62% | - |
| gcd_euclid | unopt | yes | 0.01 | 1.00x | 10.0 | 0 | +0% / +0% / +0% | - |
| gcd_euclid | -Meco | yes | 0.01 | 1.02x | 47.4 | 3,630,647 | -411% / -406% / -177% | -sroa |
| gcd_euclid | -Mbalanced | yes | 0.01 | 1.03x | 14.7 | 11,680,926 | -106% / -105% / -40% | - |
| gcd_euclid | -Mperf | yes | 0.01 | 1.00x | 1743.2 | 37,291,818,129 | -3005745% / -2936076% / -637265% | -instcombine |
| gcd_euclid | clang-O0 | yes | 0.01 | 1.02x | 30.2 | 77,289,798 | -812% / -798% / -266% | - |
| gcd_euclid | clang-O2 | yes | 0.01 | 1.00x | 37.1 | 72,771,650,400 | -1276% / -1252% / -401% | - |
| prime_count | unopt | yes | 9.01 | 1.00x | 16.5 | 0 | +0% / +0% / +0% | - |
| prime_count | -Meco | yes | 9.31 | 0.97x | 63.8 | never | -356% / -55% / -7% | -sroa -instcombine |
| prime_count | -Mbalanced | yes | 9.70 | 0.93x | 63.1 | never | -992% / -103% / -17% | - |
| prime_count | -Mperf | yes | 9.73 | 0.93x | 2793.7 | never | -2835396% / -72495% / -92% | -simplifycfg |
| prime_count | clang-O0 | yes | 9.93 | 0.91x | 31.3 | never | -262% / -50% / -22% | - |
| prime_count | clang-O2 | yes | 8.89 | 1.01x | 57.5 | 328,197 | -1117% / -88% / +2% | - |
| collatz_steps | unopt | yes | 155.33 | 1.00x | 19.4 | 0 | +0% / +0% / +0% | - |
| collatz_steps | -Meco | yes | 60.91 | 2.55x | 68.1 | 54 | -338% / +83% / +85% | -sroa -instcombine -simplifycfg |
| collatz_steps | -Mbalanced | yes | 60.42 | 2.57x | 124.1 | 234 | -1250% / +81% / +85% | -O3 |
| collatz_steps | -Mperf | yes | 59.85 | 2.60x | 2700.8 | 27,766 | -1883877% / -336% / +84% | -sroa -instcombine -simplifycfg |
| collatz_steps | clang-O0 | yes | 594.30 | 0.26x | 35.2 | never | -234% / -1345% / -1364% | - |
| collatz_steps | clang-O2 | yes | 105.37 | 1.47x | 51.5 | 642 | -596% / +51% / +54% | - |
| matmul_trace | unopt | yes | 0.91 | 1.00x | 10.8 | 0 | +0% / +0% / +0% | - |
| matmul_trace | -Meco | yes | 0.75 | 1.22x | 72.3 | 49,532 | -1076% / -432% / +26% | -sroa -instcombine |
| matmul_trace | -Mbalanced | yes | 0.92 | 0.99x | 20.8 | never | -252% / -119% / -4% | - |
| matmul_trace | -Mperf | yes | 0.71 | 1.29x | 1900.3 | 9,245,269 | -3090928% / -914875% / -698% | -instcombine -gvn -lcsr |
| matmul_trace | clang-O0 | yes | 2.28 | 0.40x | 30.1 | never | -686% / -612% / -532% | - |
| matmul_trace | clang-O2 | yes | 0.20 | 4.49x | 40.2 | 41,697 | -1297% / -353% / +93% | - |
| bit_series | unopt | yes | 3.14 | 1.00x | 11.9 | 0 | +0% / +0% / +0% | - |
| bit_series | -Meco | yes | 2.99 | 1.05x | 54.8 | 23,122 | -487% / -104% / +8% | -sroa -instcombine |
| bit_series | -Mbalanced | yes | 1.88 | 1.67x | 67.4 | 31,638 | -2350% / -224% / +62% | -sroa -instcombine -simplifycfg |
| bit_series | -Mperf | yes | 1.98 | 1.59x | 2410.5 | 2,052,081 | -4044683% / -312213% / -93% | -sroa -instcombine -simplifycfg |
| bit_series | clang-O0 | yes | 18.50 | 0.17x | 33.0 | never | -667% / -2435% / -3363% | - |
| bit_series | clang-O2 | yes | 2.45 | 1.28x | 40.3 | 41,330 | -1042% / -124% / +37% | - |
| poly_horner | unopt | yes | 1.40 | 1.00x | 9.4 | 0 | +0% / +0% / +0% | - |
| poly_horner | -Meco | yes | 1.33 | 1.06x | 38.4 | 18,906 | -373% / -128% / +8% | -sroa |
| poly_horner | -Mbalanced | yes | 1.34 | 1.04x | 16.8 | 107,880 | -203% / -62% / +7% | - |
| poly_horner | -Mperf | yes | 1.12 | 1.25x | 1655.4 | 5,727,489 | -3098569% / -502876% / -284% | -sroa -instcombine -loop-rotate |
| poly_horner | clang-O0 | yes | 2.50 | 0.56x | 25.9 | never | -667% / -374% / -219% | - |
| poly_horner | clang-O2 | yes | 0.52 | 2.71x | 33.6 | 27,384 | -1188% / -175% / +85% | - |
| newton_sqrt | unopt | yes | 11.84 | 1.00x | 9.8 | 0 | +0% / +0% / +0% | - |
| newton_sqrt | -Meco | yes | 5.94 | 1.99x | 48.9 | 467 | -538% / +53% / +75% | -sroa -gvn |
| newton_sqrt | -Mbalanced | yes | 6.07 | 1.95x | 32.4 | 2,069 | -632% / +53% / +74% | -sroa -instcombine -simplifycfg -gvn |
| newton_sqrt | -Mperf | yes | 6.03 | 1.96x | 2167.6 | 368,205 | -4840983% / -29842% / +52% | -sroa -gvn -loop-rotate |
| newton_sqrt | clang-O0 | yes | 12.24 | 0.97x | 26.6 | never | -634% / -35% / -7% | - |
| newton_sqrt | clang-O2 | yes | 0.97 | 12.22x | 43.6 | 3,111 | -1878% / +83% / +99% | - |
| mandelbrot | unopt | yes | 42.11 | 1.00x | 23.8 | 0 | +0% / +0% / +0% | - |
| mandelbrot | -Meco | yes | 17.20 | 2.45x | 72.7 | 110 | -239% / +75% / +83% | -sroa -instcombine |
| mandelbrot | -Mbalanced | yes | 17.08 | 2.47x | 67.6 | 152 | -228% / +76% / +83% | -O2 |
| mandelbrot | -Mperf | yes | 17.05 | 2.47x | 2458.6 | 96,194 | -1049106% / -3359% / +79% | -sroa -instcombine -gvn |
| mandelbrot | clang-O0 | yes | 47.52 | 0.89x | 26.7 | never | -25% / -27% / -27% | - |
| mandelbrot | clang-O2 | yes | 16.76 | 2.51x | 34.7 | 430 | -112% / +79% / +84% | - |
| const_fold | unopt | yes | 0.00 | 1.00x | 14.1 | 0 | +0% / +0% / +0% | - |
| const_fold | -Meco | yes | 0.00 | 1.20x | 51.5 | 1,627,171 | -291% / -290% / -212% | -sroa |
| const_fold | -Mbalanced | yes | 0.00 | 0.98x | 14.8 | never | -3% / -3% / -3% | - |
| const_fold | -Mperf | yes | 0.00 | 1.28x | 1452.8 | 1,721,182,515 | -1046779% / -1041261% / -655043% | -sroa -simplifycfg -gvn -lcsr -loop-unroll -ipsccp |
| const_fold | clang-O0 | yes | 0.00 | 0.78x | 25.5 | never | -224% / -223% / -186% | - |
| const_fold | clang-O2 | yes | 0.00 | 2.55x | 28.2 | 6,134,521 | -297% / -296% / -174% | - |

## Charts

### Native speedup of each object file

![Native speedup of each object file](runtime_speedup.png)

### Compile time per build

![Compile time per build](compile_time.png)

### Lifecycle EDP vs run count

![Lifecycle EDP vs run count](edp_vs_runs.png)

### Break-even run counts

![Break-even run counts](break_even.png)

### -Mperf GA convergence

![-Mperf GA convergence](perfmode_ga.png)

### Compile-time breakdown by stage

![Compile-time breakdown by stage](compile_stages.png)

## Notes on validity

- The compiler under test is written in Python (llvmlite) and the clang rows are a native C++ compiler: absolute compile times are not comparable between them, only the *shape* (what the energy-aware modes spend and what they buy) is.
- `unopt` is the unified front-end with no optional passes; it is the baseline for speedup, EDP savings and break-even.
- `-Mperf` pays for its genetic-algorithm search in `compile (ms)`; it breaks even only for programs that run many times.
- clang rows compile float-suffixed source so MiniC's `float` literals mean the same thing in C.

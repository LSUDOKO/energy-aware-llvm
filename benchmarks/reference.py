#!/usr/bin/env python
"""Independent reference implementations for the MiniC benchmark suite.

These implementations are written directly from each kernel's specification
(no LLVM, no compiler code involved) and provide the expected ``main()``
return values for the regression suite.  Float kernels mirror IEEE-754
**single-precision** semantics (``numpy.float32``, round-to-nearest, same
operation order as the C source) because the compiler's ``float`` type is
``i32``-precision; integer kernels are exact.

Usage::

    ./venv/bin/python benchmarks/reference.py --write   # (re)generate manifest
    ./venv/bin/python benchmarks/reference.py --check   # verify manifest
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
MANIFEST = BENCH_DIR / "manifest.json"
f32 = np.float32


# ---------------------------------------------------------------------------
# integer kernels (exact)
# ---------------------------------------------------------------------------
def ref_fib_iter() -> int:
    a, b = 0, 1
    for _ in range(30):
        a, b = b, a + b
    return a


def ref_fib_rec() -> int:
    def fib(n: int) -> int:
        return n if n < 2 else fib(n - 1) + fib(n - 2)
    return fib(21)


def ref_gcd_euclid() -> int:
    return math.gcd(1071, 462)


def ref_prime_count() -> int:
    def is_prime(n: int) -> bool:
        if n < 2:
            return False
        d = 2
        while d * d <= n:
            if n % d == 0:
                return False
            d += 1
        return True
    return sum(1 for i in range(2, 501) if is_prime(i))


def ref_collatz_steps() -> int:
    def steps(n: int) -> int:
        s = 0
        while n != 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            s += 1
        return s
    return max(steps(i) for i in range(1, 1000))


def ref_matmul_trace() -> int:
    n = 16
    total = 0
    for i in range(n):
        s = 0
        for k in range(n):
            a = (i * 3 + k) % 7
            b = (k * 5 + i) % 5
            s += a * b
        total += s
    return total


def ref_bit_series() -> int:
    def popcount(x: int) -> int:
        c = 0
        while x != 0:
            c += x % 2
            x //= 2
        return c
    return sum(popcount(i) * (i % 3) for i in range(1, 300))


def ref_const_fold() -> int:
    # mix(10): 3*(4+5) - 2*7 = 13; y = 10; z = 23 -> a = 23
    a = 10 + 13
    b = 64 // 4 // 4                     # 4
    f = f32(1.5) * f32(4.0) + f32(0.5)   # 6.5
    acc = 0
    if a == 23:
        acc += b
    while f > f32(6.0):
        acc += 1
        f = f - f32(1.0)
    return acc


# ---------------------------------------------------------------------------
# float kernels (float32 semantics, mirroring operation order)
# ---------------------------------------------------------------------------
def _poly(x):
    r = f32(5.0)
    r = r * x - f32(2.0)
    r = r * x + f32(3.0)
    r = r * x - f32(1.0)
    r = r * x + f32(7.0)
    return r


def ref_poly_horner() -> int:
    hits = 0
    x = f32(0.1)
    for _ in range(400):
        y = _poly(x)
        if y > f32(6.5):
            hits += 1
        x = x + f32(0.01)
    return hits


def _mysqrt(x):
    g = x
    for _ in range(24):
        g = f32(0.5) * (g + x / g)
    return g


def ref_newton_sqrt() -> int:
    hits = 0
    v = f32(1.0)
    while v <= f32(100.0):
        r = _mysqrt(v)
        if r * r > v * f32(0.999):
            if r * r < v * f32(1.001):
                hits += 1
        v = v + f32(1.0)
    return hits


def _escape(cx, cy):
    x = f32(0.0)
    y = f32(0.0)
    it = 0
    while it < 30:
        xt = x * x - y * y + cx
        y = f32(2.0) * x * y + cy
        x = xt
        if x * x + y * y > f32(4.0):
            return it + 1
        it += 1
    return 30


def ref_mandelbrot() -> int:
    total = 0
    for i in range(30):
        for j in range(24):
            cx = f32(-2.0) + f32(i) * f32(0.1)
            cy = f32(-1.2) + f32(j) * f32(0.1)
            total += _escape(cx, cy)
    return total


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# longer-running kernels (milliseconds; use for loops)
# ---------------------------------------------------------------------------
def ref_lcg_sum() -> int:
    x, s = 12345, 0
    for _ in range(2000000):
        x = (x * 75 + 74) % 65537
        s += x % 10
    return s


def ref_triple_loop() -> int:
    return sum(1 for i in range(1, 151) for j in range(1, 151)
               for k in range(1, 151) if (i * j + k) % 7 == 0)


def ref_harmonic_sum() -> int:
    s = f32(0.0)
    for k in range(1, 400001):
        s = f32(s + f32(f32(1.0) / f32(k)))
    return int(f32(s * f32(1000.0)))


def ref_prime_below() -> int:
    count = 0
    for n in range(2, 30000):
        d = 2
        prime = True
        while d * d <= n:
            if n % d == 0:
                prime = False
                break
            d += 1
        count += prime
    return count


def ref_collatz_long() -> int:
    best = 0
    for start in range(1, 30000):
        n, steps = start, 0
        while n != 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            steps += 1
        best = max(best, steps)
    return best


SPECS = {
    "fib_iter": (ref_fib_iter, "iterative Fibonacci, fib(30)"),
    "fib_rec": (ref_fib_rec, "naive recursive Fibonacci, fib(21)"),
    "gcd_euclid": (ref_gcd_euclid, "Euclidean GCD, gcd(1071, 462)"),
    "prime_count": (ref_prime_count, "primes <= 500 by trial division"),
    "collatz_steps": (ref_collatz_steps, "longest Collatz streak below 1000"),
    "matmul_trace": (ref_matmul_trace, "16x16 matrix diagonal trace"),
    "bit_series": (ref_bit_series, "popcount-weighted series to 300"),
    "poly_horner": (ref_poly_horner, "Horner polynomial hits, x in [0.1, 4.1]"),
    "newton_sqrt": (ref_newton_sqrt, "Newton sqrt accuracy hits, 1..100"),
    "mandelbrot": (ref_mandelbrot, "escape-time sum, 30x24 grid"),
    "const_fold": (ref_const_fold, "constant-folding showcase"),
    "lcg_sum": (ref_lcg_sum, "LCG digit sum, 2M iterations"),
    "triple_loop": (ref_triple_loop, "3 nested loops, 3.4M iterations"),
    "harmonic_sum": (ref_harmonic_sum, "float harmonic series, 400k terms"),
    "prime_below": (ref_prime_below, "primes below 30000, trial division"),
    "collatz_long": (ref_collatz_long, "longest Collatz chain below 30000"),
}


def build_manifest() -> dict:
    entries = []
    for name, (fn, desc) in SPECS.items():
        path = BENCH_DIR / f"{name}.c"
        if not path.exists():
            raise SystemExit(f"missing benchmark source: {path}")
        entries.append({
            "name": name,
            "file": path.name,
            "description": desc,
            "expected_return": int(fn()),
            "tolerance": 0,
        })
    return {"entries": entries,
            "note": "expected_return computed by benchmarks/reference.py "
                    "(independent implementations; float kernels use "
                    "float32 semantics)"}


def main() -> None:
    args = sys.argv[1:]
    if "--write" in args:
        manifest = build_manifest()
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Wrote {MANIFEST} ({len(manifest['entries'])} entries)")
        for e in manifest["entries"]:
            print(f"  {e['name']:16s} -> {e['expected_return']}")
    elif "--check" in args:
        current = json.loads(MANIFEST.read_text())
        fresh = build_manifest()
        if current != fresh:
            print("MANIFEST MISMATCH:")
            print(json.dumps(current, indent=2))
            print("expected:")
            print(json.dumps(fresh, indent=2))
            raise SystemExit(1)
        print(f"manifest OK ({len(fresh['entries'])} entries)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

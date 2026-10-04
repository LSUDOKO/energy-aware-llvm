"""Target numeric semantics shared by every compile-time evaluator.

MiniC ``int`` is a 32-bit two's-complement integer and ``float`` is IEEE-754
binary32.  Compile-time folding must reproduce what the generated machine
code would compute, otherwise an "optimization" silently changes program
behaviour.  Python integers are unbounded and Python floats are binary64,
so every folded result is passed through these helpers.
"""
from __future__ import annotations

import math
import struct

INT_BITS = 32
INT_MIN = -(1 << (INT_BITS - 1))
INT_MAX = (1 << (INT_BITS - 1)) - 1


def wrap_int(value: int, bits: int = INT_BITS) -> int:
    """Reduce to the signed ``bits``-wide two's-complement range."""
    mask = (1 << bits) - 1
    value &= mask
    return value - (1 << bits) if value >> (bits - 1) else value


def f32(value: float) -> float:
    """Round to the nearest binary32 value (overflow becomes +/-inf)."""
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def c_div(a: int, b: int) -> int:
    """C signed division (truncates toward zero); caller guards ``b != 0``."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def c_rem(a: int, b: int) -> int:
    """C signed remainder: result has the sign of the dividend."""
    return a - c_div(a, b) * b


def float_to_int(value: float) -> int | None:
    """C float->int conversion (truncation). ``None`` when the result is
    undefined (NaN, infinity, or out of int range) and must not be folded."""
    if math.isnan(value) or math.isinf(value):
        return None
    truncated = int(value)
    if not INT_MIN <= truncated <= INT_MAX:
        return None
    return truncated

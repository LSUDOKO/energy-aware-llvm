"""Target numeric semantics used by compile-time folding."""
import math

import pytest

from frontend.numeric import (INT_MAX, INT_MIN, c_div, c_rem, f32,
                              float_to_int, wrap_int)


@pytest.mark.parametrize("raw,expected", [
    (0, 0), (INT_MAX, INT_MAX), (INT_MAX + 1, INT_MIN), (INT_MIN - 1, INT_MAX),
    (1 << 32, 0), ((1 << 32) + 7, 7), (-(1 << 32) - 3, -3), (65536 * 65536, 0),
])
def test_wrap_int(raw, expected):
    assert wrap_int(raw) == expected


def test_f32_rounds_to_binary32():
    assert f32(16777217.0) == 16777216.0          # 2^24 + 1 is not representable
    assert f32(0.1) != 0.1 and abs(f32(0.1) - 0.1) < 1e-8
    assert f32(1.0e39) == math.inf and f32(-1.0e39) == -math.inf


def test_float_add_is_binary32_not_binary64():
    assert f32(f32(0.1) + f32(0.2)) == f32(0.3)   # true in binary32
    assert 0.1 + 0.2 != 0.3                        # false in binary64


@pytest.mark.parametrize("a,b,q,r", [
    (7, 2, 3, 1), (-7, 2, -3, -1), (7, -2, -3, 1), (-7, -2, 3, -1), (6, 3, 2, 0),
])
def test_c_div_and_rem(a, b, q, r):
    assert c_div(a, b) == q and c_rem(a, b) == r
    assert q * b + r == a


@pytest.mark.parametrize("value,expected", [
    (3.9, 3), (-3.9, -3), (0.0, 0), (2147483647.0, 2147483647),
    (2147483648.0, None), (float("nan"), None), (float("inf"), None),
])
def test_float_to_int(value, expected):
    assert float_to_int(value) == expected

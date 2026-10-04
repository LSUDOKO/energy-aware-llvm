"""Online folding must reproduce target arithmetic, not Python's.

Each program runs through the conventional pipeline (no folding: the machine
does the arithmetic) and the unified pipeline (folds at compile time); they
must agree with each other and with the hand-derived C result.
"""
import pytest

from energy.execution import jit_run
from energy.experiments import conventional_pipeline, merged_pipeline

CASES = {
    # 2147483647 + 1 wraps to INT_MIN
    "add_overflow_wraps": (
        "int main() { int a = 2147483647; int b = a + 1; "
        "if (b < 0) { return 1; } return 0; }", 1),
    "mul_overflow_wraps": ("int main() { return 65536 * 65536 + 7; }", 7),
    "sub_overflow_wraps": (
        "int main() { int a = -2147483647; int b = a - 2; "
        "if (b > 0) { return 1; } return 0; }", 1),
    "neg_int_min_wraps": (
        "int main() { int a = -2147483647; int b = a - 1; "
        "int c = -b; if (c < 0) { return 1; } return 0; }", 1),
    # 2^24 + 1 is not representable in binary32: result is 0, not 1
    "float_fold_is_binary32": (
        "int main() { float x = 16777216.0 + 1.0; float y = x - 16777216.0; "
        "return y; }", 0),
    # 0.1f + 0.2f == 0.3f in binary32 (false in binary64)
    "float_equality_prunes_like_binary32": (
        "int main() { float a = 0.1 + 0.2; if (a == 0.3) { return 1; } "
        "return 0; }", 1),
    "float_loop_bound_binary32": (
        "int main() { float f = 16777216.0; f = f + 1.0; "
        "if (f == 16777216.0) { return 1; } return 0; }", 1),
    "int_division_truncates": ("int main() { return -7 / 2; }", -3),
    # inf * 0.0 is NaN, so z != z; folding x*0.0 to 0.0 would return 0
    "float_mul_zero_keeps_nan": (
        "float inf() { float b = 1000000.0; b = b * b; b = b * b; b = b * b; "
        "return b; } "
        "int main() { float z = inf() * 0.0; if (z == z) { return 0; } "
        "return 1; }", 1),
    # NaN != NaN is true in C
    "nan_not_equal_is_true": (
        "float inf() { float b = 1000000.0; b = b * b; b = b * b; b = b * b; "
        "return b; } "
        "int main() { float n = inf() - inf(); if (n != n) { return 1; } "
        "return 0; }", 1),
    # runtime i1 -> float must be sitofp of the zext, not an i32 pun
    "runtime_compare_to_float": (
        "float conv(int a, int b) { float f = a < b; return f; } "
        "int main() { return conv(3, 4) * 8 + conv(5, 4); }", 8),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_target_semantics(name):
    src, expected = CASES[name]
    conv = jit_run(conventional_pipeline(src).ir_text)
    merged = jit_run(merged_pipeline(src).ir_text)
    assert conv == expected, "conventional baseline disagrees with C"
    assert merged == expected, "unified pipeline folded differently from C"

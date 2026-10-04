"""Implicit int<->float conversions agree across both front-end pipelines."""
import pytest

from energy.execution import jit_run
from energy.experiments import conventional_pipeline, merged_pipeline

CASES = {
    "float_times_int": ("int main() { float x = 2.5; return x * 4; }", 10),
    "int_var_gets_float": ("int main() { int n = 7.9; return n; }", 7),
    "negative_truncates_to_zero": ("int main() { int n = -7.9; return n; }", -7),
    "float_var_gets_int": ("int main() { float f = 3; return f * 2.5; }", 7),
    "return_float_from_int_fn": ("int half(float v) { return v / 2; } "
                                 "int main() { return half(9); }", 4),
    "arg_coerced_to_float": ("float sq(float v) { return v * v; } "
                             "int main() { return sq(3) + 0.5; }", 9),
    "compare_mixed": ("int main() { float x = 2.5; int n = 2; "
                      "if (x > n) { return 1; } return 0; }", 1),
    "compare_result_to_float": ("int main() { int a = 3; int b = 4; "
                                "float f = a < b; return f + 10; }", 11),
    "assign_after_decl": ("int main() { float f = 1.0; int i = 5; "
                          "f = i; return f * 3; }", 15),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_pipelines_agree_and_match_c(name):
    src, expected = CASES[name]
    conv = jit_run(conventional_pipeline(src).ir_text)
    merged = jit_run(merged_pipeline(src).ir_text)
    assert conv == merged == expected

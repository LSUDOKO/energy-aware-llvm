"""for loops, compound assignment and ++/-- (desugared in the parser)."""
import pytest

from energy.execution import jit_run
from energy.experiments import conventional_pipeline, merged_pipeline

CASES = {
    "for_sum": ("int main() { int s = 0; for (int i = 0; i < 10; i++) { s += i; } return s; }", 45),
    "for_assign_init": ("int main() { int i = 0; int s = 0; for (i = 3; i < 6; i = i + 1) { s += i; } return s + i; }", 18),
    "decrement": ("int main() { int s = 0; for (int i = 5; i > 0; i--) { s += i; } return s; }", 15),
    "compound_ops": ("int main() { int a = 10; a -= 3; a *= 4; a /= 2; a += 1; return a; }", 15),
    "nested": ("int main() { int c = 0; for (int i = 0; i < 4; i++) { for (int j = 0; j < 5; j++) { c++; } } return c; }", 20),
    "loop_var_scoped": ("int main() { int i = 100; for (int i = 0; i < 3; i++) { } return i; }", 100),
    "float_accumulate": ("int main() { float f = 0.0; for (int i = 0; i < 4; i++) { f += 0.5; } return f * 2; }", 4),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_pipelines_agree_with_c(name):
    src, expected = CASES[name]
    assert jit_run(conventional_pipeline(src).ir_text) == expected
    assert jit_run(merged_pipeline(src).ir_text) == expected


def test_compound_after_comment_and_division_still_lex():
    src = "int main() { int a = 8; // c\n a /= 2; return a / 2; }"
    assert jit_run(merged_pipeline(src).ir_text) == 2

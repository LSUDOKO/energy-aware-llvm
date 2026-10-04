"""The % operator: C remainder semantics in both pipelines, int-only."""
import pytest

from energy.execution import jit_run
from energy.experiments import conventional_pipeline, merged_pipeline

CASES = {
    "basic": ("int main() { return 17 % 5; }", 2),
    "negative_dividend_keeps_sign": ("int main() { return -17 % 5; }", -2),
    "negative_divisor": ("int main() { return 17 % -5; }", 2),
    "precedence_with_mul": ("int main() { return 2 + 7 * 3 % 4; }", 3),
    "runtime_values": (
        "int rem(int a, int b) { return a % b; } "
        "int main() { return rem(-23, 7) * 10 + rem(23, 7); }", -18),
    "mod_one": ("int f(int a) { return a % 1; } int main() { return f(9) + 4; }", 4),
    "gcd_with_modulo": (
        "int gcd(int a, int b) { while (b != 0) { int r = a % b; a = b; b = r; } "
        "return a; } int main() { return gcd(1071, 462); }", 21),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_pipelines_agree_with_c(name):
    src, expected = CASES[name]
    assert jit_run(conventional_pipeline(src).ir_text) == expected
    assert jit_run(merged_pipeline(src).ir_text) == expected


def test_modulo_by_zero_is_not_folded():
    ir = merged_pipeline("int f(int a) { return a % 0; } int main() { return 0; }").ir_text
    assert "srem" in ir


def test_float_operands_are_rejected_everywhere():
    src = "int main() { float x = 5.5; return x % 2; }"
    assert not merged_pipeline(src).checks_ok
    with pytest.raises(Exception):
        conventional_pipeline(src)
    from frontend.lexer import Lexer
    from frontend.parser import Parser
    from frontend.semantic_checker import SemanticChecker
    errs = SemanticChecker().check(Parser(Lexer(src).tokens).parse())
    assert any("integer operands" in e for e in errs)

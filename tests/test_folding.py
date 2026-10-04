"""Phase 2 tests: unified semantic visitor, online folding, pruning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from llvmlite import ir

from energy.execution import jit_run, runs_match
from energy.experiments import conventional_pipeline, merged_pipeline
from frontend.ir_builder import SimplifyingIRBuilder


FOLD_PROGRAM = """
int main() {
    int z = 2 + 3 * 4;
    return z;
}
"""

IDENTITY_PROGRAM = """
int scale(int x) {
    return x + 0;
}
int main() {
    int r = scale(7);
    return r;
}
"""

STRENGTH_PROGRAM = """
int scale8(int x) {
    return x * 8;
}
int main() {
    return scale8(3);
}
"""

CONST_PROP_PROGRAM = """
int main() {
    int a = 5;
    a = a + 1;
    int b = a * 2;
    return b;
}
"""

INVALIDATION_PROGRAM = """
int f(int x) {
    int a = 5;
    a = x;
    return a * 4;
}
int main() {
    return f(2);
}
"""

PRUNE_IF_PROGRAM = """
int main() {
    if (1) {
        return 7;
    }
    return 0;
}
"""

PRUNE_WHILE_PROGRAM = """
int main() {
    int x = 1;
    while (0) {
        x = x + 5;
    }
    return x;
}
"""

RUNTIME_LOOP_PROGRAM = """
int sum_to(int n) {
    int i = 0;
    int s = 0;
    while (i < n) {
        s = s + i;
        i = i + 1;
    }
    return s;
}
int main() {
    if (sum_to(10) == 45) {
        return 1;
    }
    return 0;
}
"""

FLOAT_PROGRAM = """
int main() {
    float f = 1.5 + 2.25;
    if (f > 3.0) {
        return 1;
    }
    return 0;
}
"""


class TestConstantFolding:
    def test_folds_arithmetic_end_to_end(self):
        merged = merged_pipeline(FOLD_PROGRAM)
        assert merged.checks_ok
        assert "mul" not in merged.ir_text
        assert "add i32" not in merged.ir_text
        assert "ret i32 14" in merged.ir_text
        assert merged.stats["folded"] >= 2

    def test_conventional_does_not_fold(self):
        conv = conventional_pipeline(FOLD_PROGRAM)
        assert "mul" in conv.ir_text and "add i32" in conv.ir_text
        assert conv.stats == {}

    def test_merged_emits_fewer_instructions(self):
        conv = conventional_pipeline(FOLD_PROGRAM)
        merged = merged_pipeline(FOLD_PROGRAM)
        assert conv.ir_text.count("\n") > merged.ir_text.count("\n")

    def test_float_folding(self):
        merged = merged_pipeline(FLOAT_PROGRAM)
        # 1.5 + 2.25 = 3.75 folded; branch pruned to return 1
        assert merged.checks_ok
        assert merged.stats["branches_pruned"] == 1
        assert jit_run(merged.ir_text) == 1
        conv = conventional_pipeline(FLOAT_PROGRAM)
        assert jit_run(conv.ir_text) == 1


class TestAlgebraicSimplification:
    def test_add_zero_identity(self):
        merged = merged_pipeline(IDENTITY_PROGRAM)
        assert "add i32" not in merged.ir_text
        assert merged.stats["identities"] >= 1
        assert jit_run(merged.ir_text) == 7

    def test_strength_reduction_to_shl(self):
        merged = merged_pipeline(STRENGTH_PROGRAM)
        assert "shl" in merged.ir_text
        assert "mul" not in merged.ir_text
        assert merged.stats["strength_reductions"] >= 1
        assert jit_run(merged.ir_text) == 24


class TestConstantPropagation:
    def test_propagates_through_assignment(self):
        merged = merged_pipeline(CONST_PROP_PROGRAM)
        assert "ret i32 12" in merged.ir_text
        assert jit_run(merged.ir_text) == 12

    def test_invalidated_by_runtime_assignment(self):
        merged = merged_pipeline(INVALIDATION_PROGRAM)
        # `a = x` makes a runtime; a*4 must become a shift, not a constant
        assert "shl" in merged.ir_text
        assert jit_run(merged.ir_text) == 8


class TestBranchPruning:
    def test_constant_if_pruned(self):
        merged = merged_pipeline(PRUNE_IF_PROGRAM)
        assert "br i1" not in merged.ir_text
        assert merged.stats["branches_pruned"] == 1
        assert jit_run(merged.ir_text) == 7

    def test_while_zero_pruned(self):
        merged = merged_pipeline(PRUNE_WHILE_PROGRAM)
        assert merged.stats["loops_pruned"] == 1
        assert "br " not in merged.ir_text
        assert jit_run(merged.ir_text) == 1

    def test_unreachable_statements_after_return(self):
        src = """
        int main() {
            return 3;
            return 9;
        }
        """
        merged = merged_pipeline(src)
        assert merged.checks_ok
        assert merged.stats["statements_pruned"] == 1
        assert jit_run(merged.ir_text) == 3

    def test_runtime_loop_not_pruned(self):
        merged = merged_pipeline(RUNTIME_LOOP_PROGRAM)
        assert "br i1" in merged.ir_text
        assert merged.stats["loops_pruned"] == 0
        assert jit_run(merged.ir_text) == 1


class TestTypedAnnotation:
    def test_nodes_annotated_after_lowering(self):
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        ast = Parser(Lexer(FOLD_PROGRAM).tokens).parse()
        visitor = UnifiedSemanticVisitor()
        visitor.generate(ast)
        ret_stmt = ast.functions[0].body.statements[1]
        assert ret_stmt.expr.const_value == 14
        assert ret_stmt.expr.type == "i32"
        assert ret_stmt.expr.value_category == "rvalue"


class TestDiagnostics:
    def test_undeclared_variable_recovered(self):
        src = """
        int main() {
            int ok = 1;
            bad = 2;
            int also_ok = ok;
            return also_ok;
        }
        """
        merged = merged_pipeline(src)
        assert any("undeclared variable 'bad'" in e for e in merged.errors)
        assert merged.checks_ok is False
        # Recovery: later statements still compiled and executed
        assert jit_run(merged.ir_text) == 1

    def test_duplicate_declaration_detected(self):
        src = """
        int main() {
            int a = 1;
            int a = 2;
            return a;
        }
        """
        merged = merged_pipeline(src)
        assert any("redeclaration of variable 'a'" in e for e in merged.errors)


ALL_PROGRAMS = [FOLD_PROGRAM, IDENTITY_PROGRAM, STRENGTH_PROGRAM,
                CONST_PROP_PROGRAM, INVALIDATION_PROGRAM, PRUNE_IF_PROGRAM,
                PRUNE_WHILE_PROGRAM, RUNTIME_LOOP_PROGRAM, FLOAT_PROGRAM]


@pytest.mark.parametrize("src", ALL_PROGRAMS, ids=lambda p: p.strip()[:20])
def test_differential_equivalence(src):
    conv = conventional_pipeline(src)
    merged = merged_pipeline(src)
    assert conv.checks_ok, conv.errors
    assert merged.checks_ok, merged.errors
    assert runs_match(conv.ir_text, merged.ir_text)


class TestSimplifyingBuilderUnit:
    """Direct builder tests: strength reduction needs runtime values."""

    def _make(self):
        module = ir.Module()
        fnty = ir.FunctionType(ir.IntType(32), [ir.IntType(32)])
        func = ir.Function(module, fnty, "f")
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)
        sb = SimplifyingIRBuilder(builder, ir.IntType(32), ir.FloatType())
        return sb, builder, func

    def test_mul_pow2_becomes_shl(self):
        sb, builder, func = self._make()
        value = sb.mul(func.args[0], ir.Constant(ir.IntType(32), 8))
        builder.ret(value)
        assert "shl" in str(func)
        assert sb.strength_reductions == 1

    def test_mul_nonpow2_stays_mul(self):
        sb, builder, func = self._make()
        value = sb.mul(func.args[0], ir.Constant(ir.IntType(32), 7))
        builder.ret(value)
        assert "mul" in str(func)
        assert sb.strength_reductions == 0

    def test_division_truncates_toward_zero(self):
        sb, _, _ = self._make()
        a = ir.Constant(ir.IntType(32), -7)
        b = ir.Constant(ir.IntType(32), 2)
        result = sb.sdiv(a, b)
        assert result.constant == -3  # C semantics; Python floor would be -4

    def test_div_by_zero_not_folded(self):
        sb, builder, func = self._make()
        value = sb.sdiv(func.args[0], ir.Constant(ir.IntType(32), 0))
        builder.ret(value)
        assert "sdiv" in str(func)
        assert sb.folded == 0

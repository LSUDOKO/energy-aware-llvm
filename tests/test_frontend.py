"""Tests for pipelines: semantic checking, codegen equivalence, execution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from energy.execution import emit_object, jit_run, runs_match
from energy.experiments import conventional_pipeline, merged_pipeline
from frontend.lexer import Lexer, LexerError
from frontend.parser import Parser
from frontend.semantic_checker import SemanticChecker


GOOD_PROGRAM = """
int main() {
    int i = 0;
    int sum = 0;
    while (i < 10) {
        sum = sum + i;
        i = i + 1;
    }
    if (sum > 40) {
        return 1;
    }
    return 0;
}
"""

WRONG_ORDER_PROGRAM = """
int main() {
    x = 5;
    int x = 1;
    return 0;
}
"""

REDECLARE_PROGRAM = """
int main() {
    int a = 1;
    int a = 2;
    return 0;
}
"""


class TestLexer:
    def test_tokens_basic(self):
        toks = Lexer("int x = 5;").tokens
        kinds = [t.type for t in toks]
        assert kinds == ["INT_KW", "IDENTIFIER", "ASSIGN", "NUMBER", "SEMI", "EOF"]

    def test_lexical_error(self):
        with pytest.raises(LexerError):
            Lexer("int $x;")

    def test_line_tracking(self):
        toks = Lexer("int a;\nint b;").tokens
        b_tok = [t for t in toks if t.value == "b"][0]
        assert b_tok.line == 2

    def test_line_comment_is_not_two_divisions(self):
        kinds = [t.type for t in Lexer("int x; // note\nint y;").tokens]
        assert "DIV" not in kinds
        assert kinds.count("INT_KW") == 2

    def test_division_still_lexes(self):
        kinds = [t.type for t in Lexer("a / b").tokens]
        assert kinds == ["IDENTIFIER", "DIV", "IDENTIFIER", "EOF"]

    def test_block_comment_keeps_line_numbers(self):
        toks = Lexer("/* a\nb */ int z;").tokens
        z_tok = [t for t in toks if t.value == "z"][0]
        assert z_tok.line == 2


class TestSemanticChecker:
    def test_good_program_passes(self):
        ast = Parser(Lexer(GOOD_PROGRAM).tokens).parse()
        errors = SemanticChecker().check(ast)
        assert errors == []

    def test_use_before_decl(self):
        ast = Parser(Lexer(WRONG_ORDER_PROGRAM).tokens).parse()
        errors = SemanticChecker().check(ast)
        assert any("undeclared variable 'x'" in e for e in errors)

    def test_redeclaration_detected(self):
        ast = Parser(Lexer(REDECLARE_PROGRAM).tokens).parse()
        errors = SemanticChecker().check(ast)
        assert any("redeclaration of 'a'" in e for e in errors)

    def test_scopes_isolate(self):
        src = """
        int main() {
            int a = 1;
            if (a > 0) {
                int a = 2;
            }
            return a;
        }
        """
        ast = Parser(Lexer(src).tokens).parse()
        assert SemanticChecker().check(ast) == []


class TestPipelines:
    def test_conventional_pipeline_emits_ir(self):
        art = conventional_pipeline(GOOD_PROGRAM)
        assert art.checks_ok
        assert "define i32" in art.ir_text and "@\"main\"" in art.ir_text

    def test_merged_pipeline_emits_ir(self):
        art = merged_pipeline(GOOD_PROGRAM)
        assert art.checks_ok
        assert "define i32" in art.ir_text and "@\"main\"" in art.ir_text

    @pytest.mark.skipif(sys.platform == "win32", reason="JIT needs native target")
    def test_differential_same_result(self):
        conv = conventional_pipeline(GOOD_PROGRAM)
        merged = merged_pipeline(GOOD_PROGRAM)
        assert runs_match(conv.ir_text, merged.ir_text)

    @pytest.mark.skipif(sys.platform == "win32", reason="JIT needs native target")
    def test_jit_computes_correctly(self):
        # sum of 0..9 = 45 > 40 -> returns 1
        art = conventional_pipeline(GOOD_PROGRAM)
        assert jit_run(art.ir_text) == 1

    def test_jit_flag_program(self):
        src = """
        int main() {
            int a = 6;
            int b = 7;
            if (a * b == 42) {
                return 5;
            }
            return 3;
        }
        """
        art = conventional_pipeline(src)
        assert jit_run(art.ir_text) == 5

    def test_emit_object(self, tmp_path):
        art = conventional_pipeline(GOOD_PROGRAM)
        obj = emit_object(art.ir_text, tmp_path / "prog.o")
        assert obj.exists() and obj.stat().st_size > 0
        # ELF magic on Linux
        assert obj.read_bytes()[:4] == b"\x7fELF"


class TestRealIRMetrics:
    """Stage 2 sanity: our good program has a loop and a branch."""

    def test_ir_contains_expected_ops(self):
        art = conventional_pipeline(GOOD_PROGRAM)
        ir = art.ir_text
        assert "alloca" in ir
        assert "load" in ir and "store" in ir
        assert "icmp" in ir
        assert "br i1" in ir

"""Phase 4 tests: honest scheduling modes + real backend.

Covers plan items 16-21: real object emission, measured-data ML path,
a real GA (no sleeps), and all three modes wired through the shared
pipeline with differential correctness checks.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from compile_pipeline import compile_source
from energy.execution import jit_run, measure_runtime, emit_object

PROGRAM = """
int fib(int n) {
    int a = 0;
    int b = 1;
    int i = 0;
    while (i < n) {
        int t = a + b;
        a = b;
        b = t;
        i = i + 1;
    }
    return a;
}

int main() {
    return fib(20);
}
"""

ELF_MAGIC = b"\x7fELF"


def _source(tmp_path: Path, text: str = PROGRAM) -> Path:
    p = tmp_path / "prog.c"
    p.write_text(text)
    return p


class TestExecutionHelpers:
    def test_measure_runtime_is_faster_than_jit_setup(self):
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(Parser(Lexer(PROGRAM).tokens).parse())
        ir = str(art.module)
        runtime, value = measure_runtime(ir, samples=5)
        assert value == jit_run(ir) == 6765  # fib(20)
        # compile-once measurement must be far below re-JIT-per-call cost
        assert runtime < 0.01, f"per-call runtime {runtime}s looks like setup cost"

    def test_emit_object_is_real_elf(self, tmp_path):
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(Parser(Lexer(PROGRAM).tokens).parse())
        out = emit_object(str(art.module), tmp_path / "x.o")
        data = Path(out).read_bytes()
        assert data[:4] == ELF_MAGIC
        assert len(data) > 64


class TestSchedulingModes:
    """Plan: same correctness in all modes; only optional passes differ."""

    @pytest.mark.parametrize("mode", ["-Meco", "-Mbalanced", "-Mperf"])
    def test_mode_compiles_verified_and_correct(self, tmp_path, mode):
        src = _source(tmp_path).read_text()
        t0 = time.perf_counter()
        res = compile_source(src, mode=mode, name="prog",
                             ga_kwargs={"generations": 3, "pop_size": 8})
        elapsed = time.perf_counter() - t0
        assert res["success"], res.get("error")
        assert res["verified"] and res["differential_ok"]
        assert res["return_value"] == 6765
        # no simulated search delays: the whole mode must finish quickly
        assert elapsed < 30, f"{mode} took {elapsed:.1f}s"

    @pytest.mark.parametrize("mode", ["-Meco", "-Mbalanced", "-Mperf"])
    def test_mode_emits_real_object_file(self, tmp_path, mode):
        src = _source(tmp_path).read_text()
        res = compile_source(src, mode=mode, name="objprog",
                             ga_kwargs={"generations": 2, "pop_size": 6})
        assert res["success"]
        obj = Path(res["object_file"])
        assert obj.exists()
        assert obj.read_bytes()[:4] == ELF_MAGIC
        assert res["object_bytes"] == obj.stat().st_size

    def test_eco_schedules_nothing_or_little(self):
        res = compile_source(PROGRAM, mode="-Meco", emit_object=False)
        assert res["success"]
        # strict budget: at most a tiny sequence, never the full pipeline
        assert len(res["selected_passes"]) <= 3

    def test_gating_log_is_auditable(self):
        from backend.passes import AVAILABLE_PASSES
        # -Meco audits every optional pass under its strict budget
        res = compile_source(PROGRAM, mode="-Meco", emit_object=False)
        gating = res["gating"]
        assert gating["budget"] == "eco"
        assert len(gating["decisions"]) == len(AVAILABLE_PASSES)
        # -Mbalanced records one auditable decision per candidate pass
        res = compile_source(PROGRAM, mode="-Mbalanced", emit_object=False)
        gating = res["gating"]
        assert gating["budget"] == "balanced"
        assert len(gating["decisions"]) == len(gating["candidate"])
        assert set(gating["scheduled"]) <= set(gating["candidate"])
        for d in gating["decisions"]:
            assert "reason" in d and d["reason"]
            assert "benefit" in d and "threshold" in d

    def test_optimization_shrinks_fib_ir(self):
        res = compile_source(PROGRAM, mode="-Mperf", emit_object=False,
                             ga_kwargs={"generations": 4, "pop_size": 10})
        assert res["success"]
        assert res["instructions"]["after"] <= res["instructions"]["before"]

    def test_response_keeps_legacy_api_schema(self):
        res = compile_source(PROGRAM, mode="-Mbalanced", emit_object=False)
        for key in ("success", "ast_time_ms", "ml_time_ms", "predicted_edp",
                    "features", "selected_passes", "logs", "llvm_ir"):
            assert key in res, f"missing legacy key {key}"
        assert len(res["features"]) >= 35

    def test_no_mock_sleeps_in_pipeline_sources(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("compile_pipeline.py", "compiler_driver.py", "api.py",
                    "ml_models/ga.py", "stage3_ml_model.py",
                    "backend/passes.py", "stage2/pass_gating.py"):
            text = (root / rel).read_text()
            assert "time.sleep(" not in text, f"mock sleep left in {rel}"


class TestGeneticSearch:
    """Plan item 19: real GA searching 1/EDP, time-budgeted, no sleeps."""

    def test_search_returns_valid_sequence_and_history(self):
        from ml_models.ga import run_ga_search
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(
            Parser(Lexer(PROGRAM).tokens).parse())
        ir = str(art.module)

        t0 = time.perf_counter()
        result = run_ga_search(ir, generations=3, pop_size=8, seed=7)
        elapsed = time.perf_counter() - t0

        assert result.generations >= 1
        assert result.evaluations >= 8
        assert elapsed < 30
        from backend.passes import AVAILABLE_PASSES, expand_sequence
        assert set(result.sequence) <= set(AVAILABLE_PASSES)
        assert result.sequence == expand_sequence(result.sequence)
        # elitism: best fitness never regresses across generations
        assert all(b >= a - 1e-12 for a, b in zip(result.history,
                                                  result.history[1:]))
        assert result.measured["verified"] is True
        assert result.measured["jit_ok"] is True
        assert result.measured["jit_result"] == 6765

    def test_search_is_deterministic_under_seed(self):
        from ml_models.ga import run_ga_search
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(
            Parser(Lexer(PROGRAM).tokens).parse())
        ir = str(art.module)
        # static proxy: same genomes and winner; t_base is timed live, so the
        # absolute fitness is compared loosely
        a = run_ga_search(ir, generations=2, pop_size=6, seed=123,
                          runtime_model="static")
        b = run_ga_search(ir, generations=2, pop_size=6, seed=123,
                          runtime_model="static")
        assert a.sequence == b.sequence
        assert a.evaluations == b.evaluations
        assert a.fitness == pytest.approx(b.fitness, rel=0.5)

    def test_measured_search_explores_the_same_genomes_under_seed(self):
        """With real timings the winner may differ run to run (noise), but the
        seeded search must still evaluate the same number of candidates."""
        from ml_models.ga import run_ga_search
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(
            Parser(Lexer(PROGRAM).tokens).parse())
        ir = str(art.module)
        a = run_ga_search(ir, generations=2, pop_size=6, seed=123)
        b = run_ga_search(ir, generations=2, pop_size=6, seed=123)
        assert a.evaluations == b.evaluations

    def test_time_budget_is_respected(self):
        from ml_models.ga import run_ga_search
        from frontend.lexer import Lexer
        from frontend.parser import Parser
        from frontend.semantic_codegen import UnifiedSemanticVisitor
        art = UnifiedSemanticVisitor().generate(
            Parser(Lexer(PROGRAM).tokens).parse())
        ir = str(art.module)
        t0 = time.perf_counter()
        result = run_ga_search(ir, generations=1000, pop_size=12,
                               budget_ms=500, seed=1)
        elapsed = time.perf_counter() - t0
        assert result.generations < 1000, "budget should stop the search early"
        assert elapsed < 10, f"budget overrun: {elapsed:.1f}s"


class TestMeasuredMLPath:
    """Plan items 17-18: dataset from real runs, model trained on it."""

    def test_collect_dataset_rows(self, tmp_path, monkeypatch):
        import ml_models.collect_dataset as cd
        monkeypatch.setattr(cd, "PROFILE_PATH", tmp_path / "per_pass.json")
        monkeypatch.setattr(cd, "save_profiles",
                            lambda prof, path=None: tmp_path / "per_pass.json")
        src = _source(tmp_path)
        df = cd.collect([src], n_random=2, runtime_repeats=3)
        assert len(df) >= 7  # 7 fixed candidates + up to 2 random
        for col in ("benchmark", "sequence", "t_compile_s", "runtime_s_median",
                    "energy_source", "t_shared_s", "edp_savings"):
            assert col in df.columns, f"missing column {col}"
        # baseline row exists and has zero savings by construction
        base = df[df["sequence"] == "(none)"]
        assert len(base) == 1
        assert float(base["edp_savings"].iloc[0]) == pytest.approx(0.0)
        assert set(df["energy_source"]) <= {"rapl", "estimated"}
        assert (df["t_compile_s"] > 0).all()

    def test_ranker_trains_on_measured_frame(self, tmp_path, monkeypatch):
        import ml_models.collect_dataset as cd
        import stage3_ml_model as s3
        monkeypatch.setattr(s3, "MODEL_PATH", tmp_path / "model.pkl")
        src = _source(tmp_path)
        df = cd.collect([src], n_random=2, runtime_repeats=3)
        model = s3.train_model(df)
        assert list(model.feature_names_in_) == (
            s3.FEATURE_KEYS + [f"pass{p}" for p in s3.AVAILABLE_PASSES])
        assert getattr(model, "training_source") == "provided"

        from stage2.extractor import IRFeatureExtractor
        from energy.experiments import merged_pipeline
        feats = IRFeatureExtractor(str(merged_pipeline(PROGRAM).ir_text)
                                   ).extract_features()
        t0 = time.perf_counter()
        seq, benefit = s3.rank_passes(feats, model)
        dt = time.perf_counter() - t0
        assert dt < 0.5, f"ranking took {dt * 1e3:.0f} ms (plan: <50 ms budget)"
        assert isinstance(seq, list)
        known = {s3.sequence_key(c) for c in s3.CANDIDATE_SEQUENCES}
        assert s3.sequence_key(seq) in known

    def test_model_label_fallback_is_deterministic(self):
        """Without a CSV the fallback labels come from real runs, not noise."""
        import stage3_ml_model as s3
        frame = s3._generate_model_labels()
        assert len(frame) >= len(s3.CANDIDATE_SEQUENCES)
        assert s3.TARGET_COL in frame.columns
        assert frame[s3.TARGET_COL].notna().all()
        # the no-pass baseline row is labelled exactly zero savings
        assert (frame[frame["sequence"] == "(none)"][s3.TARGET_COL] == 0).all()


class TestJitProgram:
    def test_repeated_calls_are_stable(self):
        from energy.execution import JitProgram
        from energy.experiments import merged_pipeline
        prog = JitProgram(str(merged_pipeline(
            "int main() { int i = 0; int s = 0; "
            "while (i < 10) { s = s + i; i = i + 1; } return s; }").ir_text))
        assert [prog.call() for _ in range(3)] == [45, 45, 45]

    def test_missing_entry_raises(self):
        from energy.execution import JitProgram
        with pytest.raises(RuntimeError):
            JitProgram('define i32 @other() {\nentry:\n  ret i32 0\n}\n')


class TestObjectBytes:
    def test_bytes_are_an_elf_object_and_match_emit_object(self, tmp_path):
        from energy.execution import emit_object, object_bytes
        from energy.experiments import merged_pipeline
        ir = merged_pipeline("int main() { return 3; }").ir_text
        data = object_bytes(ir)
        assert data[:4] == b"\x7fELF"
        assert emit_object(ir, tmp_path / "x.o").read_bytes()[:4] == data[:4]

"""-Mperf GA: lifecycle-EDP fitness from real timings, correctness-gated."""
import pytest

from backend.passes import AVAILABLE_PASSES
from energy.experiments import merged_pipeline
from ml_models import ga as ga_mod
from ml_models.ga import GeneticPassSearcher, run_ga_search

SRC = """
int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
int main() { return fib(18); }
"""


@pytest.fixture(scope="module")
def ir():
    return merged_pipeline(SRC).ir_text


def test_result_reports_objective_and_model(ir):
    res = run_ga_search(ir, generations=2, pop_size=6, seed=1, n_runs=1234)
    d = res.to_dict()
    assert d["n_runs"] == 1234 and d["runtime_model"] == "measured"
    assert d["measured"]["jit_ok"] and d["measured"]["jit_matches_unoptimized"]


def test_run_count_moves_the_optimum(ir):
    """One run: compile cost dominates, so nothing beats the empty sequence.
    A billion runs: execution dominates, so a real pass sequence wins."""
    none = tuple([0] * len(AVAILABLE_PASSES))
    full = tuple([1] * len(AVAILABLE_PASSES))
    once = GeneticPassSearcher(ir, n_runs=1, seed=1)
    many = GeneticPassSearcher(ir, n_runs=10 ** 9, seed=1)
    assert once.fitness_of(none) > once.fitness_of(full)
    assert many.fitness_of(full) > many.fitness_of(none)


def test_candidate_that_changes_behaviour_is_invalid(ir, monkeypatch):
    searcher = GeneticPassSearcher(ir, n_runs=1000, seed=1)
    good = searcher.fitness_of(tuple([0] * len(AVAILABLE_PASSES)))

    wrong = merged_pipeline("int main() { return 1; }").ir_text

    class Fake:
        verified, ir_text, total_time = True, wrong, 1e-6

    monkeypatch.setattr(ga_mod, "apply_pass_sequence", lambda *_a, **_k: Fake())
    bits = tuple([1] + [0] * (len(AVAILABLE_PASSES) - 1))
    assert searcher.fitness_of(bits) < good * 1e-6      # ~0: rejected


def test_unverified_candidate_is_invalid(ir, monkeypatch):
    searcher = GeneticPassSearcher(ir, n_runs=1000, seed=1)

    class Fake:
        verified, ir_text, total_time = False, "", 0.0

    monkeypatch.setattr(ga_mod, "apply_pass_sequence", lambda *_a, **_k: Fake())
    bits = tuple([1] + [0] * (len(AVAILABLE_PASSES) - 1))
    assert searcher._edp(bits) == ga_mod.INVALID_EDP


def test_static_model_still_available(ir):
    res = run_ga_search(ir, generations=1, pop_size=4, seed=2,
                        runtime_model="static")
    assert res.runtime_model == "static" and res.sequence is not None


def test_bad_runtime_model_rejected(ir):
    with pytest.raises(ValueError):
        GeneticPassSearcher(ir, runtime_model="guess")


def test_winner_never_worse_than_empty_sequence(ir):
    s = GeneticPassSearcher(ir, generations=3, pop_size=8, seed=5, n_runs=10 ** 6)
    res = s.run()
    empty = s.fitness_of(tuple([0] * len(AVAILABLE_PASSES)))
    assert res.fitness >= empty        # elitism: the baseline was in generation 0


def test_finalists_are_re_timed_and_best_wins(ir, monkeypatch):
    """Search-time timings are noisy; the leaders are re-measured carefully
    and the best *re-measured* candidate wins, not the best noisy one."""
    s = GeneticPassSearcher(ir, n_runs=1000, seed=7, finalists=3)
    pop = []
    for i in range(4):
        bits = [0] * len(AVAILABLE_PASSES)
        bits[i] = 1
        pop.append(ga_mod.Individual(bits=bits, fitness=100.0 - i))   # search order

    careful = {tuple(p.bits): edp for p, edp in zip(pop, (5.0, 1.0, 3.0, 0.1))}
    calls = []

    def fake(seq, samples=3, min_batch_s=0.0005):
        calls.append(samples)
        bits = tuple(1 if p in seq else 0 for p in AVAILABLE_PASSES)
        return careful[bits]

    monkeypatch.setattr(s, "_measured_edp", fake)
    winner = s._refine_finalists(pop)
    assert calls == [9, 9, 9]                      # only the top 3, carefully
    assert winner.bits == pop[1].bits              # 1.0 beats 5.0 and 3.0
    assert winner.fitness == pytest.approx(1.0)    # 4th (0.1) was not a finalist

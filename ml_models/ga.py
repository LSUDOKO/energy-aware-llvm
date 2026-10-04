"""-Mperf: real genetic-algorithm search over pass sequences.

Implements plan Stage 3, mode 3 — *no* ``time.sleep`` simulation.  The GA
searches subsets of the atomic pass set (canonical order), where each
candidate fitness evaluation actually runs LLVM passes on the module and
(by default) JIT-executes and times the result:

    fitness(seq) = 1 / EDP_lifecycle(seq, n_runs)
    EDP(n)       = (E_c + n E_r) x (T_c + n T_r)      (energy/lifecycle.py)
    T_c          = parse/verify base + measured wall time of the passes
    T_r          = measured native time of main() (compile once, call many)
    E            = P-hat x T    (the package power from the profile)

A candidate whose ``main()`` returns a different value than the unoptimized
module is *invalid* (fitness ~0), so the search can never trade correctness
for speed.  ``runtime_model="static"`` keeps the old cheaper proxy
(``static_cost x run_s_per_unit``) for tests and for machines where timing
every candidate is too slow.

Search: tournament selection + uniform crossover + bit-flip mutation +
elitism, deterministic under ``seed``, terminated by generation count or a
wall-clock budget.  The winner is re-measured for real (full sequence run,
JIT execution + optional object emission) and reported separately from the
search fitness, per plan step "predicted during search, measured for the
winner".
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from backend.passes import AVAILABLE_PASSES, apply_pass_sequence
from energy.lifecycle import Cost
from stage2.extractor import static_cost, count_instructions
from stage2.pass_gating import load_profiles, DEFAULT_POWER_W

# A tiny fixed slice of base compile time (parse + verify of the module)
# attributed to every compilation, measured live on first use.
_DEFAULT_RUN_TIME_PER_UNIT = 1e-6  # s of program runtime per static-cost unit
INVALID_EDP = 1e30                 # fitness ~0: failed verification/semantics
DEFAULT_RUNS = 10_000              # executions assumed for the lifecycle EDP
PERF_RUNS = 10_000_000             # -Mperf optimizes for a hot, long-lived program


@dataclass
class Individual:
    bits: list[int]
    fitness: float = -1.0  # higher = better (1/EDP)

    @property
    def sequence(self) -> list[str]:
        return [p for p, b in zip(AVAILABLE_PASSES, self.bits) if b]


@dataclass
class GAResult:
    sequence: list[str]
    fitness: float
    edp: float
    generations: int
    evaluations: int
    elapsed_s: float
    history: list[float] = field(default_factory=list)  # best fitness/gen
    measured: dict = field(default_factory=dict)        # winner re-measured
    n_runs: int = DEFAULT_RUNS
    runtime_model: str = "measured"

    def to_dict(self) -> dict:
        return {
            "sequence": list(self.sequence),
            "fitness": self.fitness,
            "edp": self.edp,
            "generations": self.generations,
            "evaluations": self.evaluations,
            "elapsed_s": self.elapsed_s,
            "history": list(self.history),
            "measured": dict(self.measured),
            "n_runs": self.n_runs,
            "runtime_model": self.runtime_model,
        }


class GeneticPassSearcher:
    """GA over pass-sequence bit masks (plan: optimise 1/EDP)."""

    def __init__(
        self,
        ir_text: str,
        pop_size: int = 16,
        generations: int = 8,
        mutation_rate: float = 0.15,
        crossover_rate: float = 0.7,
        tournament_k: int = 3,
        elite: int = 2,
        budget_ms: float | None = None,
        seed: int = 42,
        power_w: float | None = None,
        profiles: dict | None = None,
        n_runs: int = PERF_RUNS,
        runtime_model: str = "measured",
        finalists: int = 3,
    ):
        if runtime_model not in ("measured", "static"):
            raise ValueError("runtime_model must be 'measured' or 'static'")
        self.n_runs = n_runs
        self.runtime_model = runtime_model
        self.finalists = finalists
        self.ir_text = ir_text
        self.pop_size = pop_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_k = tournament_k
        self.elite = elite
        self.budget_ms = budget_ms
        self.rng = random.Random(seed)

        self.profiles = profiles if profiles is not None else load_profiles()
        meta = self.profiles.get("meta", {}) if isinstance(self.profiles, dict) else {}
        self.power_w = power_w if power_w is not None else float(
            meta.get("power_w", DEFAULT_POWER_W))
        self.run_s_per_unit = float(meta.get("run_s_per_unit",
                                             _DEFAULT_RUN_TIME_PER_UNIT))

        # real measured base cost: parse+verify the module once
        t0 = time.perf_counter()
        self.base_ir = ir_text
        self.c_before = static_cost(ir_text)
        self._parse(ir_text)
        self.t_base = max(time.perf_counter() - t0, 1e-6)
        self._cache: dict[tuple, float] = {}
        self.base_value: int | None = None
        if runtime_model == "measured":
            from energy.execution import jit_run
            self.base_value = jit_run(ir_text)   # semantics every candidate must keep

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _parse(ir_text: str) -> None:
        from llvmlite import binding
        binding.parse_assembly(ir_text).verify()

    def _pass_time(self, name: str) -> float:
        prof = (self.profiles.get("passes", {}) or {}).get(name, {})
        if "t_seconds" in prof:
            return float(prof["t_seconds"])
        # measure live, then memoise for this searcher instance
        from stage2.pass_gating import measure_pass_cost
        t, _ = measure_pass_cost(self.ir_text, name, repeats=3)
        return t

    def _compile_time(self, sequence: list[str]) -> float:
        return self.t_base + sum(self._pass_time(p) for p in sequence)

    def _measured_edp(self, seq: list[str], samples: int = 3,
                      min_batch_s: float = 0.0005) -> float:
        """Lifecycle EDP from a real pass run and a real native timing."""
        from energy.execution import measure_runtime
        if seq:
            result = apply_pass_sequence(self.ir_text, seq)
            if not result.verified:
                return INVALID_EDP
            opt_ir = result.ir_text
            t_compile = self.t_base + result.total_time
        else:
            opt_ir, t_compile = self.ir_text, self.t_base
        try:
            t_run, value = measure_runtime(opt_ir, samples=samples,
                                           min_batch_s=min_batch_s)
        except RuntimeError:
            return INVALID_EDP
        if value != self.base_value:
            return INVALID_EDP           # optimization changed behaviour
        cost = Cost(self.power_w * t_compile, t_compile,
                    self.power_w * t_run, t_run)
        return max(cost.edp(self.n_runs), 1e-30)

    def _edp(self, bits: tuple[int, ...]) -> float:
        if bits in self._cache:
            return self._cache[bits]
        seq = [p for p, b in zip(AVAILABLE_PASSES, bits) if b]
        if self.runtime_model == "measured":
            edp = self._measured_edp(seq)
            self._cache[bits] = edp
            return edp
        if not seq:
            cost_after = self.c_before
            t_compile = self.t_base
        else:
            key = bits
            if key in self._cache:
                return self._cache[key]
            result = apply_pass_sequence(self.ir_text, seq)
            cost_after = static_cost(result.ir_text)
            t_compile = self._compile_time(seq)
        e_compile = self.power_w * t_compile
        t_run = cost_after * self.run_s_per_unit
        edp = max(e_compile * t_run, 1e-30)
        self._cache[bits] = edp
        return edp

    def fitness_of(self, bits: tuple[int, ...]) -> float:
        return 1.0 / self._edp(bits)

    # -- GA machinery --------------------------------------------------------
    def _individual(self) -> Individual:
        # bias towards moderate-sized sequences (not empty, not everything)
        bits = [1 if self.rng.random() < 0.4 else 0
                for _ in AVAILABLE_PASSES]
        if not any(bits):
            bits[self.rng.randrange(len(bits))] = 1
        return Individual(bits=bits)

    def _tournament(self, pop: list[Individual]) -> Individual:
        contenders = [self.rng.choice(pop) for _ in range(self.tournament_k)]
        return max(contenders, key=lambda ind: ind.fitness)

    def _crossover(self, a: Individual, b: Individual) -> tuple[list, list]:
        if self.rng.random() > self.crossover_rate:
            return list(a.bits), list(b.bits)
        ca, cb = list(a.bits), list(b.bits)
        for i in range(len(ca)):
            if self.rng.random() < 0.5:
                ca[i], cb[i] = cb[i], ca[i]
        return ca, cb

    def _mutate(self, bits: list) -> list:
        return [1 - b if self.rng.random() < self.mutation_rate else b
                for b in bits]

    def run(self) -> GAResult:
        t_start = time.perf_counter()
        pop = [self._individual() for _ in range(self.pop_size)]
        # always evaluate the empty sequence (baseline) and the kitchen sink
        pop[0] = Individual(bits=[0] * len(AVAILABLE_PASSES))
        pop[1] = Individual(bits=[1] * len(AVAILABLE_PASSES))

        for ind in pop:
            ind.fitness = self.fitness_of(tuple(ind.bits))

        history: list[float] = []
        evaluations = len(pop)
        gen_done = 0

        for gen in range(self.generations):
            if self.budget_ms is not None and \
                    (time.perf_counter() - t_start) * 1000 >= self.budget_ms:
                break
            pop.sort(key=lambda i: i.fitness, reverse=True)
            best = pop[0].fitness
            history.append(best)

            nxt = [Individual(bits=list(pop[i].bits), fitness=pop[i].fitness)
                   for i in range(min(self.elite, len(pop)))]
            while len(nxt) < self.pop_size:
                pa = self._tournament(pop)
                pb = self._tournament(pop)
                ca, cb = self._crossover(pa, pb)
                for bits in (self._mutate(ca), self._mutate(cb)):
                    if len(nxt) >= self.pop_size:
                        break
                    ind = Individual(bits=bits)
                    ind.fitness = self.fitness_of(tuple(ind.bits))
                    evaluations += 1
                    nxt.append(ind)
            pop = nxt
            gen_done = gen + 1

        pop.sort(key=lambda i: i.fitness, reverse=True)
        history.append(pop[0].fitness)           # search-time scale (elitist)
        winner = self._refine_finalists(pop)     # careful re-timing: own scale
        elapsed = time.perf_counter() - t_start

        # ---- re-measure the winner for real (plan: measured for the winner)
        measured = self._measure_winner(winner.sequence)

        return GAResult(
            sequence=winner.sequence,
            fitness=winner.fitness,
            edp=1.0 / winner.fitness if winner.fitness > 0 else float("inf"),
            generations=gen_done,
            evaluations=evaluations,
            elapsed_s=elapsed,
            history=history,
            measured=measured,
            n_runs=self.n_runs,
            runtime_model=self.runtime_model,
        )

    def _refine_finalists(self, pop: list[Individual]) -> Individual:
        """Re-time the top candidates with a larger sample and keep the best.

        Search-time timings use few samples to stay cheap, so near-ties among
        the leaders are decided by noise.  The finalists are re-measured
        carefully before one is declared the winner.
        """
        if self.runtime_model != "measured" or self.finalists < 2:
            return pop[0]
        seen, finalists = set(), []
        for ind in pop:
            key = tuple(ind.bits)
            if key not in seen:
                seen.add(key)
                finalists.append(ind)
            if len(finalists) == self.finalists:
                break
        best, best_edp = finalists[0], float("inf")
        for ind in finalists:
            edp = self._measured_edp(ind.sequence, samples=9, min_batch_s=0.002)
            ind.fitness = 1.0 / edp
            if edp < best_edp:
                best, best_edp = ind, edp
        return best

    def _measure_winner(self, sequence: list[str]) -> dict:
        """Actually run the winning sequence + JIT execution."""
        out: dict = {}
        if sequence:
            result = apply_pass_sequence(self.ir_text, sequence)
            out["verified"] = result.verified
            out["compile_time_s"] = result.total_time
            out["instructions_before"] = result.instructions_before
            out["instructions_after"] = result.instructions_after
            opt_ir = result.ir_text
        else:
            out["verified"] = True
            out["compile_time_s"] = self.t_base
            out["instructions_before"] = count_instructions(self.ir_text)
            out["instructions_after"] = out["instructions_before"]
            opt_ir = self.ir_text

        # real JIT execution timing (compile once, measure many) + correctness
        from energy.execution import jit_run, measure_runtime
        try:
            runtime_s, value = measure_runtime(opt_ir, samples=5)
            out["jit_result"] = value
            out["runtime_s_median"] = runtime_s
            out["jit_ok"] = True
            out["jit_matches_unoptimized"] = (jit_run(opt_ir) == jit_run(self.ir_text))
        except RuntimeError as exc:
            out["jit_ok"] = False
            out["jit_error"] = str(exc)

        out["static_cost_after"] = static_cost(opt_ir)
        out["power_w"] = self.power_w
        if "compile_time_s" in out:
            out["energy_compile_j_est"] = self.power_w * out["compile_time_s"]
        if out.get("jit_ok"):
            out["edp_measured"] = (out.get("energy_compile_j_est", 0.0)
                                   * out["runtime_s_median"])
        out["opt_ir"] = opt_ir
        return out


def run_ga_search(ir_text: str, **kwargs) -> GAResult:
    """Convenience wrapper used by the compiler driver (-Mperf)."""
    return GeneticPassSearcher(ir_text, **kwargs).run()

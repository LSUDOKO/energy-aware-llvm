"""Stage 3 backend — real LLVM pass scheduling + object emission.

Everything here uses llvmlite's **new pass manager** (``PassBuilder``), so a
"pass sequence" is actually executed on the module instead of being printed
in a log line:

  * ``apply_pass_sequence(ir_text, names)`` runs each optional pass in
    order, timing every pass individually (these are the real T-hat_p
    inputs for the Stage-2 cost-benefit gate), verifies the result and
    returns the optimized IR text.
  * ``emit_object_file`` writes a genuine relocatable ELF object for the
    host triple; ``jit_execute`` runs ``main`` natively for correctness
    checks, runtime and E_run measurement.

Pass names are the honest llvmlite/LLVM new-PM pass names.  ``-O1/-O2/-O3``
are implemented as *presets* (fixed sequences of the atomic passes) because
the new pass manager API in llvmlite exposes passes individually rather
than as a clang-style pipeline string.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from llvmlite import binding

from energy.execution import emit_object, jit_run  # re-exported helpers

_INITIALIZED = False


def _ensure_init() -> None:
    global _INITIALIZED
    if not _INITIALIZED:
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()
        _INITIALIZED = True


# name -> method on ModulePassManager
ATOMIC_PASSES: dict[str, str] = {
    "-sroa": "add_sroa_pass",
    "-sccp": "add_sccp_pass",
    "-instcombine": "add_instruction_combine_pass",
    "-aggressive-instcombine": "add_aggressive_instcombine_pass",
    "-simplifycfg": "add_simplify_cfg_pass",
    "-gvn": "add_new_gvn_pass",
    "-dse": "add_dead_store_elimination_pass",
    "-dce": "add_dead_code_elimination_pass",
    "-adce": "add_aggressive_dce_pass",
    "-reassociate": "add_reassociate_pass",
    "-lcsr": "add_loop_strength_reduce_pass",
    "-loop-unroll": "add_loop_unroll_pass",
    "-loop-rotate": "add_loop_rotate_pass",
    "-lcssa": "add_lcssa_pass",
    "-tailcallelim": "add_tail_call_elimination_pass",
    "-sinking": "add_sinking_pass",
    "-ipsccp": "add_ipsccp_pass",
    "-constmerge": "add_constant_merge_pass",
    "-globalopt": "add_global_opt_pass",
}

# legacy aliases kept for the driver/API surface
ALIASES = {"-mem2reg": "-sroa"}

# canonical ordering used as the GA genome order
AVAILABLE_PASSES: list[str] = list(ATOMIC_PASSES.keys())

PRESETS: dict[str, list[str]] = {
    "-O1": ["-sroa", "-sccp", "-instcombine", "-simplifycfg", "-gvn", "-dce"],
    "-O2": ["-sroa", "-sccp", "-instcombine", "-simplifycfg", "-gvn", "-dce",
            "-adce", "-dse", "-reassociate", "-lcsr", "-loop-rotate",
            "-lcssa", "-tailcallelim"],
    "-O3": ["-sroa", "-sccp", "-instcombine", "-simplifycfg", "-gvn", "-dce",
            "-adce", "-dse", "-reassociate", "-lcsr", "-loop-rotate",
            "-lcssa", "-tailcallelim", "-aggressive-instcombine",
            "-loop-unroll", "-ipsccp", "-sinking", "-globalopt", "-constmerge"],
}


def expand_sequence(names) -> list[str]:
    """Expand presets/aliases into atomic pass names, preserving order."""
    out: list[str] = []
    for n in names:
        n = ALIASES.get(n, n)
        if n in PRESETS:
            out.extend(PRESETS[n])
        elif n in ATOMIC_PASSES:
            out.append(n)
        elif n in ("-O0",):
            continue  # -O0 == no optional passes
        else:
            raise ValueError(f"unknown pass: {n}")
    # de-duplicate, keep first occurrence order
    seen: set = set()
    return [p for p in out if not (p in seen or seen.add(p))]


@dataclass
class PassRunResult:
    ir_text: str
    pass_times: dict[str, float] = field(default_factory=dict)  # seconds
    total_time: float = 0.0
    verified: bool = True
    instructions_before: int = 0
    instructions_after: int = 0

    def to_dict(self) -> dict:
        return {
            "ir_text": self.ir_text,
            "pass_times": dict(self.pass_times),
            "total_time": self.total_time,
            "verified": self.verified,
            "instructions_before": self.instructions_before,
            "instructions_after": self.instructions_after,
        }


def _parse(ir_text: str) -> binding.ModuleRef:
    mod = binding.parse_assembly(ir_text)
    mod.verify()
    return mod


def apply_pass(ir_text: str, pass_name: str) -> tuple[str, float]:
    """Run exactly one pass; returns (new_ir_text, seconds)."""
    pass_name = ALIASES.get(pass_name, pass_name)
    if pass_name == "-O0":
        return ir_text, 0.0
    if pass_name in PRESETS:
        return _apply_preset(ir_text, pass_name)
    if pass_name not in ATOMIC_PASSES:
        raise ValueError(f"unknown pass: {pass_name}")

    _ensure_init()
    tm = binding.Target.from_default_triple().create_target_machine()
    mod = _parse(ir_text)
    pto = binding.PipelineTuningOptions()
    pto.speed_level = 0
    pb = binding.PassBuilder(tm, pto)
    mpm = pb.getModulePassManager()
    getattr(mpm, ATOMIC_PASSES[pass_name])()
    t0 = time.perf_counter()
    mpm.run(mod, pb)
    elapsed = time.perf_counter() - t0
    mod.verify()
    return str(mod), elapsed


def _apply_preset(ir_text: str, preset: str) -> tuple[str, float]:
    text = ir_text
    total = 0.0
    for p in PRESETS[preset]:
        text, dt = apply_pass(text, p)
        total += dt
    return text, total


def apply_pass_sequence(ir_text: str, pass_names) -> PassRunResult:
    """Run a full sequence, timing each pass individually."""
    _ensure_init()
    t_start = time.perf_counter()
    before = _count(ir_text)
    text = ir_text
    times: dict[str, float] = {}
    for name in expand_sequence(pass_names):
        text, dt = apply_pass(text, name)
        # keep only the first timing of a repeated pass name in the log
        times[name] = times.get(name, 0.0) + dt
    after = _count(text)
    total = time.perf_counter() - t_start
    try:
        _parse(text)
        verified = True
    except RuntimeError:
        verified = False
    return PassRunResult(
        ir_text=text, pass_times=times, total_time=total, verified=verified,
        instructions_before=before, instructions_after=after,
    )


def _count(ir_text: str) -> int:
    from stage2.extractor import count_instructions
    return count_instructions(ir_text)


def emit_object_file(ir_text: str, path: str):
    """Write a real relocatable object file for the host triple."""
    return emit_object(ir_text, path)


def jit_execute(ir_text: str, entry: str = "main") -> int:
    """Compile + run natively (correctness / runtime / E_run measurements)."""
    return jit_run(ir_text, entry=entry)

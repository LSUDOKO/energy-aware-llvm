"""Program execution utilities.

Bridges the gap the current codebase mocks away ("object file generated:
a.out (simulated)"):

  * jit_run(ir_text)  - compile LLVM IR text with MCJIT and execute `main`
                        natively, returning its i32 result. Used for
                        correctness checks and for measuring E_run later.
  * emit_object(ir_text, path) - emit a real relocatable object file for
                        the host triple.

Requires llvmlite's native target initialization (done lazily here).
"""
from __future__ import annotations

import ctypes
import time
from pathlib import Path

from llvmlite import binding as llvm

_initialized = False


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        _initialized = True


def _parse_and_verify(ir_text: str) -> llvm.ModuleRef:
    _ensure_init()
    mod = llvm.parse_assembly(ir_text)
    mod.verify()
    return mod


def jit_run(ir_text: str, entry: str = "main") -> int:
    """Compile and natively execute `entry` (a no-arg i32 function).

    Returns the function's return value. Raises RuntimeError with the LLVM
    error text on invalid IR.

    Implementation note: llvmlite 0.50's ORC LLJIT API does not expose
    callable symbol addresses, so we use the classic MCJIT engine and
    invoke the function pointer through ctypes.
    """
    mod = _parse_and_verify(ir_text)
    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine()

    backing = llvm.parse_assembly("")
    engine = llvm.create_mcjit_compiler(backing, tm)
    engine.add_module(mod)
    engine.finalize_object()

    addr = engine.get_function_address(entry)
    if addr == 0:
        raise RuntimeError(f"entry '{entry}' not found in module")
    cfunc = ctypes.CFUNCTYPE(ctypes.c_int)(addr)
    result = cfunc()
    # Keep engine/mod alive until after the call completes.
    del engine
    return result


def object_bytes(ir_text: str) -> bytes:
    """Machine-code generation: LLVM IR text -> relocatable object bytes.

    Kept free of file I/O so the compile pipeline can meter the backend
    (instruction selection, register allocation, emission) on its own.
    """
    mod = _parse_and_verify(ir_text)
    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine()
    return tm.emit_object(mod)


def emit_object(ir_text: str, output_path: str | Path) -> Path:
    """Emit a host-object file from LLVM IR text. Returns the written path."""
    out = Path(output_path)
    out.write_bytes(object_bytes(ir_text))
    return out


class JitProgram:
    """A JIT-compiled module kept alive so ``main`` can be called repeatedly.

    Compiling once and calling many times is what separates the *program's*
    runtime (E_run input) from JIT set-up cost.  The engine and module are
    owned by this object, so the function pointer stays valid for as long as
    the object lives.
    """

    def __init__(self, ir_text: str, entry: str = "main"):
        self._mod = _parse_and_verify(ir_text)
        target = llvm.Target.from_default_triple()
        self._tm = target.create_target_machine()
        self._engine = llvm.create_mcjit_compiler(llvm.parse_assembly(""),
                                                  self._tm)
        self._engine.add_module(self._mod)
        self._engine.finalize_object()
        addr = self._engine.get_function_address(entry)
        if addr == 0:
            raise RuntimeError(f"entry '{entry}' not found in module")
        self._cfunc = ctypes.CFUNCTYPE(ctypes.c_int)(addr)

    def call(self) -> int:
        return self._cfunc()


def measure_runtime(ir_text: str, entry: str = "main",
                    samples: int = 7, min_batch_s: float = 0.002
                    ) -> tuple[float, int]:
    """Compile once, then time repeated calls of `entry`.

    Returns ``(median_seconds_per_call, last_return_value)``.  Compiling is
    done exactly once so the measured time is the *program's* runtime
    (E_run input), not JIT setup time.  Inner batch size is auto-tuned so
    every sample spans at least ``min_batch_s``.
    """
    prog = JitProgram(ir_text, entry)
    cfunc = prog.call

    value = cfunc()                       # warm-up (also picks the result)
    t0 = time.perf_counter()
    value = cfunc()
    dt = time.perf_counter() - t0
    inner = max(1, int(min_batch_s / max(dt, 1e-9)))
    inner = min(inner, 1_000_000)

    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        for _ in range(inner):
            value = cfunc()
        times.append((time.perf_counter() - t0) / inner)
    times.sort()
    return times[len(times) // 2], value


def runs_match(ir_a: str, ir_b: str) -> bool:
    """Differential check: do two IR modules produce the same main() result?"""
    try:
        return jit_run(ir_a) == jit_run(ir_b)
    except RuntimeError:
        return False

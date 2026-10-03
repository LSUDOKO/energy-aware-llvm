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


def emit_object(ir_text: str, output_path: str | Path) -> Path:
    """Emit a host-object file from LLVM IR text. Returns the written path."""
    mod = _parse_and_verify(ir_text)
    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine()
    obj_bytes = tm.emit_object(mod)
    out = Path(output_path)
    out.write_bytes(obj_bytes)
    return out


def runs_match(ir_a: str, ir_b: str) -> bool:
    """Differential check: do two IR modules produce the same main() result?"""
    try:
        return jit_run(ir_a) == jit_run(ir_b)
    except RuntimeError:
        return False

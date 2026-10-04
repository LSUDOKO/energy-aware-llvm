"""Native (ahead-of-time) execution of the compiler's object files.

The JIT path in ``energy/execution.py`` is convenient, but the project's
deliverable is a real object file.  This module links that object with the
system C toolchain and runs it as a separate process, which gives:

  * an end-to-end correctness check (exit code of the real executable), and
  * runtime/energy measurements of the generated machine code with no JIT,
    interpreter or Python in the timed loop (``time_native``).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

HARNESS_SRC = Path(__file__).resolve().parent / "native_harness.c"


class NativeToolchainError(RuntimeError):
    pass


def find_cc() -> str:
    for cc in ("clang", "gcc", "cc"):
        path = shutil.which(cc)
        if path:
            return path
    raise NativeToolchainError("no C compiler (clang/gcc/cc) on PATH")


def link_executable(obj_path: str | Path, out_path: str | Path) -> Path:
    """Link a MiniC object (which defines ``main``) into an executable."""
    proc = subprocess.run([find_cc(), str(obj_path), "-o", str(out_path), "-lm"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise NativeToolchainError(f"link failed: {proc.stderr.strip()}")
    return Path(out_path)


def run_exit_code(obj_path: str | Path, timeout: float = 30.0) -> int:
    """Link and run the object; return its process exit status (0..255)."""
    with tempfile.TemporaryDirectory(prefix="minic_native_") as tmp:
        exe = link_executable(obj_path, Path(tmp) / "prog")
        return subprocess.run([str(exe)], timeout=timeout).returncode


@dataclass(frozen=True)
class NativeTiming:
    seconds_per_call: float
    return_value: int
    calls: int
    wall_seconds: float          # whole timing process, incl. start-up


def build_timing_binary(obj_path: str | Path, out_path: str | Path) -> Path:
    """Link ``obj`` with the timing harness (``main`` -> ``minic_main``)."""
    objcopy = shutil.which("objcopy") or shutil.which("llvm-objcopy")
    if objcopy is None:
        raise NativeToolchainError("objcopy not found (needed to rename main)")
    out = Path(out_path)
    renamed = out.with_suffix(".renamed.o")
    proc = subprocess.run([objcopy, "--redefine-sym", "main=minic_main",
                           str(obj_path), str(renamed)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise NativeToolchainError(f"objcopy failed: {proc.stderr.strip()}")
    proc = subprocess.run([find_cc(), "-O2", str(HARNESS_SRC), str(renamed),
                           "-o", str(out), "-lm"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise NativeToolchainError(f"harness link failed: {proc.stderr.strip()}")
    return out


def time_native(binary: str | Path, calls: int, timeout: float = 120.0
                ) -> NativeTiming:
    """Run a timing binary built by ``build_timing_binary``."""
    import time
    t0 = time.perf_counter()
    proc = subprocess.run([str(binary), str(calls)], capture_output=True,
                          text=True, timeout=timeout)
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        raise NativeToolchainError(f"timing binary failed: {proc.stderr.strip()}")
    value, per_call = proc.stdout.split()
    return NativeTiming(float(per_call), int(value), calls, wall)


def calibrate_calls(binary: str | Path, target_seconds: float = 0.2,
                    max_calls: int = 50_000_000) -> int:
    """Choose a call count so the timed loop lasts about ``target_seconds``."""
    t = time_native(binary, 1)
    per = max(t.seconds_per_call, 1e-9)
    return int(min(max_calls, max(1, target_seconds / per)))

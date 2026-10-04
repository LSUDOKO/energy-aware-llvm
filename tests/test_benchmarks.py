"""Benchmark-suite regression: every kernel, every pipeline, every mode.

``benchmarks/manifest.json`` holds expected ``main()`` values computed by
``benchmarks/reference.py`` (independent of the compiler).  The compiler must
reproduce them through the conventional pipeline, the unified pipeline, and
all three scheduling modes, and the emitted object must give the same answer
when linked and run as a native process.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend import native
from compile_pipeline import compile_source
from energy.execution import jit_run
from energy.experiments import conventional_pipeline, merged_pipeline
from stage2.extractor import count_instructions

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks"
ENTRIES = json.loads((BENCH / "manifest.json").read_text())["entries"]
IDS = [e["name"] for e in ENTRIES]
HAVE_CC = any(shutil.which(c) for c in ("clang", "gcc", "cc"))
FAST_GA = {"generations": 2, "pop_size": 6, "seed": 3}


def source(entry) -> str:
    return (BENCH / entry["file"]).read_text()


def test_every_kernel_is_in_the_manifest():
    on_disk = {p.name for p in BENCH.glob("*.c")}
    assert on_disk == {e["file"] for e in ENTRIES}


def test_reference_script_agrees_with_manifest():
    proc = subprocess.run([sys.executable, str(BENCH / "reference.py"), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_conventional_pipeline(entry):
    art = conventional_pipeline(source(entry))
    assert art.checks_ok, art.errors
    assert jit_run(art.ir_text) == entry["expected_return"]


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_unified_pipeline(entry):
    art = merged_pipeline(source(entry))
    assert art.checks_ok, art.errors
    assert jit_run(art.ir_text) == entry["expected_return"]


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_unified_never_emits_more_than_conventional(entry):
    conv = count_instructions(conventional_pipeline(source(entry)).ir_text)
    merged = count_instructions(merged_pipeline(source(entry)).ir_text)
    assert merged <= conv


@pytest.mark.parametrize("mode", ["-Meco", "-Mbalanced", "-Mperf"])
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_modes_preserve_semantics(entry, mode, tmp_path):
    res = compile_source(source(entry), mode=mode, name=f"t_{entry['name']}",
                         ga_kwargs=FAST_GA if mode == "-Mperf" else None)
    assert res["success"], res.get("error") or res["logs"][-3:]
    assert res["verified"] and res["differential_ok"]
    assert res["return_value"] == entry["expected_return"]
    if HAVE_CC:
        assert native.run_exit_code(res["object_file"]) == \
            entry["expected_return"] % 256

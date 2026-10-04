"""Native link/run of emitted object files (real executable, real exit code)."""
import shutil

import pytest

from backend import native
from backend.passes import emit_object_file
from energy.experiments import merged_pipeline

pytestmark = pytest.mark.skipif(
    not any(shutil.which(c) for c in ("clang", "gcc", "cc")),
    reason="no C toolchain available")

SRC = """
int tri(int n) { int s = 0; int i = 1; while (i <= n) { s = s + i; i = i + 1; } return s; }
int main() { return tri(20); }
"""


@pytest.fixture()
def obj(tmp_path):
    ir = merged_pipeline(SRC).ir_text
    return emit_object_file(ir, tmp_path / "prog.o")


def test_exit_code_of_linked_executable(obj):
    assert native.run_exit_code(obj) == 210       # tri(20) = 210


def test_exit_code_is_low_byte_of_main_result(tmp_path):
    ir = merged_pipeline("int main() { return 1000; }").ir_text
    o = emit_object_file(ir, tmp_path / "k.o")
    assert native.run_exit_code(o) == 1000 % 256


def test_timing_binary_reports_value_and_positive_time(obj, tmp_path):
    exe = native.build_timing_binary(obj, tmp_path / "t")
    calls = native.calibrate_calls(exe, target_seconds=0.02)
    t = native.time_native(exe, calls)
    assert t.return_value == 210
    assert t.seconds_per_call > 0 and t.calls == calls >= 1


def test_link_failure_is_reported(tmp_path):
    bad = tmp_path / "bad.o"
    bad.write_bytes(b"not an object file")
    with pytest.raises(native.NativeToolchainError):
        native.link_executable(bad, tmp_path / "x")

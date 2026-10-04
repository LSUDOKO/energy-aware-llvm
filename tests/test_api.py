"""Flask API contract tests (no network: Flask test client)."""
import pytest

import api

SRC = """
int main() {
    int i = 0;
    int s = 0;
    while (i < 50) { s = s + i * 2; i = i + 1; }
    return s;
}
"""


@pytest.fixture()
def client():
    api.app.config["TESTING"] = True
    return api.app.test_client()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"


def test_compile_returns_measured_evidence(client):
    r = client.post("/compile", json={"source_code": SRC, "mode": "-Meco",
                                      "n_runs": 5000})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] and body["differential_ok"]
    assert body["return_value"] == 2450
    for key in ("llvm_ir", "optimized_ir", "gating", "energy", "features",
                "instructions", "runtime_us", "baseline_runtime_us"):
        assert key in body, key
    assert body["energy"]["n_runs"] == 5000
    assert "break_even_runs" in body["energy"] and body["energy"]["lifecycle"]


def test_rejects_unknown_mode(client):
    r = client.post("/compile", json={"source_code": SRC, "mode": "-Mfoo"})
    assert r.status_code == 400
    assert "unknown mode" in r.get_json()["error"]


def test_rejects_empty_source(client):
    r = client.post("/compile", json={"source_code": "  \n", "mode": "-Meco"})
    assert r.status_code == 400


def test_rejects_bad_n_runs(client):
    r = client.post("/compile", json={"source_code": SRC, "n_runs": "many"})
    assert r.status_code == 400


def test_diagnostics_return_422(client):
    r = client.post("/compile", json={"source_code": "int main() { return y; }",
                                      "mode": "-Meco"})
    assert r.status_code == 422
    assert "y" in r.get_json()["error"]


def test_lists_all_bundled_benchmarks(client):
    r = client.get("/benchmarks")
    names = {b["name"] for b in r.get_json()}
    assert r.status_code == 200
    assert {"fib_rec", "mandelbrot", "const_fold"} <= names


def test_benchmark_source_compiles(client):
    body = client.get("/benchmarks/fib_iter").get_json()
    assert body["expected_return"] == 832040
    r = client.post("/compile", json={"source_code": body["source"],
                                      "mode": "-Meco"})
    assert r.get_json()["return_value"] == 832040


def test_unknown_benchmark_is_404(client):
    r = client.get("/benchmarks/nope")
    assert r.status_code == 404 and "nope" in r.get_json()["error"]


def test_benchmark_results_reflect_files(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "BENCH_REPORT_DIR", tmp_path)
    assert client.get("/benchmark-results").status_code == 404
    (tmp_path / "results.json").write_text('{"kernels": []}')
    (tmp_path / "runtime_speedup.png").write_bytes(b"\x89PNG")
    body = client.get("/benchmark-results").get_json()
    assert body["available"] and body["charts"] == ["runtime_speedup.png"]
    assert client.get("/reports/benchmarks/runtime_speedup.png").status_code == 200

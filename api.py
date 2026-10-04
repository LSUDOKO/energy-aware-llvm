"""Flask API for the web editor.

Delegates to ``compile_pipeline.compile_source`` — the same real Stages 1-3
implementation used by the CLI driver — so the browser gets measured
numbers (front-end time, pass time, native runtime, object size, EDP) and
the full 52-metric feature dump instead of the old simulated backend logs.

Response schema is backward compatible (success, ast_time_ms, ml_time_ms,
predicted_edp, features, selected_passes, logs, llvm_ir) plus the new
evidence fields (optimized_ir, gating, energy, instructions, runtime_us,
object_file, differential_ok).
"""
import json
import time
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

from compile_pipeline import DEFAULT_RUNS, MODES, compile_source

app = Flask(__name__)
CORS(app)

ROOT = Path(__file__).resolve().parent
BENCH_DIR = ROOT / "benchmarks"
BENCH_REPORT_DIR = ROOT / "reports" / "benchmarks"


def _manifest_entries() -> list[dict]:
    path = BENCH_DIR / "manifest.json"
    return json.loads(path.read_text())["entries"] if path.exists() else []


@app.route('/compile', methods=['POST'])
def compile_code():
    data = request.json or {}
    source_code = data.get('source_code', '')
    mode = data.get('mode', '-Mbalanced')  # -Meco, -Mbalanced, -Mperf
    try:
        n_runs = int(data.get('n_runs', DEFAULT_RUNS))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'n_runs must be an integer'}), 400
    if mode not in MODES:
        return jsonify({'success': False,
                        'error': f'unknown mode {mode!r}; expected one of {list(MODES)}'}), 400
    if not source_code.strip():
        return jsonify({'success': False, 'error': 'source_code is empty'}), 400

    try:
        result = compile_source(source_code, mode=mode, emit_object=True,
                                name='a', n_runs=n_runs)
        payload = {
            'success': result['success'],
            'ast_time_ms': result.get('ast_time_ms', 0.0),
            'ml_time_ms': result.get('ml_time_ms', 0.0),
            'predicted_edp': result.get('predicted_edp', 0.0),
            'features': result.get('features', {}),
            'selected_passes': result.get('selected_passes', []),
            'logs': result.get('logs', []),
            'llvm_ir': result.get('llvm_ir', ''),
            # --- measured evidence (new) ---
            'mode': mode,
            'gating': result.get('gating', {}),
            'optimized_ir': result.get('optimized_ir', ''),
            'instructions': result.get('instructions', {}),
            'passes_time_ms': result.get('passes_time_ms', 0.0),
            'runtime_us': result.get('runtime_us'),
            'baseline_runtime_us': result.get('baseline_runtime_us'),
            'ga': result.get('ga'),
            'return_value': result.get('return_value'),
            'differential_ok': result.get('differential_ok'),
            'object_file': result.get('object_file'),
            'object_bytes': result.get('object_bytes'),
            'energy': result.get('energy', {}),
        }
        if not result['success']:
            payload['error'] = result.get('error', 'verification failed')
            return jsonify(payload), 422
        return jsonify(payload)

    except Exception as e:
        logs = [f"[{time.strftime('%H:%M:%S')}] ERROR: {str(e)}",
                traceback.format_exc()]
        return jsonify({'success': False, 'logs': logs, 'error': str(e)}), 500


@app.route('/benchmarks', methods=['GET'])
def list_benchmarks():
    """Bundled kernels (name, description, expected main() value)."""
    return jsonify([{'name': e['name'], 'description': e['description'],
                     'expected_return': e['expected_return']}
                    for e in _manifest_entries()])


@app.route('/benchmarks/<name>', methods=['GET'])
def benchmark_source(name):
    for e in _manifest_entries():
        if e['name'] == name:
            return jsonify({**e, 'source': (BENCH_DIR / e['file']).read_text()})
    abort(404, description=f"unknown benchmark {name!r}")


@app.route('/benchmark-results', methods=['GET'])
def benchmark_results():
    """Measured suite results written by benchmarks/run_benchmarks.py."""
    path = BENCH_REPORT_DIR / "results.json"
    if not path.exists():
        return jsonify({'available': False,
                        'hint': 'run benchmarks/run_benchmarks.py'}), 404
    data = json.loads(path.read_text())
    data['available'] = True
    data['charts'] = sorted(p.name for p in BENCH_REPORT_DIR.glob("*.png"))
    return jsonify(data)


@app.route('/reports/benchmarks/<path:filename>', methods=['GET'])
def benchmark_chart(filename):
    return send_from_directory(BENCH_REPORT_DIR, filename)


@app.errorhandler(404)
def not_found(err):
    return jsonify({'success': False, 'error': err.description}), 404


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': time.time()})


if __name__ == '__main__':
    app.run(debug=True, port=5000)

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
import time
import traceback

from flask import Flask, request, jsonify
from flask_cors import CORS

from compile_pipeline import compile_source

app = Flask(__name__)
CORS(app)


@app.route('/compile', methods=['POST'])
def compile_code():
    data = request.json or {}
    source_code = data.get('source_code', '')
    mode = data.get('mode', '-Mbalanced')  # -Meco, -Mbalanced, -Mperf

    try:
        result = compile_source(source_code, mode=mode, emit_object=True,
                                name='a')
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


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': time.time()})


if __name__ == '__main__':
    app.run(debug=True, port=5000)

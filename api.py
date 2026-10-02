import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from frontend.lexer import Lexer
from frontend.parser import Parser
from frontend.codegen import CodeGenVisitor
from stage2_extractor import IRFeatureExtractor
from stage3_ml_model import get_model, rank_passes
import traceback

app = Flask(__name__)
CORS(app)

@app.route('/compile', methods=['POST'])
def compile_code():
    data = request.json
    source_code = data.get('source_code', '')
    mode = data.get('mode', '-Mbalanced') # -Meco, -Mbalanced, -Mperf

    logs = []
    
    def log_stage(msg):
        logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    try:
        # STAGE 1
        log_stage("STAGE 1: Fused Single-Pass Front-End (Minimizing E_compile)")
        t0 = time.time()
        lexer = Lexer(source_code)
        parser_obj = Parser(lexer.tokens)
        ast = parser_obj.parse()
        
        codegen = CodeGenVisitor()
        llvm_module = codegen.generate_code(ast)
        t1 = time.time()
        
        ast_time = (t1-t0)*1000
        logs.append(f"-> AST Parsed and LLVM IR emitted in {ast_time:.2f} ms.")

        # STAGE 2
        log_stage("STAGE 2: Static IR Feature Extraction & Pass Gating")
        extractor = IRFeatureExtractor(llvm_module)
        features = extractor.extract_features()
        
        logs.append("-> Extracted 35+ IR Metrics:")
        for k, v in features.items():
            logs.append(f"   {k}: {v}")

        # STAGE 3
        log_stage(f"STAGE 3: Multi-Mode Pass Scheduling & Backend ({mode})")
        selected_passes = []
        pred_edp = 0.0
        ml_time = 0.0
        
        if mode == '-Meco':
            logs.append("-> Mode: -Meco (Minimal Compile Overhead)")
            selected_passes = ['-O0']
            logs.append("-> Skipping optional passes to minimize E_compile.")
            
        elif mode == '-Mperf':
            logs.append("-> Mode: -Mperf (Genetic Algorithm Search Loop)")
            logs.append("-> Running GA to optimize 1/EDP... (Mocking search)")
            time.sleep(1.5) # Simulate GA search time
            selected_passes = ['-mem2reg', '-simplifycfg', '-loop-unroll', '-O3']
            logs.append("-> GA Selected Top Sequence.")
            
        else: # Default to -Mbalanced
            logs.append("-> Mode: -Mbalanced (ML Pass Ranker)")
            model = get_model() # Loads or trains XGBoost model
            t_start_ml = time.time()
            selected_passes, pred_edp = rank_passes(features, model)
            t_end_ml = time.time()
            ml_time = (t_end_ml-t_start_ml)*1000
            logs.append(f"-> ML Pass Ranker completed in {ml_time:.2f} ms.")
            logs.append(f"-> Predicted EDP Benefit: {pred_edp:.2f}")

        logs.append(f"-> Final Gated Pass Sequence: {', '.join(selected_passes)}")
        
        # Fake backend codegen step
        logs.append("\n-> Passing to LLVM Backend Code Gen...")
        logs.append("-> Object file generated: a.out (simulated)")
        log_stage("COMPILATION SUCCESSFUL")

        return jsonify({
            'success': True,
            'ast_time_ms': ast_time,
            'ml_time_ms': ml_time,
            'predicted_edp': pred_edp,
            'features': features,
            'selected_passes': selected_passes,
            'logs': logs,
            'llvm_ir': str(llvm_module)
        })

    except Exception as e:
        log_stage(f"ERROR: {str(e)}")
        logs.append(traceback.format_exc())
        return jsonify({
            'success': False,
            'logs': logs,
            'error': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)

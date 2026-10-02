import argparse
import sys
import time
from frontend.lexer import Lexer
from frontend.parser import Parser
from frontend.codegen import CodeGenVisitor
from stage2_extractor import IRFeatureExtractor
from stage3_ml_model import get_model, rank_passes

def log_stage(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] {msg}")

def main():
    parser = argparse.ArgumentParser(description="Energy Aware Compiler Optimization")
    parser.add_argument('source_file', type=str, help='Input MiniC source file')
    parser.add_argument('-Meco', action='store_true', help='Minimal Compile Overhead mode')
    parser.add_argument('-Mbalanced', action='store_true', help='ML Pass Ranker mode')
    parser.add_argument('-Mperf', action='store_true', help='Genetic Algorithm Search mode')
    
    args = parser.parse_args()
    
    with open(args.source_file, 'r') as f:
        source_code = f.read()

    # ==========================================
    # STAGE 1: FUSED SINGLE-PASS FRONT-END
    # ==========================================
    log_stage("STAGE 1: Fused Single-Pass Front-End (Minimizing E_compile)")
    
    t0 = time.time()
    lexer = Lexer(source_code)
    parser_obj = Parser(lexer.tokens)
    ast = parser_obj.parse()
    
    codegen = CodeGenVisitor()
    llvm_module = codegen.generate_code(ast)
    t1 = time.time()
    
    print(f"-> AST Parsed and LLVM IR emitted in {((t1-t0)*1000):.2f} ms.")

    # ==========================================
    # STAGE 2: STATIC IR FEATURE EXTRACTION
    # ==========================================
    log_stage("STAGE 2: Static IR Feature Extraction & Pass Gating")
    
    extractor = IRFeatureExtractor(llvm_module)
    features = extractor.extract_features()
    
    print("-> Extracted 35+ IR Metrics:")
    for k, v in features.items():
        print(f"   {k}: {v}")

    # ==========================================
    # STAGE 3: MULTI-MODE PASS SCHEDULING
    # ==========================================
    log_stage("STAGE 3: Multi-Mode Pass Scheduling & Backend")
    
    selected_passes = []
    
    if args.Meco:
        print("-> Mode: -Meco (Minimal Compile Overhead)")
        selected_passes = ['-O0']
        print("-> Skipping optional passes to minimize E_compile.")
        
    elif args.Mperf:
        print("-> Mode: -Mperf (Genetic Algorithm Search Loop)")
        print("-> Running GA to optimize 1/EDP... (Mocking search)")
        time.sleep(1.5) # Simulate GA search time
        selected_passes = ['-mem2reg', '-simplifycfg', '-loop-unroll', '-O3']
        print("-> GA Selected Top Sequence.")
        
    else: # Default to -Mbalanced if nothing specified, or if -Mbalanced specified
        print("-> Mode: -Mbalanced (ML Pass Ranker)")
        model = get_model() # Loads or trains XGBoost model
        t_start_ml = time.time()
        selected_passes, pred_edp = rank_passes(features, model)
        t_end_ml = time.time()
        print(f"-> ML Pass Ranker completed in {((t_end_ml-t_start_ml)*1000):.2f} ms.")
        print(f"-> Predicted EDP Benefit: {pred_edp:.2f}")

    print(f"-> Final Gated Pass Sequence: {', '.join(selected_passes)}")
    
    # Fake backend codegen step
    print("\n-> Passing to LLVM Backend Code Gen...")
    print("-> Object file generated: a.out (simulated)")
    
    log_stage("COMPILATION SUCCESSFUL")
    
if __name__ == '__main__':
    main()

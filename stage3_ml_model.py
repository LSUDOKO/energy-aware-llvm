import os
import numpy as np
import pandas as pd
from xgboost import XGBRegressor
import joblib

MODEL_PATH = 'xgboost_pass_model.pkl'

# Define the set of possible passes
AVAILABLE_PASSES = [
    '-O1', '-O2', '-O3', '-mem2reg', '-simplifycfg', 
    '-instcombine', '-gvn', '-dse', '-licm', '-loop-unroll'
]

def generate_synthetic_data(num_samples=1000):
    """Generates fake training data simulating the output of Stage 1 & 2 + Hardware RAPL."""
    print("Generating synthetic profiling data for ML model...")
    np.random.seed(42)
    
    data = []
    for _ in range(num_samples):
        # Fake IR features
        features = {
            'total_instructions': np.random.randint(10, 5000),
            'num_functions': np.random.randint(1, 50),
            'num_basic_blocks': np.random.randint(1, 1000),
            'num_loads': np.random.randint(0, 1000),
            'num_stores': np.random.randint(0, 500),
            'num_branches': np.random.randint(0, 500),
            'num_calls': np.random.randint(0, 200),
            'num_alu_ops': np.random.randint(10, 2000),
            'num_fp_ops': np.random.randint(0, 500),
            'num_allocas': np.random.randint(1, 100),
            'num_icmp': np.random.randint(0, 300),
            'num_fcmp': np.random.randint(0, 100),
            'cyclomatic_complexity_estimate': np.random.randint(1, 200)
        }
        
        # Pick a random subset of passes
        num_passes = np.random.randint(1, len(AVAILABLE_PASSES) + 1)
        selected_passes = np.random.choice(AVAILABLE_PASSES, size=num_passes, replace=False)
        
        # Fake Target: EDP Benefit (Energy-Delay Product improvement)
        # We invent a heuristic: more instructions + loop-unroll = good benefit, etc.
        edp_benefit = (features['num_alu_ops'] * 0.01) + (features['num_branches'] * 0.05)
        if '-loop-unroll' in selected_passes and features['num_branches'] > 50:
            edp_benefit += 10.0
        if '-O3' in selected_passes:
            edp_benefit += 15.0
        if '-mem2reg' in selected_passes and features['num_allocas'] > 10:
            edp_benefit += 5.0
            
        # Add pass flags to features for the model
        row = features.copy()
        for p in AVAILABLE_PASSES:
            row[p] = 1 if p in selected_passes else 0
            
        row['target_edp_benefit'] = edp_benefit + np.random.normal(0, 2.0) # Add noise
        data.append(row)
        
    return pd.DataFrame(data)

def train_model():
    df = generate_synthetic_data()
    X = df.drop(columns=['target_edp_benefit'])
    y = df['target_edp_benefit']
    
    print("Training XGBoost Regressor...")
    model = XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42)
    model.fit(X, y)
    
    joblib.dump(model, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")
    return model

def get_model():
    if os.path.exists(MODEL_PATH):
        return joblib.load(MODEL_PATH)
    else:
        return train_model()

def rank_passes(features, model):
    """Predicts the best pass sequence using the trained model (-Mbalanced mode)"""
    print("[ML Ranker] Evaluating pass sequences in <50ms...")
    
    best_benefit = -float('inf')
    best_passes = []
    
    # We test a few predefined sensible sequences and ask the model to score them
    candidates = [
        ['-O1'],
        ['-mem2reg', '-instcombine', '-simplifycfg'],
        ['-O2', '-licm'],
        ['-O3', '-loop-unroll'],
        AVAILABLE_PASSES
    ]
    
    for candidate in candidates:
        row = features.copy()
        for p in AVAILABLE_PASSES:
            row[p] = 1 if p in candidate else 0
            
        df = pd.DataFrame([row])
        pred_benefit = model.predict(df)[0]
        
        if pred_benefit > best_benefit:
            best_benefit = pred_benefit
            best_passes = candidate
            
    return best_passes, best_benefit

if __name__ == '__main__':
    train_model()

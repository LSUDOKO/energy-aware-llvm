# Energy-Aware Compiler Optimization Lab

This project implements the Energy-Aware Compiler Optimization framework as described in your architecture diagram. It is written in Python to allow for rapid lab development without requiring a complex C++ LLVM toolchain setup on Windows.

## Project Structure
* `frontend/`: Contains the **Stage 1 (Fused Single-Pass Front-End)**.
  * `lexer.py`: Tokenizes a subset of C.
  * `ast_nodes.py`: Defines the AST nodes.
  * `parser.py`: Recursive descent parser that builds the AST.
  * `codegen.py`: The **Unified Semantic Visitor** that uses `llvmlite` to emit LLVM IR.
* `stage2_extractor.py`: Contains **Stage 2 (Static IR Feature Extraction)**. Extracts static metrics (e.g., number of branches, loads, alu ops) directly from the LLVM IR.
* `stage3_ml_model.py`: Contains the **ML Pass Ranker**. Simulates dataset generation (since we don't have native Intel RAPL access on this Windows machine) and trains an XGBoost Regressor to predict the Energy-Delay Product (EDP) benefit of different LLVM pass combinations.
* `compiler_driver.py`: The main executable that stitches all stages together and handles the `-Meco`, `-Mbalanced`, and `-Mperf` flags.
* `test_program.c`: A sample MiniC program for testing.

## Prerequisites
The required packages have already been installed in the `venv` virtual environment. They include:
* `llvmlite` (LLVM python bindings)
* `xgboost` (Machine Learning model)
* `scikit-learn`, `pandas`, `numpy`

## How to Run
First, activate the virtual environment:
```powershell
.\venv\Scripts\activate
```

Then, run the compiler driver using one of the three optimization modes:

**1. Minimal Compile Overhead Mode (`-Meco`)**
Skips optional passes to minimize $E_{compile}$.
```powershell
python compiler_driver.py test_program.c -Meco
```

**2. ML Pass Ranker Mode (`-Mbalanced`) - (Default)**
Uses the trained XGBoost model to evaluate different pass sequences in <50ms and selects the one with the best predicted EDP benefit.
```powershell
python compiler_driver.py test_program.c -Mbalanced
```

**3. Genetic Algorithm Search Loop Mode (`-Mperf`)**
Simulates a Genetic Algorithm that attempts to find the absolute best pass sequence by evaluating 1/EDP over time.
```powershell
python compiler_driver.py test_program.c -Mperf
```

*(Note: The very first time you run `-Mbalanced`, it will automatically generate a synthetic dataset and train the XGBoost model, saving it to `xgboost_pass_model.pkl`)*.

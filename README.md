# Energy-Aware Compiler Optimization (LLVM)

This repository contains the prototype for an Energy-Aware Compiler Optimization framework, built as an extension to LLVM. The goal is to reduce energy usage during both compilation and execution while maintaining acceptable performance.

## Architecture Overview

The system is divided into three main stages and a hardware evaluation pipeline:

### Stage 1: Fused Single-Pass Front-End (Minimizes E_compile)
- A streamlined C/C++ front-end that fuses parsing, semantic analysis, and initial IR emission into a single pass.
- Uses Arena Allocation for fast memory management.
- Outputs a Compact SSA LLVM IR.

### Stage 2: Static IR Feature Extraction & Dynamic Pass Gating
- Extracts 35+ static features from the LLVM IR.
- Evaluates a cost-benefit decision rule to determine if an optional optimization pass should be run.

### Stage 3: Multi-Mode Pass Scheduling & Backend (Minimizes E_run & EDP)
Supports three modes:
1. **-Meco Mode**: Minimal Compile Overhead.
2. **-Mbalanced Mode**: Uses an ML model (XGBoost/Neural Network) to rank and select an optimized pass sequence in <50ms.
3. **-Mperf Mode**: Uses a Genetic Algorithm search loop to optimize the Energy-Delay Product (1/EDP).

### Validation & Hardware Evaluation
- Uses Intel RAPL (Running Average Power Limit) to measure actual energy consumption during paired interleaved trials.
- Evaluates the final Energy-Delay Product (EDP) savings against standard `-O2` / `-O3`.

## Directory Structure
- `src/Frontend`: Prototype components for the fused single-pass front-end.
- `src/Passes`: LLVM passes for IR feature extraction and dynamic gating.
- `src/Backend`: Pass scheduling logic.
- `ml_models`: Python scripts for training the XGBoost pass ranker and running the Genetic Algorithm.
- `tools`: Utilities for RAPL measurements and differential testing.

## Building (Skeleton)
```bash
mkdir build && cd build
cmake ..
make
```

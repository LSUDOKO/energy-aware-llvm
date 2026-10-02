#include "llvm/Pass.h"
#include "llvm/IR/Function.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

namespace {
  struct IRMetricsExtractor : public FunctionPass {
    static char ID;
    IRMetricsExtractor() : FunctionPass(ID) {}

    bool runOnFunction(Function &F) override {
      // Stage 2: Static IR Feature Extraction
      unsigned instrCount = 0;
      unsigned branchCount = 0;
      unsigned memoryOpCount = 0;

      for (auto &B : F) {
        for (auto &I : B) {
          instrCount++;
          if (I.isTerminator()) branchCount++;
          if (I.mayReadFromMemory() || I.mayWriteToMemory()) memoryOpCount++;
        }
      }

      errs() << "Function: " << F.getName() << "\n";
      errs() << "  Total Instructions: " << instrCount << "\n";
      errs() << "  Branches: " << branchCount << "\n";
      errs() << "  Memory Ops: " << memoryOpCount << "\n";
      
      // Feature vector would be passed to ML model or heuristic here
      return false; // Does not modify the IR
    }
  };
}

char IRMetricsExtractor::ID = 0;
static RegisterPass<IRMetricsExtractor> X("ir-metrics", "Extract static IR features for energy modelling", false, false);

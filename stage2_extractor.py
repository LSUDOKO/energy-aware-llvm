"""Backwards-compatible entry point for the Stage-2 IR feature extractor.

The original implementation counted ~13 metrics by string-splitting printed
IR.  It has been replaced by ``stage2.extractor.IRFeatureExtractor``, which
walks LLVM IR through llvmlite's ``binding`` API (functions → blocks →
instructions → operands) with real dominator/loop analysis and reports 40+
metrics.  Existing imports (``from stage2_extractor import
IRFeatureExtractor``) keep working unchanged.
"""
from stage2.extractor import (  # noqa: F401
    IRFeatureExtractor,
    count_instructions,
    static_cost,
)

if __name__ == "__main__":
    print("IRFeatureExtractor ready (stage2.extractor, 40+ metrics).")

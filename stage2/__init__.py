"""Stage 2 package: static IR metrics + energy-aware pass gating."""

from stage2.extractor import IRFeatureExtractor, static_cost, count_instructions  # noqa: F401
from stage2.pass_gating import CostBenefitGate, PassDecision, gate_passes  # noqa: F401

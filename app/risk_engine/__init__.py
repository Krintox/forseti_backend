"""
Unified Risk Engine.

A composite view over signals every other module in this expansion already
computed for a round - the DTL invariant outcome, the Intent Firewall's drift
score, whether Deception Lab found anything, the ML detector's probability,
and the kill-chain's attack_chain_score. This is a SYNTHESIS layer, not a new
detector: it invents no signal of its own, and `deterministic_override` makes
explicit that the DTL invariant - not this composite - is what actually
decided the round's outcome. The equal weighting is a deliberate, documented
simplification (see `compute_unified_risk`'s docstring), not a tuned model.
"""

from .risk import compute_unified_risk

__all__ = ["compute_unified_risk"]

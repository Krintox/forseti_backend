"""
Agentic Payment Kill Chain.

A lifecycle taxonomy (11 stages, INTENT -> RECONCILIATION) that every attack
vector built so far is mapped onto, plus per-round and per-session scoring
computed from the SAME event stream every other module already produces -
nothing here re-runs an attack or invents a parallel measurement.

This is a mapping and scoring layer on top of `taxonomy.py`'s IMPLEMENTED
vectors, not a new attack surface: it answers "where in the agent's lifecycle
did this attack land" for vectors that already exist, and "how much of the
lifecycle has this session actually exercised."
"""

from .scoring import coverage, score_round
from .stages import KILL_CHAIN_STAGES, STRATEGY_TO_STAGE, stage_for_strategy

__all__ = ["KILL_CHAIN_STAGES", "STRATEGY_TO_STAGE", "stage_for_strategy", "score_round", "coverage"]

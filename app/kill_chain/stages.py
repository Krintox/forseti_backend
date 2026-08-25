"""
The 11-stage agentic payment lifecycle and the mapping of every currently
IMPLEMENTED red-team vector (see taxonomy.py) onto the ONE stage its
mechanism most specifically demonstrates.

Each round in the arena runs a single vector, so a single round exercises
exactly one stage - "stages_reached" becomes meaningful across a SESSION of
multiple rounds (see scoring.coverage), not within one round. A vector is
mapped to one stage, not several, for the same reason STRATEGY_DIMENSION in
orchestrator.py assigns one authority dimension per vector: an honest map
should be checkable, and "this vector touches 4 stages a little" is not.

All 11 stages now have an implemented vector behind them. The last two -
SETTLEMENT_CONFLICT and RECONCILIATION_DRIFT - are deliberately NOT DTL
invariants or Deception Lab detections: they are post-authorization lifecycle
failures (see app/settlement/reconciliation.py), where every authority
dimension was satisfied at authorization time and the failure only appears in
the disagreement between settlement legs afterwards. CONSTRAINT_EROSION fills
what was previously the unmapped GOAL_HIJACKING stage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

KILL_CHAIN_STAGES: List[Dict[str, Any]] = [
    {"index": 1, "code": "INTENT_MANIPULATION",
     "label": "Intent Manipulation",
     "description": "The agent's understanding of what the user asked for is corrupted at the source."},
    {"index": 2, "code": "DELEGATION_ABUSE",
     "label": "Delegation Abuse",
     "description": "The delegation's own lifecycle (grant, expiry, sub-delegation, revocation) is exploited."},
    {"index": 3, "code": "GOAL_HIJACKING",
     "label": "Goal Hijacking",
     "description": "The agent's plan diverges from the delegated goal while still executing 'successfully'."},
    {"index": 4, "code": "MEMORY_POISONING",
     "label": "Memory Poisoning",
     "description": "The agent's context/memory is fed a false record of prior approval or state."},
    {"index": 5, "code": "TOOL_POISONING",
     "label": "Tool Poisoning",
     "description": "A tool or reference signal the agent relies on returns misleading output."},
    {"index": 6, "code": "MERCHANT_IMPERSONATION",
     "label": "Merchant / Counterparty Impersonation",
     "description": "The transaction settles with a counterparty other than the one actually authorised."},
    {"index": 7, "code": "CART_SUBSTITUTION",
     "label": "Cart Substitution",
     "description": "The economic substance of the basket diverges from the delegated purpose."},
    {"index": 8, "code": "AUTHORITY_BYPASS",
     "label": "Authority Bypass",
     "description": "The transaction proceeds without authority that was actually required for it."},
    {"index": 9, "code": "CROSS_RAIL_SPLIT",
     "label": "Cross-Rail Split",
     "description": "One objective is fragmented across rails so no single rail sees the aggregate."},
    {"index": 10, "code": "SETTLEMENT_CONFLICT",
     "label": "Settlement Conflict",
     "description": "Two legitimate-looking settlement instructions for the same authority conflict."},
    {"index": 11, "code": "RECONCILIATION_DRIFT",
     "label": "Reconciliation Drift",
     "description": "Post-settlement books diverge from what the delegation actually authorised."},
]

_STAGE_BY_CODE = {s["code"]: s for s in KILL_CHAIN_STAGES}

# One primary stage per IMPLEMENTED vector key (taxonomy.py IMPLEMENTED[...]["key"]).
STRATEGY_TO_STAGE: Dict[str, str] = {
    "PROMPT_INJECTION": "INTENT_MANIPULATION",
    "REVOCATION_FLOOD": "DELEGATION_ABUSE",
    "SCOPE_CREEP": "DELEGATION_ABUSE",
    "LAPSED_MANDATE": "DELEGATION_ABUSE",
    "CONTEXT_MEMORY_POISONING": "MEMORY_POISONING",
    "TOOL_OUTPUT_POISONING": "TOOL_POISONING",
    "BASELINE_POISONING": "TOOL_POISONING",
    "BENEFICIARY_DRIFT": "MERCHANT_IMPERSONATION",
    "INTENT_LAUNDERING": "CART_SUBSTITUTION",
    "RAIL_SCOPE_VIOLATION": "AUTHORITY_BYPASS",
    "PER_TX_BREACH": "AUTHORITY_BYPASS",
    "AUTHORITY_IMPERSONATION": "AUTHORITY_BYPASS",
    "VELOCITY_BURST": "AUTHORITY_BYPASS",
    "CROSS_RAIL_SPLIT": "CROSS_RAIL_SPLIT",
    "CONSTRAINT_EROSION": "GOAL_HIJACKING",
    "SETTLEMENT_CONFLICT": "SETTLEMENT_CONFLICT",
    "RECONCILIATION_DRIFT": "RECONCILIATION_DRIFT",
}


def stage_for_strategy(strategy: str) -> Optional[Dict[str, Any]]:
    code = STRATEGY_TO_STAGE.get(strategy)
    return dict(_STAGE_BY_CODE[code]) if code else None

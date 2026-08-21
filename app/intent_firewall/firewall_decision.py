"""
Verdict layer for the Agent Intent Firewall.

Turns a drift vector (see drift_engine.py) into one of three actionable
verdicts. Thresholds are keyed to invariant SEVERITY, already assigned
per-dimension in dtl/invariant_engine.py's INVARIANT_REGISTRY - this module
does not invent its own severity scale, it reads the one that already exists.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..dtl.invariant_engine import INVARIANT_REGISTRY
from ..models.proofs import SemanticDriftProof
from .drift_engine import compute_drift_vector

_SEVERITY_BY_CODE: Dict[str, str] = {row["code"]: row["severity"] for row in INVARIANT_REGISTRY}

# Additive invariants that live outside dtl/invariant_engine.py's own registry
# scope (there are none today - INV_07 is registered there directly) register
# their severity here instead of mutating that registry. Kept for the next
# invariant a future module adds without touching the six-dimension core.
_EXTRA_SEVERITY: Dict[str, str] = {}


def _severity(code: str) -> str:
    return _SEVERITY_BY_CODE.get(code) or _EXTRA_SEVERITY.get(code, "MEDIUM")


def evaluate(tx_id: str, proofs: List[SemanticDriftProof]) -> Dict[str, Any]:
    """
    ALLOW         - no dimension drifted.
    PARTIAL_DRIFT - drift confined to MEDIUM-severity dimensions (e.g. a
                    per-transaction overshoot); a step-up / partial-auth
                    response is sufficient, no need to freeze the agent.
    HARD_DRIFT    - any HIGH or CRITICAL dimension drifted; the action is
                    outside the delegated authority in a way that must be
                    blocked or quarantined, not merely queried.
    """
    vector = compute_drift_vector(tx_id, proofs)
    if not proofs:
        vector["verdict"] = "ALLOW"
        return vector

    severities = {_severity(p.invariant_code) for p in proofs}
    vector["verdict"] = "HARD_DRIFT" if severities & {"HIGH", "CRITICAL"} else "PARTIAL_DRIFT"
    return vector

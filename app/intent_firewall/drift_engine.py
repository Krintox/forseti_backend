"""
Multi-dimensional drift vector computation.

Reuses the SAME SemanticDriftProof objects `DTLInvariantEngine.evaluate_all`
already produced for a transaction - every proof already carries the
`authority_dimension` it violates and a `drift_score` in [0, 1]. Nothing here
is invented; this module only reshapes proofs into the per-dimension
breakdown a judge reasons about.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models.proofs import SemanticDriftProof
from ..models.state import AuthorityDimension

# Maps a proof's `authority_dimension` onto the JSON key the firewall reports.
_DIMENSION_TO_DRIFT_KEY: Dict[str, str] = {
    AuthorityDimension.AMOUNT.value: "amount_drift",
    AuthorityDimension.PER_TX.value: "per_tx_drift",
    AuthorityDimension.RAIL.value: "rail_drift",
    AuthorityDimension.MERCHANT.value: "merchant_drift",
    AuthorityDimension.PURPOSE.value: "semantic_drift",
    AuthorityDimension.TIME.value: "temporal_drift",
    AuthorityDimension.BENEFICIARY.value: "beneficiary_drift",
}

DRIFT_KEYS: List[str] = list(_DIMENSION_TO_DRIFT_KEY.values())


def compute_drift_vector(tx_id: str, proofs: List[SemanticDriftProof]) -> Dict[str, Any]:
    """
    Builds the per-dimension drift breakdown for one transaction from
    whatever proofs the invariant engine produced for it (possibly none).
    """
    breakdown: Dict[str, float] = {key: 0.0 for key in DRIFT_KEYS}
    violating: List[str] = []
    codes: List[str] = []
    for proof in proofs:
        key = _DIMENSION_TO_DRIFT_KEY.get(proof.authority_dimension)
        if key is None:
            continue
        breakdown[key] = max(breakdown[key], round(proof.drift_score, 4))
        violating.append(key)
        codes.append(proof.invariant_code)

    overall = max(breakdown.values()) if breakdown else 0.0
    return {
        "tx_id": tx_id,
        "overall_drift_score": round(overall, 4),
        "drift_breakdown": breakdown,
        "violating_dimensions": violating,
        "invariant_codes": codes,
    }

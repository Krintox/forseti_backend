from typing import List, Dict, Any, Optional
from .vectors.cross_rail_split import CrossRailSplitVector
from .vectors.intent_laundering import IntentLaunderingVector
from .vectors.other_vectors import BaselinePoisonVector, RevocationFloodVector, VelocitySpikeVector, ScopeCreepVector
from ..models.transactions import SyntheticTransaction

class RedPlanner:
    """
    Goal-Driven Deterministic & Adaptive Red Agent Planner.
    Maintains attack memory and composes primitives.
    """
    def __init__(self):
        self.attack_history: List[Dict[str, Any]] = []

    def get_attack_for_round(self, round_num: int) -> List[SyntheticTransaction]:
        """
        Deterministic 6-Round Red vs Blue Tournament Mapping for live demos.
        """
        if round_num == 1:
            # Round 1: Intent Laundering (Grocery MCC + Amazon Gift Cards)
            return [IntentLaunderingVector.generate_attack()]
        elif round_num == 2:
            # Round 2: Flagship Cross-Rail Budget Splitting (₹4k x 3 across Card, UPI, AP2)
            return CrossRailSplitVector.generate_attack()
        elif round_num == 3:
            # Round 3: Baseline Poisoning
            return BaselinePoisonVector.generate_attack()
        elif round_num == 4:
            # Round 4: Revocation Flood
            return RevocationFloodVector.generate_attack()
        elif round_num == 5:
            # Round 5: Velocity Card Testing
            return VelocitySpikeVector.generate_attack()
        elif round_num == 6:
            # Round 6: Sub-Agent Scope Creep
            return [ScopeCreepVector.generate_attack()]
        else:
            return CrossRailSplitVector.generate_attack()

    def record_outcome(self, tx_id: str, attack_type: str, blocked_by_dtl: bool, proof_generated: bool):
        self.attack_history.append({
            "tx_id": tx_id,
            "attack_type": attack_type,
            "blocked_by_dtl": blocked_by_dtl,
            "proof_generated": proof_generated
        })

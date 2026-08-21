from typing import Dict, Any, List, Optional
from datetime import datetime

from .attack_memory import AttackMemoryStore, AttackAttemptRecord
from .adaptive_planner import AdaptiveRedPlanner
from .policy_adapter import BluePolicyAdapter
from ..models.state import DTLGlobalAuthorityState

class ClosedLoopFeedbackEngine:
    """
    Closed-Loop Red/Blue Feedback Coordinator.
    Drives iterative rounds where Red observes defense outcomes and Blue hardens policies.
    """
    def __init__(self):
        self.memory = AttackMemoryStore()
        self.red_planner = AdaptiveRedPlanner(self.memory)
        self.blue_adapter = BluePolicyAdapter()

    def get_next_red_action(self, round_id: int) -> Dict[str, Any]:
        return self.red_planner.plan_next_move(round_id)

    def plan_next_strategy(self, headroom_ratio: float = 1.0) -> Dict[str, Any]:
        """Red agent's next move, derived from the outcomes it has observed."""
        return self.red_planner.plan_next_move(
            round_id=len(self.memory.history), headroom_ratio=headroom_ratio
        )

    def record_round_outcome(
        self,
        round_id: int,
        strategy: str,
        target_rails: List[str],
        attempted_amount: float,
        is_detected: bool,
        detection_score: float,
        violating_invariant: Optional[str],
        defense_action: str,
        red_reasoning: str,
        auth_state: DTLGlobalAuthorityState
    ) -> Dict[str, Any]:
        # Blue adapts, escalating if this SAME invariant has fired before -
        # count prior occurrences BEFORE appending this round's record, so a
        # first-time violation still gets violation_count=1 (the soft
        # response), not the escalated one.
        blue_desc, changes = "", {}
        if is_detected and violating_invariant:
            prior_hits = sum(
                1 for r in self.memory.history if r.violating_invariant == violating_invariant
            )
            blue_desc, changes = self.blue_adapter.adapt_policy(
                auth_state, violating_invariant, violation_count=prior_hits + 1
            )

        record = AttackAttemptRecord(
            round_id=round_id,
            strategy=strategy,
            target_rails=target_rails,
            attempted_amount=attempted_amount,
            is_detected=is_detected,
            detection_score=detection_score,
            violating_invariant=violating_invariant,
            defense_action=defense_action,
            red_reasoning=red_reasoning,
            blue_adaptation=blue_desc
        )
        self.memory.record_attempt(record)

        return {
            "record": record.model_dump(mode="json"),
            "policy_changes": changes,
            "blue_adaptation_summary": blue_desc
        }

    def get_adaptation_history(self) -> List[Dict[str, Any]]:
        # mode="json" keeps the timestamp a string; a raw datetime here used to
        # make the whole arena state unserializable over the WebSocket.
        return [r.model_dump(mode="json") for r in self.memory.history]

    def reset(self):
        self.memory.clear()

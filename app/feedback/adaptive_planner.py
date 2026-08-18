"""
Adaptive Red planner.

Strategy selection is DERIVED from observed outcomes, not looked up from a
hardcoded if/else chain. Each candidate is scored from what actually happened in
previous rounds - whether it was contained, how confidently the detector scored
it, and how much authority headroom is left - and the full scoring table is
returned so the UI can show WHY a strategy was chosen.

The planner is deterministic: identical history always yields an identical plan,
which is what makes the demo reproducible. No LLM is involved.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .attack_memory import AttackMemoryStore

# What each strategy needs in order to work. The planner reasons over these
# preconditions rather than over the strategy name.
STRATEGY_PROFILE: Dict[str, Dict[str, Any]] = {
    "CROSS_RAIL_SPLIT": {
        "round": 2,
        "needs_headroom": False,
        "exploits": "Per-rail limit silos: no single rail sees the aggregate.",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "target_rails": ["CARD_TOKEN", "UPI_CIRCLE", "AGENTIC_AP2"],
        "base_prior": 0.90,
    },
    "INTENT_LAUNDERING": {
        "round": 1,
        "needs_headroom": True,
        "exploits": "Merchant category looks compliant while value becomes liquid stored value.",
        "defeated_by": "INV_02_SEMANTIC_INTENT_DRIFT",
        "target_rails": ["CARD_TOKEN"],
        "base_prior": 0.82,
    },
    "REVOCATION_FLOOD": {
        "round": 4,
        "needs_headroom": False,
        "exploits": "Asynchronous finality race between revoke and re-grant.",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "target_rails": ["AGENTIC_AP2"],
        "base_prior": 0.74,
    },
    "VELOCITY_BURST": {
        "round": 5,
        "needs_headroom": True,
        "exploits": "Many sub-threshold transactions that individually avoid review.",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "target_rails": ["CARD_TOKEN"],
        "base_prior": 0.68,
    },
    "SCOPE_CREEP": {
        "round": 6,
        "needs_headroom": True,
        "exploits": "Sub-agent delegation widens the economic scope beyond the parent grant.",
        "defeated_by": "INV_03_UNAUTHORIZED_MCC",
        "target_rails": ["AGENTIC_AP2"],
        "base_prior": 0.66,
    },
    "BASELINE_POISONING": {
        "round": 3,
        "needs_headroom": True,
        "exploits": "Slow drift of the spending baseline so thresholds follow the attacker.",
        "defeated_by": "INV_02_SEMANTIC_INTENT_DRIFT",
        "target_rails": ["UPI_CIRCLE"],
        "base_prior": 0.60,
    },
}


class AdaptiveRedPlanner:
    """Scores every strategy against observed defensive behaviour."""

    def __init__(self, memory: AttackMemoryStore):
        self.memory = memory

    def _observations(self) -> Dict[str, Dict[str, Any]]:
        """Aggregates what the Red agent has actually observed per strategy."""
        obs: Dict[str, Dict[str, Any]] = {}
        for record in self.memory.history:
            o = obs.setdefault(record.strategy, {
                "attempts": 0, "contained": 0, "detection_scores": [],
                "invariants_triggered": [],
            })
            o["attempts"] += 1
            if record.is_detected:
                o["contained"] += 1
                if record.violating_invariant:
                    o["invariants_triggered"].append(record.violating_invariant)
            o["detection_scores"].append(float(record.detection_score or 0.0))
        return obs

    def score_strategies(self, headroom_ratio: float = 1.0) -> List[Dict[str, Any]]:
        """
        Returns every candidate with a derived score and a written rationale.

        score = base_prior
                x (1 - containment_rate)      how often it has been stopped
                x (1 - mean_detection_score)  how loudly the model flagged it
                x headroom_feasibility        can it still be executed at all
        """
        obs = self._observations()
        rows: List[Dict[str, Any]] = []

        for name, profile in STRATEGY_PROFILE.items():
            o = obs.get(name)
            attempts = o["attempts"] if o else 0
            contained = o["contained"] if o else 0
            containment_rate = (contained / attempts) if attempts else 0.0
            mean_detection = (sum(o["detection_scores"]) / len(o["detection_scores"])) if o and o["detection_scores"] else 0.0

            # A strategy needing headroom becomes infeasible once the budget is
            # committed; a splitting strategy does not care.
            if profile["needs_headroom"]:
                feasibility = max(0.05, min(1.0, headroom_ratio))
            else:
                feasibility = 1.0

            score = profile["base_prior"] * (1.0 - containment_rate) * (1.0 - min(1.0, mean_detection)) * feasibility

            if attempts == 0:
                rationale = f"Never attempted. Prior {profile['base_prior']:.2f} from exploit surface: {profile['exploits']}"
            elif containment_rate >= 1.0:
                rationale = (f"Contained on all {attempts} attempt(s) by {sorted(set(o['invariants_triggered'])) or 'defence'}. "
                             f"Expected value collapses to {score:.3f}.")
            else:
                rationale = (f"{contained}/{attempts} contained, mean detector score {mean_detection:.2f}. "
                             f"Residual expected value {score:.3f}.")

            rows.append({
                "strategy": name,
                "score": round(score, 4),
                "base_prior": profile["base_prior"],
                "attempts_observed": attempts,
                "containment_rate": round(containment_rate, 3),
                "mean_detection_score": round(mean_detection, 3),
                "headroom_feasibility": round(feasibility, 3),
                "exploits": profile["exploits"],
                "expected_defence": profile["defeated_by"],
                "target_rails": profile["target_rails"],
                "round_number": profile["round"],
                "rationale": rationale,
            })

        rows.sort(key=lambda r: (-r["score"], r["strategy"]))
        return rows

    def plan_next_move(self, round_id: int = 0, headroom_ratio: float = 1.0) -> Dict[str, Any]:
        """Picks the highest-scoring strategy and explains the pivot."""
        table = self.score_strategies(headroom_ratio=headroom_ratio)
        best = table[0]
        latest = self.memory.get_latest_attempt()

        if latest is None:
            pivot = "Opening move. No defensive behaviour observed yet."
        elif latest.is_detected:
            pivot = (f"{latest.strategy} was contained by "
                     f"{latest.violating_invariant or 'the defence'} (detector score "
                     f"{latest.detection_score:.2f}). Pivoting to the highest-scoring alternative.")
        else:
            pivot = f"{latest.strategy} was not contained. Continuing to press the highest-scoring option."

        return {
            "next_strategy": best["strategy"],
            "strategy": best["strategy"],
            "round_number": best["round_number"],
            "confidence": best["score"],
            "reasoning": f"{pivot} {best['rationale']}",
            "pivot_reason": pivot,
            "target_rails": best["target_rails"],
            "expected_defence": best["expected_defence"],
            "scoring_table": table,
            "selection_method": "deterministic argmax over outcome-derived scores (no LLM, reproducible)",
        }

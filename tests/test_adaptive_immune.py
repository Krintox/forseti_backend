"""
Tests for Module 5 of the Agentic Payment Security Runtime expansion:
the Adaptive Fraud Immune System (Blue-side escalation).

Before this module, BluePolicyAdapter.adapt_policy was a pure function of
the CURRENT invariant code alone - the same invariant always produced the
exact same response, regardless of how many times Red had already been
caught doing it. These tests pin the escalation ladder that replaces that:
1st occurrence -> the original per-invariant soft response, 2nd -> capability
downgrade, 3rd+ -> mandate suspension, capped (does not escalate further).
"""

import asyncio

import pytest

from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.feedback.feedback_engine import ClosedLoopFeedbackEngine
from app.feedback.policy_adapter import BluePolicyAdapter
from app.kill_chain import stage_for_strategy
from app.models.state import DefensePolicy
from app.redteam.vectors.constraint_erosion import ConstraintErosionVector

AUTHORITY_ID = "auth_household_grocery_2026"


class TestEscalationLadder:
    def test_first_occurrence_of_budget_violation_gets_the_original_soft_response(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        desc, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_01_GLOBAL_BUDGET_EXCEEDED", violation_count=1
        )
        assert auth.active_policy == DefensePolicy.TIGHTENED_HEADROOM_V2
        assert changes["escalated"] is False
        assert "10%" in desc

    def test_first_occurrence_of_semantic_drift_gets_the_original_soft_response(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        desc, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_02_SEMANTIC_INTENT_DRIFT", violation_count=1
        )
        assert auth.active_policy == DefensePolicy.STRICT_CATALOG_ATTESTATION
        assert changes["escalated"] is False

    def test_first_occurrence_of_any_other_invariant_gets_step_up(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        desc, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_04_UNAUTHORIZED_RAIL", violation_count=1
        )
        assert auth.active_policy == DefensePolicy.STEP_UP_VERIFICATION
        assert changes["escalated"] is False

    def test_second_occurrence_escalates_to_capability_quarantine_regardless_of_invariant(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        desc, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_04_UNAUTHORIZED_RAIL", violation_count=2
        )
        assert auth.active_policy == DefensePolicy.CAPABILITY_QUARANTINED
        assert changes["escalated"] is True
        assert "REPEAT OFFENSE" in desc
        assert "2x" in desc

    def test_third_occurrence_escalates_to_agent_suspended(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        desc, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_01_GLOBAL_BUDGET_EXCEEDED", violation_count=3
        )
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED
        assert changes["escalated"] is True
        assert "PERSISTENT OFFENSE" in desc

    def test_escalation_is_capped_not_unbounded(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        _, changes = BluePolicyAdapter.adapt_policy(
            auth, "INV_01_GLOBAL_BUDGET_EXCEEDED", violation_count=9
        )
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED
        assert changes["violation_count"] == 9  # the raw count is still reported


class TestFeedbackEngineIntegration:
    """The engine must count PRIOR occurrences before recording the new one."""

    def _outcome(self, engine: ClosedLoopFeedbackEngine, auth, round_id: int):
        return engine.record_round_outcome(
            round_id=round_id,
            strategy="RAIL_SCOPE_VIOLATION",
            target_rails=["CARD_TOKEN"],
            attempted_amount=5000.0,
            is_detected=True,
            detection_score=0.1,
            violating_invariant="INV_04_UNAUTHORIZED_RAIL",
            defense_action="CONTAINED",
            red_reasoning="test",
            auth_state=auth,
        )

    def test_repeated_invariant_across_rounds_escalates(self):
        engine = ClosedLoopFeedbackEngine()
        auth = DTLLedger().get_authority(AUTHORITY_ID)

        first = self._outcome(engine, auth, round_id=1)
        assert first["policy_changes"]["violation_count"] == 1
        assert first["policy_changes"]["escalated"] is False
        assert auth.active_policy == DefensePolicy.STEP_UP_VERIFICATION

        second = self._outcome(engine, auth, round_id=2)
        assert second["policy_changes"]["violation_count"] == 2
        assert second["policy_changes"]["escalated"] is True
        assert auth.active_policy == DefensePolicy.CAPABILITY_QUARANTINED

        third = self._outcome(engine, auth, round_id=3)
        assert third["policy_changes"]["violation_count"] == 3
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED

    def test_a_different_invariant_starts_its_own_count(self):
        engine = ClosedLoopFeedbackEngine()
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        self._outcome(engine, auth, round_id=1)  # INV_04, count 1

        outcome = engine.record_round_outcome(
            round_id=2, strategy="LAPSED_MANDATE", target_rails=["UPI_CIRCLE"],
            attempted_amount=2500.0, is_detected=True, detection_score=0.1,
            violating_invariant="INV_06_AUTHORITY_EXPIRED", defense_action="CONTAINED",
            red_reasoning="test", auth_state=auth,
        )
        assert outcome["policy_changes"]["violation_count"] == 1
        assert outcome["policy_changes"]["escalated"] is False

    def test_reset_clears_the_escalation_count(self):
        engine = ClosedLoopFeedbackEngine()
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        self._outcome(engine, auth, round_id=1)
        self._outcome(engine, auth, round_id=2)
        assert auth.active_policy == DefensePolicy.CAPABILITY_QUARANTINED

        engine.reset()
        auth2 = DTLLedger().get_authority(AUTHORITY_ID)
        outcome = self._outcome(engine, auth2, round_id=1)
        assert outcome["policy_changes"]["violation_count"] == 1


class TestOrchestratorIntegration:
    def test_repeating_the_same_strategy_escalates_the_live_round(self):
        from app.arena.orchestrator import ArenaBattleOrchestrator

        async def go():
            orch = ArenaBattleOrchestrator()
            r1 = await orch.run_round_stream(round_number=7, dtl_enabled=True, speed=100.0)  # RAIL_SCOPE_VIOLATION
            r2 = await orch.run_round_stream(round_number=7, dtl_enabled=True, speed=100.0)
            return r1, r2

        r1, r2 = asyncio.run(go())
        blue1 = [e for e in r1["events"] if e["event_type"] == "BLUE_ADAPTATION" and e["round_id"] == 7]
        blue2 = [e for e in r2["events"] if e["event_type"] == "BLUE_ADAPTATION" and e["round_id"] == 7]
        assert blue1 and blue1[-1]["payload"]["violation_count"] == 1
        assert blue1[-1]["payload"]["escalated"] is False
        assert blue2 and blue2[-1]["payload"]["violation_count"] == 2
        assert blue2[-1]["payload"]["escalated"] is True

    def test_default_campaign_escalates_through_the_full_ladder(self):
        from app.arena.orchestrator import ArenaBattleOrchestrator

        async def go():
            orch = ArenaBattleOrchestrator()
            return await orch.run_campaign()

        result = asyncio.run(go())
        assert result["round_numbers"] == [7, 7, 7]
        assert len(result["rounds"]) == 3
        assert result["final_active_policy"] == DefensePolicy.AGENT_SUSPENDED.value
        assert result["kill_chain_coverage"]["stages_reached"] == 1  # all 3 rounds are AUTHORITY_BYPASS
        assert result["kill_chain_coverage"]["stages_contained"] == 1

    def test_custom_campaign_sequence_accumulates_round_history(self):
        from app.arena.orchestrator import ArenaBattleOrchestrator

        async def go():
            orch = ArenaBattleOrchestrator()
            result = await orch.run_campaign(round_numbers=[2, 10, 15])
            return orch, result

        orch, result = asyncio.run(go())
        assert result["round_numbers"] == [2, 10, 15]
        assert len(orch.round_history) == 3
        strategies = [r["strategy"] for r in result["rounds"]]
        assert strategies == ["CROSS_RAIL_SPLIT", "BENEFICIARY_DRIFT", "CONSTRAINT_EROSION"]


class TestConstraintErosionVector:
    def test_pure_leg_passes_every_stored_value_leg_is_caught(self):
        ledger = DTLLedger()
        auth = ledger.reset_authority(
            AUTHORITY_ID,
            budget=ConstraintErosionVector.authority_profile["global_budget_ceiling"],
            profile=ConstraintErosionVector.authority_profile,
        )
        txs = ConstraintErosionVector.generate_attack(AUTHORITY_ID)
        engine = DTLInvariantEngine()

        leg1_ok, leg1_proof = engine.evaluate_invariants(auth, txs[0])
        assert leg1_ok is True and leg1_proof is None

        for leg in txs[1:]:
            ok, proof = engine.evaluate_invariants(auth, leg)
            assert ok is False
            assert proof.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT"

    def test_small_first_slice_is_caught_exactly_as_reliably_as_the_last(self):
        """The deterministic-not-threshold claim this vector exists to demonstrate."""
        ledger = DTLLedger()
        auth = ledger.reset_authority(
            AUTHORITY_ID,
            budget=ConstraintErosionVector.authority_profile["global_budget_ceiling"],
            profile=ConstraintErosionVector.authority_profile,
        )
        txs = ConstraintErosionVector.generate_attack(AUTHORITY_ID)
        engine = DTLInvariantEngine()
        _, small_slice_proof = engine.evaluate_invariants(auth, txs[1])   # 15% eroded
        _, near_total_proof = engine.evaluate_invariants(auth, txs[3])    # ~95% eroded
        assert small_slice_proof.invariant_code == near_total_proof.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT"

    def test_no_leg_trips_the_amount_dimension(self):
        """Each leg individually, and the running total, stay under the ceiling."""
        ledger = DTLLedger()
        auth = ledger.reset_authority(
            AUTHORITY_ID,
            budget=ConstraintErosionVector.authority_profile["global_budget_ceiling"],
            profile=ConstraintErosionVector.authority_profile,
        )
        txs = ConstraintErosionVector.generate_attack(AUTHORITY_ID)
        running_total = sum(t.amount for t in txs)
        assert running_total <= auth.global_budget_ceiling

    def test_maps_onto_the_previously_unfilled_goal_hijacking_stage(self):
        stage = stage_for_strategy("CONSTRAINT_EROSION")
        assert stage is not None
        assert stage["code"] == "GOAL_HIJACKING"

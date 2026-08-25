"""
Tests for the Settlement Reconciliation Engine: the two Kill Chain stages
(SETTLEMENT_CONFLICT, RECONCILIATION_DRIFT) that had no implemented vector
until this module. Mirrors the structure of test_deception_lab.py and
test_kill_chain.py: unit tests on the deterministic detectors themselves,
then an end-to-end orchestrator round proving detection, containment, and
kill-chain scoring all actually wire together.
"""

import asyncio

import pytest

from app.arena.orchestrator import ArenaBattleOrchestrator
from app.kill_chain import STRATEGY_TO_STAGE, stage_for_strategy
from app.models.state import DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.redteam.vectors.reconciliation_drift import ReconciliationDriftVector
from app.redteam.vectors.settlement_conflict import SettlementConflictVector
from app.settlement import apply_settlement_containment, evaluate_all
from app.settlement.reconciliation import detect_reconciliation_drift, detect_settlement_conflict
from app.taxonomy import IMPLEMENTED, TAXONOMY


def _auth() -> DTLGlobalAuthorityState:
    return DTLGlobalAuthorityState(
        authority_id="auth_test", principal="test_principal", agent_id="agent_test",
        global_budget_ceiling=12000.0,
    )


def _tx(**overrides) -> SyntheticTransaction:
    base = dict(
        tx_id="tx_test_001", authority_id="auth_test", agent_id="agent_test",
        rail=PaymentRailType.CARD_TOKEN, amount=5000.0,
        merchant_id="merch_x", merchant_name="Merchant X", merchant_mcc="5411",
        items=[CartItem(sku="SKU_X", name="Item X", category="GROCERY", unit_price=5000.0, quantity=1)],
    )
    base.update(overrides)
    return SyntheticTransaction(**base)


class TestSettlementConflictDetector:
    def test_fires_on_cross_rail_capture_and_refund(self):
        auth = _auth()
        capture = _tx(tx_id="tx_a", rail=PaymentRailType.CARD_TOKEN,
                       obligation_id="oblig_1", settlement_action="CAPTURE")
        refund = _tx(tx_id="tx_b", rail=PaymentRailType.UPI_CIRCLE,
                      obligation_id="oblig_1", settlement_action="REFUND")
        proof = detect_settlement_conflict(auth, [capture, refund])
        assert proof is not None
        assert proof.conflict_code == "RECON_01_SETTLEMENT_CONFLICT"
        assert proof.kill_chain_stage == "SETTLEMENT_CONFLICT"
        assert set(proof.leg_tx_ids) == {"tx_a", "tx_b"}
        assert proof.economic_exposure_at_risk == 5000.0

    def test_silent_on_unrelated_transactions(self):
        auth = _auth()
        tx1 = _tx(tx_id="tx_a", obligation_id=None)
        tx2 = _tx(tx_id="tx_b", obligation_id=None)
        assert detect_settlement_conflict(auth, [tx1, tx2]) is None

    def test_silent_when_refund_is_on_the_same_rail_that_captured(self):
        """A refund from the SAME rail that captured the funds is a normal reversal, not a conflict."""
        auth = _auth()
        capture = _tx(tx_id="tx_a", rail=PaymentRailType.CARD_TOKEN,
                       obligation_id="oblig_2", settlement_action="CAPTURE")
        refund = _tx(tx_id="tx_b", rail=PaymentRailType.CARD_TOKEN,
                      obligation_id="oblig_2", settlement_action="REFUND")
        assert detect_settlement_conflict(auth, [capture, refund]) is None

    def test_silent_on_a_single_clean_capture(self):
        auth = _auth()
        capture = _tx(obligation_id="oblig_3", settlement_action="CAPTURE")
        assert detect_settlement_conflict(auth, [capture]) is None


class TestReconciliationDriftDetector:
    def test_fires_on_duplicate_capture_same_rail(self):
        auth = _auth()
        first = _tx(tx_id="tx_a", rail=PaymentRailType.CARD_TOKEN,
                    obligation_id="oblig_4", settlement_action="CAPTURE")
        duplicate = _tx(tx_id="tx_b", rail=PaymentRailType.CARD_TOKEN,
                         obligation_id="oblig_4", settlement_action="DUPLICATE_CAPTURE")
        proof = detect_reconciliation_drift(auth, [first, duplicate])
        assert proof is not None
        assert proof.conflict_code == "RECON_02_RECONCILIATION_DRIFT"
        assert proof.kill_chain_stage == "RECONCILIATION_DRIFT"
        assert proof.economic_exposure_at_risk == 5000.0  # the excess (second) capture only

    def test_silent_when_captures_are_on_different_rails(self):
        """Two DIFFERENT obligations' captures on different rails is not a duplicate of one obligation."""
        auth = _auth()
        a = _tx(tx_id="tx_a", rail=PaymentRailType.CARD_TOKEN,
                obligation_id="oblig_5", settlement_action="CAPTURE")
        b = _tx(tx_id="tx_b", rail=PaymentRailType.UPI_CIRCLE,
                obligation_id="oblig_5", settlement_action="CAPTURE")
        assert detect_reconciliation_drift(auth, [a, b]) is None

    def test_silent_on_a_single_clean_capture(self):
        auth = _auth()
        capture = _tx(obligation_id="oblig_6", settlement_action="CAPTURE")
        assert detect_reconciliation_drift(auth, [capture]) is None


class TestEvaluateAllAndContainment:
    def test_evaluate_all_runs_both_detectors(self):
        auth = _auth()
        assert evaluate_all(auth, []) == []

    def test_containment_action_is_proportionate_per_conflict_type(self):
        auth = _auth()
        capture = _tx(tx_id="tx_a", rail=PaymentRailType.CARD_TOKEN,
                       obligation_id="oblig_7", settlement_action="CAPTURE")
        refund = _tx(tx_id="tx_b", rail=PaymentRailType.UPI_CIRCLE,
                      obligation_id="oblig_7", settlement_action="REFUND")
        proof = detect_settlement_conflict(auth, [capture, refund])
        action = apply_settlement_containment(proof)
        assert "SETTLEMENT_HOLD" in action
        assert proof.obligation_id in action


class TestVectorsProduceValidTransactions:
    def test_settlement_conflict_vector(self):
        txs = SettlementConflictVector.generate_attack()
        assert len(txs) == 2
        assert all(isinstance(t, SyntheticTransaction) for t in txs)
        assert txs[0].obligation_id == txs[1].obligation_id
        assert {t.settlement_action for t in txs} == {"CAPTURE", "REFUND"}
        assert {t.rail for t in txs} == {PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE}

    def test_reconciliation_drift_vector(self):
        txs = ReconciliationDriftVector.generate_attack()
        assert len(txs) == 2
        assert txs[0].obligation_id == txs[1].obligation_id
        assert txs[0].rail == txs[1].rail
        assert {t.settlement_action for t in txs} == {"CAPTURE", "DUPLICATE_CAPTURE"}


class TestTaxonomyRegistration:
    def test_both_vectors_registered_as_implemented(self):
        assert IMPLEMENTED[62]["key"] == "SETTLEMENT_CONFLICT"
        assert IMPLEMENTED[63]["key"] == "RECONCILIATION_DRIFT"

    def test_both_vectors_present_and_marked_implemented_in_parsed_taxonomy(self):
        by_id = {v["id"]: v for v in TAXONOMY}
        assert by_id[62]["implemented"] is True
        assert by_id[63]["implemented"] is True
        assert by_id[62]["simulation_status"] == "IMPLEMENTED / EXECUTABLE"
        assert by_id[63]["simulation_status"] == "IMPLEMENTED / EXECUTABLE"

    def test_both_strategies_mapped_to_their_kill_chain_stage(self):
        assert STRATEGY_TO_STAGE["SETTLEMENT_CONFLICT"] == "SETTLEMENT_CONFLICT"
        assert STRATEGY_TO_STAGE["RECONCILIATION_DRIFT"] == "RECONCILIATION_DRIFT"
        assert stage_for_strategy("SETTLEMENT_CONFLICT")["code"] == "SETTLEMENT_CONFLICT"
        assert stage_for_strategy("RECONCILIATION_DRIFT")["code"] == "RECONCILIATION_DRIFT"


class TestOrchestratorEndToEnd:
    def _run(self, round_number: int) -> dict:
        async def go():
            orch = ArenaBattleOrchestrator()
            return await orch.run_round_stream(round_number=round_number, dtl_enabled=True, speed=100.0)
        return asyncio.run(go())

    def test_settlement_conflict_round_is_detected_and_contained(self):
        result = self._run(16)
        assert result["strategy"] == "SETTLEMENT_CONFLICT"
        assert result["detected"] is True
        assert result["winner"] == "BLUE"
        sv = result["settlement_verdict"]
        assert sv["verdict"] == "CONFLICT_DETECTED"
        assert sv["conflict_code"] == "RECON_01_SETTLEMENT_CONFLICT"
        kc = result["kill_chain"]
        assert kc["stage"]["code"] == "SETTLEMENT_CONFLICT"
        assert kc["contained"] is True
        assert kc["economic_exposure_prevented_inr"] > 0

    def test_reconciliation_drift_round_is_detected_and_contained(self):
        result = self._run(17)
        assert result["strategy"] == "RECONCILIATION_DRIFT"
        assert result["detected"] is True
        assert result["winner"] == "BLUE"
        sv = result["settlement_verdict"]
        assert sv["verdict"] == "CONFLICT_DETECTED"
        assert sv["conflict_code"] == "RECON_02_RECONCILIATION_DRIFT"
        kc = result["kill_chain"]
        assert kc["stage"]["code"] == "RECONCILIATION_DRIFT"
        assert kc["contained"] is True

    def test_no_authority_dimension_invariant_fires_for_settlement_conflict(self):
        """
        The vector is designed to stay inside every one of the seven
        authority dimensions - only the reconciliation layer should catch it.
        """
        result = self._run(16)
        for step in result["step_results"]:
            assert step["proof"] is None, "an authority-dimension invariant fired unexpectedly"

    def test_no_authority_dimension_invariant_fires_for_reconciliation_drift(self):
        result = self._run(17)
        for step in result["step_results"]:
            assert step["proof"] is None, "an authority-dimension invariant fired unexpectedly"

    def test_unrelated_round_reports_consistent_settlement_verdict(self):
        result = self._run(2)  # CROSS_RAIL_SPLIT - no obligation_id anywhere
        assert result["settlement_verdict"]["verdict"] == "CONSISTENT"

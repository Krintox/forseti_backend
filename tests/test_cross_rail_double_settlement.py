"""
RECON_03. Found by tracing the settlement module by hand, not by a failing test.

RECON_01 covers capture-vs-REFUND across rails. RECON_02 covers a duplicated
capture on the SAME rail. Neither covered one obligation being CAPTURED on two
different rails - and that is the case this module's thesis is actually about,
because no rail-local view can see it and the money genuinely leaves twice.

A Rs 4,000 obligation captured once on card and once on UPI returned zero
proofs. These tests pin the detector that closes it, and - just as importantly -
pin that it stays quiet on the legitimate shapes.
"""

import pytest

from app.arena.orchestrator import AUTHORITY_ID
from app.dtl.ledger import DTLLedger
from app.models.state import PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.settlement import evaluate_all

CARD, UPI, AP2 = (PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE,
                  PaymentRailType.AGENTIC_AP2)


@pytest.fixture
def auth():
    return DTLLedger().get_authority(AUTHORITY_ID)


def _leg(tx_id, amount, obligation, action, rail):
    tx = SyntheticTransaction(
        tx_id=tx_id, authority_id="a", agent_id="g", rail=rail, amount=amount,
        merchant_id="m", merchant_name="M", merchant_mcc="5411",
        items=[CartItem(sku="S", name="X", category="GROCERY",
                        unit_price=amount, quantity=1)])
    tx.obligation_id = obligation
    tx.settlement_action = action
    return tx


def _codes(auth, legs):
    return [p.conflict_code for p in evaluate_all(auth, legs)]


class TestTheGapItCloses:
    def test_one_obligation_captured_on_two_rails_is_caught(self, auth):
        legs = [_leg("t1", 4000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 4000.0, "OBL", "CAPTURE", UPI)]
        assert "RECON_03_CROSS_RAIL_DOUBLE_SETTLEMENT" in _codes(auth, legs)

    def test_the_amount_at_risk_is_the_EXCESS_not_the_total(self, auth):
        legs = [_leg("t1", 4000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 4000.0, "OBL", "CAPTURE", UPI)]
        proof = next(p for p in evaluate_all(auth, legs)
                     if p.conflict_code == "RECON_03_CROSS_RAIL_DOUBLE_SETTLEMENT")
        assert proof.economic_exposure_at_risk == 4000.0, (
            "one of the two captures was authorised; only the second is exposure"
        )

    def test_three_rails_report_two_legs_of_excess(self, auth):
        legs = [_leg("t1", 4000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 4000.0, "OBL", "CAPTURE", UPI),
                _leg("t3", 4000.0, "OBL", "CAPTURE", AP2)]
        proof = next(p for p in evaluate_all(auth, legs)
                     if p.conflict_code == "RECON_03_CROSS_RAIL_DOUBLE_SETTLEMENT")
        assert proof.economic_exposure_at_risk == 8000.0
        assert len(proof.leg_tx_ids) == 3

    def test_it_does_not_depend_on_a_self_declared_duplicate_label(self, auth):
        """Both legs say plain CAPTURE - nothing labels itself a duplicate."""
        legs = [_leg("t1", 4000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 4000.0, "OBL", "CAPTURE", UPI)]
        assert all(t.settlement_action == "CAPTURE" for t in legs)
        assert _codes(auth, legs)


class TestItStaysQuietOnLegitimateShapes:
    """A detector that fires on normal settlement is worse than none."""

    def test_a_single_capture_is_clean(self, auth):
        assert _codes(auth, [_leg("t1", 900.0, "OBL", "CAPTURE", CARD)]) == []

    def test_capture_then_same_rail_refund_is_clean(self, auth):
        legs = [_leg("t1", 5000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 5000.0, "OBL", "REFUND", CARD)]
        assert _codes(auth, legs) == []

    def test_two_different_obligations_on_two_rails_is_clean(self, auth):
        legs = [_leg("t1", 900.0, "OBL_A", "CAPTURE", CARD),
                _leg("t2", 900.0, "OBL_B", "CAPTURE", UPI)]
        assert _codes(auth, legs) == []


class TestItDoesNotStealTheOtherDetectorsCases:
    def test_same_rail_duplication_still_reports_RECON_02(self, auth):
        legs = [_leg("t1", 5000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 5000.0, "OBL", "DUPLICATE_CAPTURE", CARD)]
        codes = _codes(auth, legs)
        assert "RECON_02_RECONCILIATION_DRIFT" in codes
        assert "RECON_03_CROSS_RAIL_DOUBLE_SETTLEMENT" not in codes

    def test_cross_rail_capture_and_refund_still_reports_RECON_01(self, auth):
        legs = [_leg("t1", 5000.0, "OBL", "CAPTURE", CARD),
                _leg("t2", 5000.0, "OBL", "REFUND", UPI)]
        assert "RECON_01_SETTLEMENT_CONFLICT" in _codes(auth, legs)

"""
Tests for the multidimensional delegated-authority model.

The point of these tests is the distinction that makes FORSETI defensible: an
agent can stay entirely inside the money limit and still act outside the grant.
Each test below pins one dimension and proves that violating it is caught while
the other dimensions remain satisfied - and, just as importantly, that a
transaction inside the whole grant is still allowed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.models.state import DefensePolicy, PaymentRailType, TransactionState
from app.models.transactions import CartItem, SyntheticTransaction

AUTHORITY_ID = "auth_household_grocery_2026"


def _tx(rail=PaymentRailType.UPI_CIRCLE, amount=1000.0, mcc="5411", stored_value=False):
    return SyntheticTransaction(
        tx_id=f"tx_dim_{rail}_{amount}",
        authority_id=AUTHORITY_ID,
        agent_id="agent_test",
        rail=rail,
        amount=amount,
        merchant_id="m_test",
        merchant_name="Test Mart",
        merchant_mcc=mcc,
        items=[
            CartItem(
                sku="SKU_T",
                name="Gift Card" if stored_value else "Milk",
                category="GIFT_CARD" if stored_value else "GROCERY",
                unit_price=amount, quantity=1, is_stored_value=stored_value,
            )
        ],
    )


class TestRailDimension:
    """INV_04 - the dimension a ceiling alone cannot express."""

    def test_rail_outside_grant_is_rejected_even_with_full_headroom(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)

        # Not one rupee has been spent: the AMOUNT dimension is untouched.
        assert auth.authority_headroom == pytest.approx(12000.0)

        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.CARD_TOKEN, 5000.0))
        assert ok is False
        assert proof.invariant_code == "INV_04_UNAUTHORIZED_RAIL"
        assert proof.authority_dimension == "RAIL"

    def test_permitted_rail_still_passes_under_the_same_grant(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.UPI_CIRCLE, 5000.0))
        assert ok is True and proof is None

    def test_rail_containment_consumes_no_headroom(self):
        """A scope violation must not spend the user's authority to refuse it."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = _tx(PaymentRailType.CARD_TOKEN, 5000.0)
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)

        contained, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert contained.state == TransactionState.QUARANTINED
        assert "RAIL_SCOPE_BLOCK" in action
        assert auth.authority_headroom == pytest.approx(12000.0), "refusing must not book spend"


class TestPerTransactionDimension:
    """INV_05 - bounds a single action independently of the aggregate."""

    def test_transaction_above_cap_is_caught_while_budget_is_fine(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)

        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=4000.0))
        assert ok is False
        assert proof.invariant_code == "INV_05_PER_TX_CAP_EXCEEDED"
        assert proof.authority_dimension == "PER_TX"
        # The aggregate ceiling was never the binding constraint.
        assert 4000.0 < auth.global_budget_ceiling

    def test_transaction_exactly_at_cap_is_allowed(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=3000.0))
        assert ok is True

    def test_per_tx_containment_escalates_rather_than_declines(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = _tx(amount=4000.0)
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)
        _, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert "STEP_UP_REQUIRED" in action
        assert auth.active_policy == DefensePolicy.STEP_UP_VERIFICATION


class TestTimeDimension:
    """INV_06 - an elapsed mandate authorises nothing, at any amount."""

    def test_expired_delegation_rejects_a_fully_in_scope_transaction(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 0.0})
        auth = ledger.get_authority(AUTHORITY_ID)

        # Amount, rail, merchant and basket are all perfectly in scope.
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=2500.0))
        assert ok is False
        assert proof.invariant_code == "INV_06_AUTHORITY_EXPIRED"
        assert proof.authority_dimension == "TIME"

    def test_live_delegation_inside_its_window_passes(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 168.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=2500.0))
        assert ok is True

    def test_expiry_is_evaluated_against_a_supplied_clock(self):
        """Time checks must be testable without waiting a week."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 24.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        later = datetime.now(timezone.utc) + timedelta(hours=48)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=100.0), now=later)
        assert ok is False
        assert proof.invariant_code == "INV_06_AUTHORITY_EXPIRED"


class TestMerchantDimension:
    """INV_03 - documented from the start, but previously never enforced at runtime."""

    def test_out_of_scope_mcc_is_now_actually_rejected(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        assert "5311" not in auth.permitted_mccs
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=8000.0, mcc="5311"))
        assert ok is False
        assert proof.invariant_code == "INV_03_UNAUTHORIZED_MCC"
        assert proof.authority_dimension == "MERCHANT"


class TestDimensionInteraction:
    def test_all_violated_dimensions_are_reported_not_just_the_first(self):
        """One transaction can break the grant in several ways at once."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=5000.0, profile={
            "permitted_rails": [PaymentRailType.UPI_CIRCLE],
            "per_transaction_cap": 1000.0,
        })
        auth = ledger.get_authority(AUTHORITY_ID)
        # Wrong rail, over the per-tx cap, out-of-scope MCC, stored value, over budget.
        tx = _tx(PaymentRailType.CARD_TOKEN, amount=9000.0, mcc="5311", stored_value=True)

        proofs = DTLInvariantEngine().evaluate_all(auth, tx)
        codes = {p.invariant_code for p in proofs}
        assert "INV_04_UNAUTHORIZED_RAIL" in codes
        assert "INV_05_PER_TX_CAP_EXCEEDED" in codes
        assert "INV_03_UNAUTHORIZED_MCC" in codes
        assert "INV_02_SEMANTIC_INTENT_DRIFT" in codes
        assert "INV_01_GLOBAL_BUDGET_EXCEEDED" in codes

    def test_registry_covers_every_dimension_exactly_once(self):
        rows = DTLInvariantEngine.registry()
        dims = [r["dimension"] for r in rows]
        assert sorted(dims) == sorted(["TIME", "RAIL", "PER_TX", "MERCHANT", "PURPOSE", "AMOUNT"])
        assert len(rows) == len(set(r["code"] for r in rows))

    def test_default_grant_is_unconstrained_on_the_new_dimensions(self):
        """
        Back-compat: the historical demo (₹10k, any rail, no per-tx cap) must
        behave exactly as before, or every existing result silently changes.
        """
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        assert auth.per_transaction_cap is None
        assert len(auth.permitted_rail_values) == 3
        assert auth.is_expired() is False
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.CARD_TOKEN, 4000.0))
        assert ok is True


class TestAuthorityVector:
    def test_vector_exposes_one_row_per_dimension_with_its_invariant(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        vector = auth.authority_vector()
        assert set(vector) == {"AMOUNT", "PER_TX", "RAIL", "MERCHANT", "PURPOSE", "TIME"}
        assert vector["AMOUNT"]["invariant"] == "INV_01_GLOBAL_BUDGET_EXCEEDED"
        assert vector["RAIL"]["invariant"] == "INV_04_UNAUTHORIZED_RAIL"
        assert vector["TIME"]["expired"] is False

    def test_vector_tracks_scope_changes(self):
        ledger = DTLLedger()
        ledger.update_authority_scope(AUTHORITY_ID, {"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        vector = ledger.get_authority(AUTHORITY_ID).authority_vector()
        assert vector["RAIL"]["granted"] == ["UPI_CIRCLE"]
        assert vector["RAIL"]["unconstrained"] is False

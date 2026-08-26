"""
The adversarial review's runner-up question, asked from the stage:

    "Show me the line where AGENT_SUSPENDED causes a transaction to be rejected."

At the time it was asked there was no such line. Blue's escalation ladder wrote
`active_policy` and a handful of knobs that no authorization path ever read, so
an agent whose mandate was "suspended" kept transacting exactly as before.

These tests are the answer. They deliberately use a transaction that breaches
NOTHING - small amount, permitted rail, permitted MCC, in-scope basket, live
mandate - so the only thing that can reject it is the policy state itself. If
suspension were still decorative, every one of them would fail.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.dtl.invariant_engine import DTLInvariantEngine
from app.models.state import DefensePolicy, DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction

ENGINE = DTLInvariantEngine()


def _auth(policy=DefensePolicy.STANDARD, **kw) -> DTLGlobalAuthorityState:
    base = dict(authority_id="a", principal="p", agent_id="g", global_budget_ceiling=10000.0)
    base.update(kw)
    auth = DTLGlobalAuthorityState(**base)
    auth.active_policy = policy
    return auth


def _innocuous_tx() -> SyntheticTransaction:
    """A transaction with nothing wrong with it, on every dimension."""
    return SyntheticTransaction(
        tx_id="t_clean", authority_id="a", agent_id="g",
        rail=PaymentRailType.CARD_TOKEN, amount=100.0,
        merchant_id="m", merchant_name="Local Kirana", merchant_mcc="5411",
        items=[CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                        unit_price=100.0, quantity=1)],
    )


class TestTheControl:
    """Establish that the transaction really is clean before blaming the policy."""

    def test_under_standard_policy_this_transaction_passes_every_invariant(self):
        valid, proof = ENGINE.evaluate_invariants(_auth(), _innocuous_tx())
        assert valid is True, f"control transaction is not clean: {proof and proof.invariant_code}"
        assert proof is None

    @pytest.mark.parametrize(
        "policy",
        [p for p in DefensePolicy if p is not DefensePolicy.AGENT_SUSPENDED],
    )
    def test_no_other_rung_of_the_ladder_rejects_it(self, policy):
        """Containment without lockout: every lesser rung still lets ₹100 through."""
        valid, proof = ENGINE.evaluate_invariants(_auth(policy=policy), _innocuous_tx())
        assert valid is True, (
            f"{policy.value} rejected an innocuous ₹100 grocery payment "
            f"({proof and proof.invariant_code}) - the ladder is supposed to "
            "narrow authority, not switch it off before the top rung"
        )


class TestSuspensionRejects:
    """The answer to the question."""

    def test_agent_suspended_rejects_the_same_clean_transaction(self):
        valid, proof = ENGINE.evaluate_invariants(
            _auth(policy=DefensePolicy.AGENT_SUSPENDED), _innocuous_tx()
        )
        assert valid is False
        assert proof is not None
        assert proof.invariant_code == "INV_08_MANDATE_SUSPENDED"
        assert proof.severity == "CRITICAL"
        assert proof.drift_score == 1.0

    def test_the_proof_names_the_policy_as_the_cause(self):
        _, proof = ENGINE.evaluate_invariants(
            _auth(policy=DefensePolicy.AGENT_SUSPENDED), _innocuous_tx()
        )
        assert "AGENT_SUSPENDED" in proof.invariant_expression
        assert "suspended" in proof.explanation.lower()

    @pytest.mark.parametrize("amount", [1.0, 100.0, 999.0, 4999.0])
    @pytest.mark.parametrize("rail", list(PaymentRailType))
    def test_no_amount_on_no_rail_is_authorised_while_suspended(self, amount, rail):
        tx = _innocuous_tx()
        tx.amount = amount
        tx.rail = rail
        tx.items[0].unit_price = amount
        valid, proof = ENGINE.evaluate_invariants(
            _auth(policy=DefensePolicy.AGENT_SUSPENDED), tx
        )
        assert valid is False
        assert proof.invariant_code == "INV_08_MANDATE_SUSPENDED"

    def test_suspension_is_checked_before_every_other_dimension(self):
        """
        A suspended mandate authorises nothing, so INV_08 must be the FIRST
        proof returned even when the transaction also breaches other
        dimensions - otherwise the UI would explain the wrong cause.
        """
        tx = _innocuous_tx()
        tx.amount = 50_000.0            # blows the ceiling
        tx.merchant_mcc = "5734"         # out-of-scope MCC
        tx.items[0].unit_price = 50_000.0
        proofs = ENGINE.evaluate_all(_auth(policy=DefensePolicy.AGENT_SUSPENDED), tx)
        assert len(proofs) > 1, "expected multiple dimensions to be breached here"
        assert proofs[0].invariant_code == "INV_08_MANDATE_SUSPENDED"

    def test_lifting_the_suspension_restores_authority(self):
        """Suspension is a pause pending re-consent, not a permanent kill."""
        auth = _auth(policy=DefensePolicy.AGENT_SUSPENDED)
        assert ENGINE.evaluate_invariants(auth, _innocuous_tx())[0] is False
        auth.active_policy = DefensePolicy.STRICT_INVARIANT   # principal re-consents
        assert ENGINE.evaluate_invariants(auth, _innocuous_tx())[0] is True


class TestOverlayAgreesWithTheEngine:
    """What the UI is told must match what the engine does."""

    def test_overlay_reports_total_suspension(self):
        auth = _auth(policy=DefensePolicy.AGENT_SUSPENDED)
        assert auth.policy_overlay()["suspends_all_spend"] is True

    @pytest.mark.parametrize(
        "policy",
        [p for p in DefensePolicy if p is not DefensePolicy.AGENT_SUSPENDED],
    )
    def test_overlay_does_not_claim_suspension_for_lesser_rungs(self, policy):
        assert _auth(policy=policy).policy_overlay()["suspends_all_spend"] is False

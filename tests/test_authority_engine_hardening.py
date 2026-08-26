"""
Regression tests for the core-authority-engine defects found by adversarial
review. Each test pins a specific behaviour that was WRONG and is now right,
and names the failure it guards against so a future change cannot quietly
reintroduce it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.sku_catalogue import classify_item, register, SkuAttestation
from app.models.state import DefensePolicy, DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.simulator.adapters.agentic_adapter import AgenticAp2Adapter
from app.simulator.adapters.upi_adapter import UpiCircleAdapter
from app.tokenization import activate, issue_token, use_token

ENGINE = DTLInvariantEngine()
GOV = AdversarialCostGovernor()


def _auth(**kw) -> DTLGlobalAuthorityState:
    base = dict(authority_id="a", principal="p", agent_id="g", global_budget_ceiling=10000.0)
    base.update(kw)
    return DTLGlobalAuthorityState(**base)


def _tx(*, mcc="5411", amount=1000.0, rail=PaymentRailType.CARD_TOKEN,
        sku="SKU_GROC_01", name="Milk", category="GROCERY", stored=False, vpa=None):
    return SyntheticTransaction(
        tx_id="t", authority_id="a", agent_id="g", rail=rail, amount=amount,
        merchant_id="m", merchant_name="M", merchant_mcc=mcc, vpa_delegate=vpa,
        items=[CartItem(sku=sku, name=name, category=category,
                        unit_price=amount, quantity=1, is_stored_value=stored)],
    )


class TestCostGovernorNeverRewritesTheGrant:
    """F-09. Containment used to subtract the approved amount from the ceiling."""

    def test_partial_auth_books_exposure_and_leaves_ceiling_intact(self):
        auth = _auth()
        tx = SyntheticTransaction(
            tx_id="t", authority_id="a", agent_id="g", rail=PaymentRailType.CARD_TOKEN,
            amount=4000.0, merchant_id="m", merchant_name="M", merchant_mcc="5411",
            items=[
                CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                         unit_price=2500.0, quantity=1),
                CartItem(sku="SKU_GIFT_DIGITAL", name="Gift Card", category="GIFT_CARD",
                         unit_price=1500.0, quantity=1, is_stored_value=True),
            ],
        )
        violations = ENGINE.evaluate_all(auth, tx)
        GOV.apply_containment(auth, tx, GOV.select_governing_proof(violations))
        assert auth.global_budget_ceiling == 10000.0, "the principal's grant was rewritten"
        assert auth.total_exposure_global == pytest.approx(2500.0), "approved value was not booked"

    def test_no_containment_path_mutates_the_ceiling(self):
        """Every branch, not just the one that had the bug."""
        cases = [
            ("rail", _auth(permitted_rails=[PaymentRailType.UPI_CIRCLE]),
             _tx(rail=PaymentRailType.CARD_TOKEN)),
            ("mcc", _auth(permitted_mccs=["4900"]), _tx(mcc="5311")),
            ("per_tx", _auth(per_transaction_cap=200.0), _tx(amount=900.0)),
            ("beneficiary", _auth(beneficiary_scope=["vpa_a@upi"]), _tx(vpa="vpa_b@upi")),
            ("purpose", _auth(), _tx(sku="SKU_GIFT_DIGITAL", category="GIFT_CARD", stored=True)),
            ("amount", _auth(global_budget_ceiling=500.0), _tx(amount=4000.0)),
        ]
        for label, auth, tx in cases:
            before = auth.global_budget_ceiling
            violations = ENGINE.evaluate_all(auth, tx)
            assert violations, f"{label}: expected a violation to contain"
            GOV.apply_containment(auth, tx, GOV.select_governing_proof(violations))
            assert auth.global_budget_ceiling == before, f"{label} branch mutated the ceiling"

    def test_amount_branch_is_reachable_when_purpose_also_fires(self):
        """
        The dispatcher used to fall through to the purpose branch for anything,
        so a budget breach whose cart contained a gift card never reached the
        headroom cap. Precedence is now explicit.
        """
        auth = _auth()
        auth.cumulative_spent_authorized = 9500.0
        tx = _tx(amount=4000.0, sku="SKU_GROC_01")
        violations = ENGINE.evaluate_all(auth, tx)
        codes = {v.invariant_code for v in violations}
        assert "INV_01_GLOBAL_BUDGET_EXCEEDED" in codes
        governing = GOV.select_governing_proof(violations)
        assert governing.invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED"
        _, action = GOV.apply_containment(auth, tx, governing)
        assert "HEADROOM_CAP" in action

    def test_cleared_value_never_exceeds_remaining_headroom(self):
        auth = _auth()
        auth.cumulative_spent_authorized = 8800.0      # Rs 1,200 headroom
        tx = SyntheticTransaction(
            tx_id="t", authority_id="a", agent_id="g", rail=PaymentRailType.CARD_TOKEN,
            amount=8000.0, merchant_id="m", merchant_name="M", merchant_mcc="5411",
            items=[
                CartItem(sku="SKU_GROC_01", name="Groceries", category="GROCERY",
                         unit_price=5000.0, quantity=1),
                CartItem(sku="SKU_GIFT_DIGITAL", name="Gift Card", category="GIFT_CARD",
                         unit_price=3000.0, quantity=1, is_stored_value=True),
            ],
        )
        violations = ENGINE.evaluate_all(auth, tx)
        GOV.apply_containment(auth, tx, GOV.select_governing_proof(violations))
        assert auth.total_exposure_global <= auth.global_budget_ceiling


class TestAttestedSkuCatalogue:
    """F-03. INV_02 reasoned over merchant-supplied free text and was evadable."""

    def test_renaming_a_liquid_instrument_no_longer_evades(self):
        auth = _auth()
        honest = ENGINE.evaluate_all(auth, _tx(
            sku="SKU_GIFT_DIGITAL", name="Amazon Gift Card", category="GIFT_CARD", stored=True))
        renamed = ENGINE.evaluate_all(auth, _tx(
            sku="SKU_GIFT_DIGITAL", name="Prepaid Value Instrument",
            category="MERCHANDISE", stored=False))
        assert any(v.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT" for v in honest)
        assert any(v.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT" for v in renamed), (
            "renaming the SKU and clearing the flag evaded INV_02"
        )

    def test_merchant_misdeclaration_is_recorded_as_evidence(self):
        verdict = classify_item("SKU_GIFT_DIGITAL", "MERCHANDISE", False)
        assert verdict["attested"] is True
        assert verdict["is_liquid"] is True
        assert verdict["misdeclared"] is True

    def test_genuine_groceries_still_pass(self):
        auth = _auth()
        assert not ENGINE.evaluate_all(auth, _tx(sku="SKU_GROC_01"))

    def test_strict_attestation_policy_is_load_bearing(self):
        """
        STRICT_CATALOG_ATTESTATION existed as an enum nothing read. Under it,
        an unattested SKU is refused; under STANDARD it is not.
        """
        tx = _tx(sku="SKU_INVENTED_XYZ", name="Flexi Credit", category="GENERAL")
        lenient = _auth()
        strict = _auth()
        strict.active_policy = DefensePolicy.STRICT_CATALOG_ATTESTATION
        assert not ENGINE.evaluate_all(lenient, tx)
        assert any(v.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT"
                   for v in ENGINE.evaluate_all(strict, tx))

    def test_unattested_sku_still_falls_back_to_merchant_claim(self):
        """Unknown SKU that the merchant DOES declare as stored value is caught."""
        auth = _auth()
        v = ENGINE.evaluate_all(auth, _tx(
            sku="SKU_UNKNOWN_1", name="Gift Card", category="GIFT_CARD", stored=True))
        assert any(x.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT" for x in v)


class TestTokenHonoursTheLiveDelegation:
    """F-07 / F-38. Five of seven dimensions were unenforced at the token layer."""

    @pytest.mark.parametrize("label,mutate,tx_kwargs", [
        ("expired", lambda a: (setattr(a, "validity_window_hours", 0.0),
                               setattr(a, "delegation_created_at",
                                       datetime.now(timezone.utc) - timedelta(hours=5))), {}),
        ("merchant", lambda a: setattr(a, "permitted_mccs", ["4900"]), {"mcc": "5411"}),
        ("per_tx", lambda a: setattr(a, "per_transaction_cap", 200.0), {"amount": 900.0}),
        ("beneficiary", lambda a: setattr(a, "beneficiary_scope", ["vpa_only@upi"]),
         {"vpa": "vpa_other@upi"}),
        ("rail", lambda a: setattr(a, "permitted_rails", [PaymentRailType.CARD_TOKEN]),
         {"rail": PaymentRailType.UPI_CIRCLE}),
    ])
    def test_token_cannot_outlive_a_tightened_delegation(self, label, mutate, tx_kwargs):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="g", principal_id="p", scope="groceries"))
        mutate(auth)
        ok, violation = use_token(token, auth, _tx(**tx_kwargs))
        assert ok is False, f"{label}: token authorised spend outside the live delegation"
        assert violation.violation_code == "TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY"

    def test_token_still_works_inside_a_valid_delegation(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="g", principal_id="p", scope="groceries"))
        ok, violation = use_token(token, auth, _tx(amount=500.0))
        assert ok is True and violation is None


class TestRailAdaptersDoNotFabricate:
    """F-08 / F-42. Rails wrote fields the DTL judged, and claimed unperformed checks."""

    def test_upi_rail_does_not_invent_a_beneficiary(self):
        tx = _tx(rail=PaymentRailType.UPI_CIRCLE, amount=500.0, mcc="4900")
        assert tx.vpa_delegate is None
        UpiCircleAdapter().validate_and_authorize_local(tx)
        assert tx.vpa_delegate is None, "the rail fabricated the field INV_07 judges"

    def test_upi_rail_enforces_the_real_scheme_per_transaction_cap(self):
        """NPCI mandates Rs 5,000/transaction for a delegated secondary user."""
        adapter = UpiCircleAdapter()
        ok, msg = adapter.validate_and_authorize_local(
            _tx(rail=PaymentRailType.UPI_CIRCLE, amount=6000.0))
        assert ok is False and "per-transaction" in msg
        ok2, _ = UpiCircleAdapter().validate_and_authorize_local(
            _tx(rail=PaymentRailType.UPI_CIRCLE, amount=4500.0))
        assert ok2 is True

    def test_ap2_rail_actually_verifies_the_mandate_chain(self):
        adapter = AgenticAp2Adapter()
        groceries = [CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                              unit_price=1000.0, quantity=1)]
        substituted = [CartItem(sku="SKU_GIFT_DIGITAL", name="Gift Card", category="GIFT_CARD",
                                unit_price=1000.0, quantity=1, is_stored_value=True)]
        intent = adapter.compute_intent_hash("Household groceries", 10000.0)
        cart = adapter.compute_cart_hash(intent, 1000.0, groceries)

        def mk(items):
            return SyntheticTransaction(
                tx_id="t", authority_id="a", agent_id="g", rail=PaymentRailType.AGENTIC_AP2,
                amount=1000.0, merchant_id="m", merchant_name="M", merchant_mcc="5411",
                items=items, intent_mandate_hash=intent, cart_mandate_hash=cart)

        assert AgenticAp2Adapter().validate_and_authorize_local(mk(groceries))[0] is True
        ok, msg = AgenticAp2Adapter().validate_and_authorize_local(mk(substituted))
        assert ok is False and "does not match the signed intent" in msg

    def test_ap2_rail_does_not_claim_verification_it_did_not_perform(self):
        tx = _tx(rail=PaymentRailType.AGENTIC_AP2, amount=1000.0)
        AgenticAp2Adapter().validate_and_authorize_local(tx)
        assert "unsigned" in tx.local_rail_message
        assert "verified" not in tx.local_rail_message.lower()

    def test_card_rail_does_not_claim_a_cryptogram_check(self):
        tx = _tx(amount=1000.0)
        AgenticAp2Adapter()  # noqa - symmetry with above
        from app.simulator.adapters.card_adapter import CardTokenAdapter
        CardTokenAdapter().validate_and_authorize_local(tx)
        assert "cryptogram" not in tx.local_rail_message.lower()
        assert tx.merchant_mcc in tx.local_rail_message

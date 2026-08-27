"""
The containment outcome must be machine-readable, not just English.

`containment_action` is a sentence for a human, and it was the ONLY place the
numbers lived. A transaction capped at headroom carried `amount = 6000` (the
attempted figure) while Rs 3,000 actually cleared - recoverable only by parsing
prose. Anything asking "how much really cleared?" had to regex a sentence.

`amount` still holds the ATTEMPTED figure on purpose: it is evidence, and
rewriting it is what the governor was caught doing once already. These tests pin
the structured fields that record what the governor decided about it.
"""

import pytest

from app.arena.orchestrator import AUTHORITY_ID
from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.models.state import DefensePolicy, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction

ENGINE = DTLInvariantEngine()
GOV = AdversarialCostGovernor()

GROCERY = [CartItem(sku="SKU_GROC_01", name="G", category="GROCERY",
                    unit_price=6000.0, quantity=1)]
MIXED = [CartItem(sku="SKU_GROC_01", name="G", category="GROCERY",
                  unit_price=2500.0, quantity=1),
         CartItem(sku="SKU_GIFT_01", name="Gift", category="STORED_VALUE",
                  unit_price=1500.0, quantity=1, is_stored_value=True)]
ALL_STORED = [CartItem(sku="SKU_GIFT_01", name="Gift", category="STORED_VALUE",
                       unit_price=3000.0, quantity=1, is_stored_value=True)]


def _contain(items, amount, *, mcc="5411", rail=PaymentRailType.CARD_TOKEN,
             pre_spend=0.0, **auth_kw):
    led = DTLLedger()
    auth = led.get_authority(AUTHORITY_ID)
    auth.active_policy = DefensePolicy.STRICT_INVARIANT
    for k, v in auth_kw.items():
        setattr(auth, k, v)
    auth.cumulative_spent_authorized += pre_spend
    exposure_before = auth.total_exposure_global

    tx = SyntheticTransaction(
        tx_id="t", authority_id=auth.authority_id, agent_id=auth.agent_id,
        rail=rail, amount=amount, merchant_id="m", merchant_name="M",
        merchant_mcc=mcc, items=items)
    proofs = ENGINE.evaluate_all(auth, tx)
    assert proofs, "fixture did not produce a violation"
    out, _ = GOV.apply_containment(auth, tx, GOV.select_governing_proof(proofs) or proofs[0])
    return out, auth, exposure_before


CASES = [
    ("headroom cap",        dict(items=GROCERY, amount=6000.0, pre_spend=7000.0), "HEADROOM_CAP"),
    ("partial auth",        dict(items=MIXED, amount=4000.0), "PARTIAL_AUTH"),
    ("full quarantine",     dict(items=ALL_STORED, amount=3000.0), "FULL_QUARANTINE"),
    ("rail block",          dict(items=GROCERY, amount=6000.0,
                                 permitted_rails=[PaymentRailType.UPI_CIRCLE]), "RAIL_SCOPE_BLOCK"),
    ("merchant block",      dict(items=GROCERY, amount=6000.0, mcc="5734"), "SCOPE_QUARANTINE"),
    ("ceiling consumed",    dict(items=GROCERY, amount=6000.0, pre_spend=10000.0), "CAPABILITY_CONTAINED"),
]


class TestEveryBranchRecordsItsDecision:
    @pytest.mark.parametrize("label,kw,expected_code", CASES)
    def test_the_containment_code_is_set(self, label, kw, expected_code):
        out, _, _ = _contain(**kw)
        assert out.containment_code == expected_code

    @pytest.mark.parametrize("label,kw,expected_code", CASES)
    def test_authorized_plus_quarantined_equals_attempted(self, label, kw, expected_code):
        out, _, _ = _contain(**kw)
        assert out.authorized_amount is not None
        assert out.quarantined_amount is not None
        assert out.authorized_amount + out.quarantined_amount == pytest.approx(out.amount, abs=0.01), (
            f"{label}: the split does not account for the whole attempted amount"
        )

    @pytest.mark.parametrize("label,kw,expected_code", CASES)
    def test_the_attempted_amount_is_never_rewritten(self, label, kw, expected_code):
        out, _, _ = _contain(**kw)
        assert out.amount == kw["amount"], (
            "amount must keep the ATTEMPTED figure - it is evidence"
        )


class TestTheStructuredFigureMatchesTheLedger:
    """The whole point: no parsing needed to know what really cleared."""

    @pytest.mark.parametrize("label,kw,expected_code", CASES)
    def test_authorized_amount_equals_what_was_actually_booked(self, label, kw, expected_code):
        out, auth, exposure_before = _contain(**kw)
        booked = auth.total_exposure_global - exposure_before
        assert booked == pytest.approx(out.authorized_amount, abs=0.01), (
            f"{label}: reported authorized_amount {out.authorized_amount} but the ledger "
            f"moved by {booked}"
        )

    def test_a_blocked_branch_books_nothing(self):
        out, auth, before = _contain(items=GROCERY, amount=6000.0,
                                     permitted_rails=[PaymentRailType.UPI_CIRCLE])
        assert out.authorized_amount == 0.0
        assert auth.total_exposure_global == before

    def test_the_ceiling_is_never_touched_by_any_branch(self):
        for _, kw, _ in CASES:
            out, auth, _ = _contain(**kw)
            assert auth.global_budget_ceiling == 10000.0, (
                "a containment branch rewrote the principal's grant"
            )

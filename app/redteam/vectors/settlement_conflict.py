"""
Vector 62 - Settlement Conflict (Kill Chain stage 10, RECON_01).

"₹5,000 for the weekly grocery order" is a single economic obligation. Card
Token captures it; UPI Circle, independently and just as validly from its own
point of view, reports a REFUND against the same obligation. Amount, rail,
merchant category, beneficiary and purpose are all inside the delegation on
both legs - there is no authority-dimension violation here at all. The
failure only exists in the disagreement between what the two rails each
believe happened to the same money, which is exactly what
app/settlement/reconciliation.py's detect_settlement_conflict checks for.
"""

from typing import List

from ...models.state import PaymentRailType
from ...models.transactions import CartItem, SyntheticTransaction

OBLIGATION_ID = "oblig_settlement_conflict_001"

# A ceiling comfortably above the ₹10,000 both legs sum to, applied
# regardless of whatever ceiling the operator last configured - the same
# determinism guarantee STRATEGY_AUTHORITY_PROFILE gives every other
# non-monetary vector. Without it, this vector's outcome would depend on
# accidental operator state instead of only ever demonstrating the
# reconciliation failure it exists to prove.
SETTLEMENT_CONFLICT_PROFILE = {"global_budget_ceiling": 12000.0}


class SettlementConflictVector:
    """
    Vector: Settlement Conflict (RECON_01).

    Card Token captures ₹5,000; UPI Circle reports a refund of the same
    obligation on a different rail than the one that captured it.
    """

    authority_profile = SETTLEMENT_CONFLICT_PROFILE

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        captured = SyntheticTransaction(
            tx_id="tx_settlement_capture_001",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.CARD_TOKEN,
            amount=5000.0,
            merchant_id="merch_supermarket_alpha",
            merchant_name="Nature's Basket Supermarket",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GROC_WEEKLY", name="Weekly Grocery Order",
                             category="GROCERY", unit_price=5000.0, quantity=1)],
            obligation_id=OBLIGATION_ID,
            settlement_action="CAPTURE",
            is_anomalous_red_attack=True,
            attack_primitive_type="SETTLEMENT_CONFLICT",
        )
        conflicting_refund = SyntheticTransaction(
            tx_id="tx_settlement_refund_002",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.UPI_CIRCLE,
            amount=5000.0,
            merchant_id="merch_supermarket_alpha",
            merchant_name="Nature's Basket Supermarket",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GROC_WEEKLY", name="Weekly Grocery Order",
                             category="GROCERY", unit_price=5000.0, quantity=1)],
            obligation_id=OBLIGATION_ID,
            settlement_action="REFUND",
            is_anomalous_red_attack=True,
            attack_primitive_type="SETTLEMENT_CONFLICT",
        )
        return [captured, conflicting_refund]

"""
Vector 63 - Reconciliation Drift (Kill Chain stage 11, RECON_02).

A settlement message for a single ₹5,000 obligation is applied twice on the
SAME rail - the synthetic model of a duplicated/replayed settlement event,
not a cross-rail disagreement (that is Settlement Conflict, vector 62). Both
legs are individually well-formed local captures; the drift only exists in
the obligation's reconciled total (₹10,000 booked against a ₹5,000
authorised obligation), which app/settlement/reconciliation.py's
detect_reconciliation_drift checks for.
"""

from typing import List

from ...models.state import PaymentRailType
from ...models.transactions import CartItem, SyntheticTransaction

OBLIGATION_ID = "oblig_reconciliation_drift_001"

# Same determinism guarantee as SettlementConflictVector - see that module's
# comment on SETTLEMENT_CONFLICT_PROFILE.
RECONCILIATION_DRIFT_PROFILE = {"global_budget_ceiling": 12000.0}


class ReconciliationDriftVector:
    """
    Vector: Reconciliation Drift (RECON_02).

    Card Token captures the same ₹5,000 obligation twice - a replayed
    settlement message rather than a second, separately-authorised purchase.
    """

    authority_profile = RECONCILIATION_DRIFT_PROFILE

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        first_capture = SyntheticTransaction(
            tx_id="tx_reconciliation_capture_001",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.CARD_TOKEN,
            amount=5000.0,
            merchant_id="merch_supermarket_alpha",
            merchant_name="Nature's Basket Supermarket",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GROC_MONTHLY", name="Monthly Grocery Order",
                             category="GROCERY", unit_price=5000.0, quantity=1)],
            obligation_id=OBLIGATION_ID,
            settlement_action="CAPTURE",
            is_anomalous_red_attack=True,
            attack_primitive_type="RECONCILIATION_DRIFT",
        )
        duplicate_capture = SyntheticTransaction(
            tx_id="tx_reconciliation_capture_002",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.CARD_TOKEN,
            amount=5000.0,
            merchant_id="merch_supermarket_alpha",
            merchant_name="Nature's Basket Supermarket",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GROC_MONTHLY", name="Monthly Grocery Order",
                             category="GROCERY", unit_price=5000.0, quantity=1)],
            obligation_id=OBLIGATION_ID,
            settlement_action="DUPLICATE_CAPTURE",
            is_anomalous_red_attack=True,
            attack_primitive_type="RECONCILIATION_DRIFT",
        )
        return [first_capture, duplicate_capture]

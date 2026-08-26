from typing import List
from ...models.transactions import SyntheticTransaction, CartItem
from ...models.state import PaymentRailType

class IntentLaunderingVector:
    """
    Vector 2: Intent Laundering (Semantic Drift).

    A grocery mandate (MCC 5411) is used to buy liquid stored value alongside
    genuine groceries at a legitimate supermarket. The local card rail approves
    because the MCC is in scope and the amount is under its own ceiling.

    RATIO FIX. This previously put Rs 8,500 of gift cards against Rs 1,000 of
    groceries - 89% of the basket in one transaction. No launderer converts 89%
    in a single visit to one merchant; that is precisely the "one obvious
    spike" this project's own CONSTRAINT_EROSION docstring correctly identifies
    as unrealistic. The critique of this vector was already written elsewhere
    in the repo, and this vector was left as the demo anyway.

    It is now ~34% - a realistic slice that a cautious actor would expect to
    pass unnoticed, and small enough that a threshold-tuned detector plausibly
    would miss it. INV_02 catches it regardless, because it is a membership
    check on the attested catalogue rather than a proportion threshold, which
    is the property actually worth demonstrating.
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> SyntheticTransaction:
        return SyntheticTransaction(
            tx_id="tx_intent_laundering_001",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.CARD_TOKEN,
            amount=6400.0,
            merchant_id="merch_hybrid_retailer",
            merchant_name="Gourmet Mega-Store & Vouchers",
            merchant_mcc="5411", # Supermarket / Grocery
            items=[
                CartItem(sku="SKU_LEGIT_MILK", name="Organic Farm Fresh Milk (2L)", category="GROCERY", unit_price=200.0, quantity=2, is_stored_value=False),
                CartItem(sku="SKU_LEGIT_BREAD", name="Whole Wheat Sourdough Bread", category="GROCERY", unit_price=100.0, quantity=1, is_stored_value=False),
                CartItem(sku="SKU_LEGIT_FRUIT", name="Imported Kiwi & Berries", category="GROCERY", unit_price=500.0, quantity=1, is_stored_value=False),
                CartItem(sku="SKU_GROC_02", name="Household Cleaning & Staples", category="GROCERY", unit_price=1500.0, quantity=1, is_stored_value=False),
                # ~34% of the basket, not 89%.
                CartItem(sku="SKU_GIFT_DIGITAL", name="Multi-Brand Voucher", category="GIFT_CARD", unit_price=2200.0, quantity=1, is_stored_value=True)
            ],
            is_anomalous_red_attack=True,
        )

from typing import List, Dict
from ...models.transactions import SyntheticTransaction, CartItem
from ...models.state import PaymentRailType

class CrossRailSplitVector:
    """
    Vector 1 (Flagship): Cross-Rail Budget Splitting
    Slices an unauthorized ₹12,000 spend into three ₹4,000 transactions across
    Card Token, UPI Circle, and AP2 adapters.
    Each is individually <= ₹10k limit and passes local checks,
    but collectively violates the ₹10,000 global budget.
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        tx_card = SyntheticTransaction(
            tx_id="tx_split_card_001",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.CARD_TOKEN,
            amount=4000.0,
            merchant_id="merch_supermarket_alpha",
            merchant_name="Nature's Basket Supermarket",
            merchant_mcc="5411",
            items=[
                CartItem(sku="SKU_GROC_01", name="Premium Olive Oil & Organic Staples", category="GROCERY", unit_price=4000.0, quantity=1)
            ],
            is_anomalous_red_attack=True,
            attack_primitive_type="CROSS_RAIL_SPLIT"
        )
        
        tx_upi = SyntheticTransaction(
            tx_id="tx_split_upi_002",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.UPI_CIRCLE,
            amount=4000.0,
            merchant_id="merch_quick_grocer_beta",
            merchant_name="Blinkit Quick Delivery",
            merchant_mcc="5411",
            items=[
                CartItem(sku="SKU_GROC_02", name="Artisanal Bakery & Gourmet Dairy", category="GROCERY", unit_price=4000.0, quantity=1)
            ],
            is_anomalous_red_attack=True,
            attack_primitive_type="CROSS_RAIL_SPLIT"
        )
        
        tx_ap2 = SyntheticTransaction(
            tx_id="tx_split_ap2_003",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.AGENTIC_AP2,
            amount=4000.0,
            merchant_id="merch_hypermarket_gamma",
            merchant_name="Reliance Smart Hypermarket",
            merchant_mcc="5411",
            items=[
                CartItem(sku="SKU_GROC_03", name="Monthly Household Cleaning Consumables", category="GROCERY", unit_price=4000.0, quantity=1)
            ],
            is_anomalous_red_attack=True,
            attack_primitive_type="CROSS_RAIL_SPLIT"
        )
        
        return [tx_card, tx_upi, tx_ap2]

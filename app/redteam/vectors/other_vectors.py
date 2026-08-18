from typing import List
from ...models.transactions import SyntheticTransaction, CartItem
from ...models.state import PaymentRailType

class BaselinePoisonVector:
    """
    Vector 3: Adaptive Baseline Poisoning
    Fires 10 low-amplitude micro-transactions (₹300 - ₹500) over UPI to artificially
    inflate the statistical baseline before executing an uncharacteristic burst.
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        txs = []
        for i in range(1, 6):
            txs.append(
                SyntheticTransaction(
                    tx_id=f"tx_poison_micro_{i:03d}",
                    authority_id=authority_id,
                    agent_id="agent_household_butler",
                    rail=PaymentRailType.UPI_CIRCLE,
                    amount=450.0,
                    merchant_id="merch_local_vendor",
                    merchant_name=f"Local Kirana Vendor #{i}",
                    merchant_mcc="5411",
                    items=[
                        CartItem(sku=f"SKU_MICRO_{i}", name="Daily Fresh Vegetables", category="GROCERY", unit_price=450.0, quantity=1)
                    ],
                    is_anomalous_red_attack=True,
                    attack_primitive_type="BASELINE_POISONING"
                )
            )
        return txs

# Alias
BaselinePoisoningVector = BaselinePoisonVector

class RevocationFloodVector:
    """
    Vector 4: Mandate Revocation Flooding (Immune Overreaction Attack)
    Fires rapid-fire grant/revoke actions to induce race-conditions in asynchronous token state.
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        return [
            SyntheticTransaction(
                tx_id=f"tx_flood_revoke_{i}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=PaymentRailType.AGENTIC_AP2,
                amount=9900.0,
                merchant_id="merch_rapid_checkout",
                merchant_name="Fast Mandate Exchange",
                merchant_mcc="5411",
                items=[CartItem(sku="SKU_BURST", name="Flash Sale Grocery Hamper", category="GROCERY", unit_price=9900.0, quantity=1)],
                is_anomalous_red_attack=True,
                attack_primitive_type="REVOCATION_FLOOD"
            ) for i in range(1, 3)
        ]

class VelocitySpikeVector:
    """
    Vector 5: Card Testing Velocity Burst
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        return [
            SyntheticTransaction(
                tx_id=f"tx_velocity_probe_{i}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=PaymentRailType.CARD_TOKEN,
                amount=200.0 * i,
                merchant_id=f"merch_probe_{i}",
                merchant_name="Automated POS Terminal",
                merchant_mcc="5411",
                items=[CartItem(sku="SKU_PROBE", name="Test Micro Item", category="GROCERY", unit_price=200.0 * i, quantity=1)],
                is_anomalous_red_attack=True,
                attack_primitive_type="VELOCITY_BURST"
            ) for i in range(1, 4)
        ]

class ScopeCreepVector:
    """
    Vector 6: Sub-Agent Scope Creep
    """
    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> SyntheticTransaction:
        return SyntheticTransaction(
            tx_id="tx_scope_creep_001",
            authority_id=authority_id,
            agent_id="agent_sub_delegate_level3",
            rail=PaymentRailType.CARD_TOKEN,
            amount=8000.0,
            merchant_id="merch_luxury_goods",
            merchant_name="Department Store & Electronics",
            merchant_mcc="5311", # Department store outside strict grocery scope
            items=[CartItem(sku="SKU_ELEC_WATCH", name="Smart Fitness Tracker", category="ELECTRONICS", unit_price=8000.0, quantity=1)],
            is_anomalous_red_attack=True,
            attack_primitive_type="SCOPE_CREEP"
        )

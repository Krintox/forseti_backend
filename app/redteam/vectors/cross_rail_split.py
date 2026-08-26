"""
Vector 1 (Flagship): Cross-Rail Budget Splitting.

WHAT WAS WRONG. This is the vector the entire pitch rests on, and it was a
unit-test fixture. Three transactions of exactly Rs 4,000, same agent, same MCC
(5411), sequentially numbered tx_ids (`tx_split_card_001`, `..._upi_002`,
`..._ap2_003`), emitted from one function call about two seconds apart, each
carrying `attack_primitive_type="CROSS_RAIL_SPLIT"`.

Every one of those is a correlation key, and three of them are properties a
real splitter would specifically destroy - because destroying correlation IS
the technique. Documented structuring varies amounts deliberately, and smurfing
distributes across accounts, identities, locations and time precisely so that
no single correlation key survives. The old vector preserved every one of them
and then congratulated the defence for using one.

WHAT IT IS NOW. Amounts are uneven and non-round. Merchants differ per leg.
Identifiers are unlinkable. Legs land on different merchant categories inside
the permitted set. What it does NOT vary is the thing that actually matters:
the legs still belong to one delegated authority, and the aggregate still
exceeds the grant.

THAT IS THE POINT, and it is stronger than the old version. The defence does
not depend on any correlation key the attacker controls - not amount, not
merchant, not timing, not identifier shape. It depends on aggregate exposure
against a ceiling, which the attacker cannot vary without abandoning the
objective. An attack that evades every heuristic and is still caught is a much
better demonstration than one that trips a heuristic it was built to trip.
"""

import random
from typing import List

from ...models.transactions import SyntheticTransaction, CartItem
from ...models.state import PaymentRailType

# Seeded for reproducible demos; the SHAPE is varied, not fixed.
_RNG = random.Random(20260825)

# Legs draw from DIFFERENT merchants that are all INSIDE the delegated
# categories. Both halves matter:
#
#   * different merchants - destroys the merchant correlation key;
#   * all in-scope MCCs - a competent splitter picks compliant merchants
#     precisely so that no scope check fires. Landing a leg on an
#     out-of-scope category would trip INV_03 and hand the defence a cheaper
#     win than the one being demonstrated.
#
# The result is a vector where EVERY dimension except the aggregate is clean,
# which is what makes it a test of the aggregate.
_MERCHANTS = [
    ("merch_fresh_direct", "Fresh Direct Mart", "5411"),
    ("merch_city_supermart", "City Supermart", "5411"),
    ("merch_daily_grocer", "Daily Grocer Co-op", "5411"),
    ("merch_corner_kirana", "Corner Kirana Store", "5411"),
    ("merch_greenleaf_organics", "Greenleaf Organics", "5411"),
    ("merch_quickmart_misc", "QuickMart Miscellaneous", "5499"),
]

_BASKETS = [
    ("SKU_GROC_01", "Household Consumables", "GROCERY"),
    ("SKU_GROC_02", "Fresh Produce & Dairy", "GROCERY"),
    ("SKU_GROC_03", "Home Cleaning Supplies", "GROCERY"),
    ("SKU_SPLIT_01", "General Retail Order", "RETAIL"),
]


class CrossRailSplitVector:
    """
    Slices one over-budget objective across three rails so that no single rail
    sees a violation, while destroying the correlation keys a real analyst
    would pivot on.
    """

    # The tightest per-transaction ceiling across the three rails. UPI Circle
    # mandates Rs 5,000 for a delegated secondary user, so that is the binding
    # one. Every leg is sized BELOW it on purpose: a splitter whose leg trips a
    # local rail limit has failed at the first step, and the vector's entire
    # premise is that each leg is individually unremarkable and locally
    # approved. Keeping legs under this bound is the attacker being competent,
    # not the test being tuned.
    _TIGHTEST_RAIL_PER_TX = 5000.0
    _LEG_HEADROOM = 0.92          # sit clearly under it, not flush against it

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026",
                        target_total: float = 12600.0) -> List[SyntheticTransaction]:
        rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE,
                 PaymentRailType.AGENTIC_AP2]
        _RNG.shuffle(rails)

        # Uneven, non-round split. Real structuring never repeats a figure -
        # identical amounts are the single loudest signal available.
        weights = [_RNG.uniform(0.28, 0.38) for _ in rails]
        scale = target_total / sum(weights)
        cap = CrossRailSplitVector._TIGHTEST_RAIL_PER_TX * CrossRailSplitVector._LEG_HEADROOM
        amounts = [round(min(w * scale, cap), 2) for w in weights]

        chosen = _RNG.sample(_MERCHANTS, k=3)
        txs: List[SyntheticTransaction] = []
        for rail, amount, (mid, mname, mcc) in zip(rails, amounts, chosen):
            sku, item_name, category = _BASKETS[_RNG.randrange(len(_BASKETS))]
            txs.append(SyntheticTransaction(
                # Unlinkable identifiers: sequential ids were a free correlation
                # key that a real splitter would never emit.
                tx_id=f"tx_{_RNG.randrange(10**10):010x}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=rail,
                amount=amount,
                merchant_id=mid,
                merchant_name=mname,
                merchant_mcc=mcc,
                items=[CartItem(sku=sku, name=item_name, category=category,
                                unit_price=amount, quantity=1)],
                is_anomalous_red_attack=True,
            ))
        return txs

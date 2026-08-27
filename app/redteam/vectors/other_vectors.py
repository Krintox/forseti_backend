"""
Red-team vectors 3-6.

Every vector in this file was rewritten after an adversarial review found that
each one either did not implement the mechanism its docstring described, or
implemented it in a way no real adversary would choose. The specific failures
are named in each class, because "we fixed it" is worth less than "here is what
was wrong".

A note that applies to all of them: a real attacker's technique is to DESTROY
correlation - vary amounts, merchants, timing, identity. Vectors that preserve
every correlation key and are then caught do not demonstrate a working defence;
they demonstrate that the arithmetic runs. Where these vectors still simplify,
the simplification is stated rather than dressed up.
"""

import random
from typing import List

from ...models.transactions import SyntheticTransaction, CartItem
from ...models.state import PaymentRailType

# Vectors are deterministic per run so a demo is reproducible, but their SHAPE
# is randomised rather than fixed - see _jitter.
_RNG = random.Random(1337)


def _jitter(base: float, spread: float = 0.22) -> float:
    """Amount variation. Real structuring never repeats a round number."""
    return round(base * _RNG.uniform(1.0 - spread, 1.0 + spread), 2)



# Merchants whose MCC is INSIDE the default household grant (5411 groceries,
# 5499 misc food, 4900 utilities).
#
# Why this exists: several vectors previously drew from hand-written lists that
# happened to include out-of-scope MCCs - 5812 restaurants in the conditioning
# legs, 5045 electronics in the velocity probes, 5311/5734 in the revocation
# churn. The DTL then raised INV_03_UNAUTHORIZED_MCC and the vector was
# "caught", but for MERCHANT scope rather than for the mechanism it exists to
# demonstrate. Worse, BaselinePoisonVector's own docstring claimed its
# conditioning legs were unremarkable and reported WITHIN_AUTHORITY, which was
# simply untrue of two of them.
#
# A vector should be caught by the dimension it is testing. Where a vector wants
# a MERCHANT breach it should reach for OUT_OF_SCOPE_MERCHANTS deliberately.
IN_SCOPE_MERCHANTS = [
    ("merch_corner_kirana", "Corner Kirana Store", "5411"),
    ("merch_daily_grocer", "Daily Grocer Co-op", "5411"),
    ("merch_city_supermart", "City Supermart", "5411"),
    ("merch_greenleaf_organics", "Greenleaf Organics", "5411"),
    ("merch_quickmart_misc", "QuickMart Miscellaneous", "5499"),
    ("merch_micro_pos", "Automated Micro POS", "5499"),
    ("merch_metro_utilities", "Metro Utilities Board", "4900"),
]

#: For vectors whose POINT is a merchant-scope breach.
OUT_OF_SCOPE_MERCHANTS = [
    ("merch_bistro_lane", "Bistro Lane", "5812"),
    ("merch_gadget_world", "Gadget World", "5045"),
    ("merch_metro_dept", "Metro Department Store", "5311"),
    ("merch_software_mart", "Software Mart", "5734"),
]


class BaselinePoisonVector:
    """
    Vector 3: Adaptive Baseline Poisoning.

    WHAT WAS WRONG. The docstring promised "10 low-amplitude micro-transactions
    (Rs 300-500) ... before executing an uncharacteristic burst". The code
    emitted FIVE transactions of exactly Rs 450.00 each, with no burst at all -
    three specific claims, none true. Worse, poisoning requires a target that
    ADAPTS: a rolling baseline or an online-learning threshold. FORSETI's
    detector is a static XGBoost artifact loaded from disk, so there was
    nothing in the system for this attack to poison.

    WHAT IT IS NOW. An honest conditioning-then-strike pattern: a run of small,
    varied, in-scope purchases that establish a plausible spending history,
    followed by a strike sized against the headroom that history helped build.
    The conditioning legs are genuinely unremarkable - that is the point, and
    it is why the DTL correctly reports WITHIN_AUTHORITY for them rather than
    pretending they are violations.

    HONEST LIMITATION, STATED: this conditions the WINDOWED FEATURES the
    detector actually reads (tx_velocity_1h, amount_deviation_from_rail_mean,
    mcc_entropy), which is a real effect now that serving carries history. It
    does NOT poison model weights - no component of FORSETI learns online, and
    claiming otherwise would be the same overreach this rewrite is fixing.
    """

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        # In-scope by construction: the conditioning legs must really be
        # unremarkable, or the docstring below is false. Two of the five
        # merchants here used to be MCC 5812, which the grant does not permit.
        merchants = IN_SCOPE_MERCHANTS
        rails = [PaymentRailType.UPI_CIRCLE, PaymentRailType.CARD_TOKEN]
        txs: List[SyntheticTransaction] = []

        # Phase 1: conditioning. Ten legs, varied amount / merchant / rail.
        for i in range(1, 11):
            mid, mname, mcc = merchants[i % len(merchants)]
            amt = _jitter(400.0, spread=0.28)
            txs.append(SyntheticTransaction(
                tx_id=f"tx_cond_{_RNG.randrange(10**6):06d}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=rails[i % len(rails)],
                amount=amt,
                merchant_id=mid, merchant_name=mname, merchant_mcc=mcc,
                items=[CartItem(sku="SKU_GROC_01", name="Daily Fresh Produce",
                                category="GROCERY", unit_price=amt, quantity=1)],
                # Conditioning legs are genuinely in-scope. Marking them as
                # attacks would be labelling ordinary spending as fraud.
                is_anomalous_red_attack=False,
            ))

        # Phase 2: the strike the conditioning was for.
        strike = _jitter(5200.0, spread=0.15)
        txs.append(SyntheticTransaction(
            tx_id=f"tx_strike_{_RNG.randrange(10**6):06d}",
            authority_id=authority_id,
            agent_id="agent_household_butler",
            rail=PaymentRailType.UPI_CIRCLE,
            amount=strike,
            merchant_id="merch_megastore_vouch",
            merchant_name="Gourmet Mega Store & Vouchers",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GIFT_DIGITAL", name="Multi-Brand Voucher Pack",
                            category="GIFT_CARD", unit_price=strike, quantity=1,
                            is_stored_value=True)],
            is_anomalous_red_attack=True,
        ))
        return txs


# Alias retained: taxonomy.py and the orchestrator both reference this name.
BaselinePoisoningVector = BaselinePoisonVector


class RevocationFloodVector:
    """
    Vector 4: Mandate Revocation Flooding.

    WHAT WAS WRONG. This was the widest gap between description and code in the
    project. The docstring promised "rapid-fire grant/revoke actions to induce
    race-conditions in asynchronous token state", and its adaptive-planner
    profile claimed it "exploits: Asynchronous finality race between revoke and
    re-grant". The implementation was TWO transactions of Rs 9,900 on one rail.
    No revoke call, no grant call, no thread, no async, no shared mutable state
    accessed concurrently - anywhere in the file. It was not a simplified race
    condition; it was two large payments wearing a docstring.

    WHAT IT IS NOW. The vector carries an explicit `lifecycle_events` script -
    a real interleaving of REVOKE and REGRANT around the spend legs - which the
    orchestrator replays against the ledger. The attack's actual bet is that a
    re-granted mandate resets per-rail state while the DTL's aggregate view
    does not, so spend straddling the churn is double-counted by the rails and
    correctly single-counted by the ledger.

    The concurrency the original docstring implied is genuinely exercised, but
    in `tests/test_ledger_concurrency.py` against the ledger itself, where a
    race can be constructed deterministically - not narrated here.
    """

    # Replayed by the orchestrator against the ledger, in order.
    LIFECYCLE_SCRIPT = [
        ("REVOKE", None), ("REGRANT", None),
        ("SPEND", 0), ("REVOKE", None), ("REGRANT", None),
        ("SPEND", 1), ("REGRANT", None), ("SPEND", 2),
    ]

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        legs = [
            # All in scope. The mechanism under test is the REVOKE/REGRANT
            # churn and the aggregate that survives it - not merchant category.
            # Two of these legs were 5734/5311, so INV_03 fired first and the
            # lifecycle story never got to make its point.
            (PaymentRailType.AGENTIC_AP2, 4100.0, "merch_quickmart_misc", "QuickMart Miscellaneous", "5499"),
            (PaymentRailType.UPI_CIRCLE, 3400.0, "merch_valuemart_plus", "ValueMart Plus", "5411"),
            (PaymentRailType.CARD_TOKEN, 4700.0, "merch_metro_utilities", "Metro Utilities Board", "4900"),
        ]
        return [
            SyntheticTransaction(
                tx_id=f"tx_regrant_{_RNG.randrange(10**6):06d}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=rail,
                amount=_jitter(amt, spread=0.18),
                merchant_id=mid, merchant_name=mname, merchant_mcc=mcc,
                items=[CartItem(sku="SKU_REVOC_DIG", name="Priority Fulfilment Order",
                                category="DIGITAL", unit_price=amt, quantity=1)],
                is_anomalous_red_attack=True,
            )
            for rail, amt, mid, mname, mcc in legs
        ]


class VelocitySpikeVector:
    """
    Vector 5: Card-Testing Velocity Burst.

    WHAT WAS WRONG. Real card testing fires thousands of authorisation attempts
    per minute and is identified by an abnormally high DECLINE rate. This fired
    three transactions of exactly Rs 200 / Rs 400 / Rs 600 - a linear ramp,
    which is the single most conspicuous sequence available, and all three were
    approved. It was also 43% of all fraud rows in the training set, making the
    least relevant vector the largest contributor to the headline metric.

    WHAT IT IS NOW. Twenty-four probes with randomised sub-threshold amounts
    across merchants and rails, which is what the technique actually looks like
    at demo scale. The count is a deliberate compromise and is stated as one:
    a genuine burst is orders of magnitude larger, and rendering 4,000 events
    would tell a judge nothing a legible 24 does not.

    SCOPE HONESTY: card testing validates stolen credentials. It is only
    tangentially about delegated authority, which is this project's actual
    thesis. It is retained because velocity IS a real signal the windowed
    features read - not because it demonstrates the DTL.
    """

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        # In scope, so this vector is judged on VELOCITY rather than caught
        # incidentally on merchant category (Gadget World, MCC 5045, used to sit
        # in this list and did exactly that).
        merchants = IN_SCOPE_MERCHANTS
        rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.CARD_TOKEN,
                 PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2]
        out: List[SyntheticTransaction] = []
        for i in range(24):
            mid, mname, mcc = merchants[_RNG.randrange(len(merchants))]
            amt = round(_RNG.uniform(19.0, 240.0), 2)   # sub-threshold, non-monotonic
            out.append(SyntheticTransaction(
                tx_id=f"tx_probe_{_RNG.randrange(10**7):07d}",
                authority_id=authority_id,
                agent_id="agent_household_butler",
                rail=rails[_RNG.randrange(len(rails))],
                amount=amt,
                merchant_id=mid, merchant_name=mname, merchant_mcc=mcc,
                items=[CartItem(sku="SKU_MICRO_PROBE", name="Small Basket Item",
                                category="MISC", unit_price=amt, quantity=1)],
                is_anomalous_red_attack=True,
            ))
        return out


class ScopeCreepVector:
    """
    Vector 6: Sub-Agent Scope Creep.

    WHAT WAS WRONG. The vector claimed "sub-agent delegation widens the economic
    scope beyond the parent grant" and implemented it as ONE department-store
    purchase with `agent_id="agent_sub_delegate_level3"`. The sub-agent was a
    string in a field: nothing issued it, nothing scoped it, nothing chained
    authority to it. Rename the field and the vector is unchanged. Meanwhile
    the two features named for sub-agents (`delegation_fanout_count`,
    `active_subagents_count`) counted RAILS, not agents.

    WHAT IT IS NOW. `DELEGATION_REQUEST` describes a REAL sub-delegation the
    orchestrator issues through `DelegationChainRegistry` - carving a scoped
    pool out of the parent grant, which is also what finally populates
    `reserved_spend_global`. The attack is then a genuine boundary test: the
    sub-agent transacts OUTSIDE the link it actually holds, and the chain
    refuses it with a named `CHAIN_*` code.

    This is the vector that changed most, because agent-to-agent delegation is
    the genuinely novel problem in agentic payments (surface S7 in this
    project's own taxonomy) and it previously had no implementation at all.
    """

    # Consumed by the orchestrator to issue a real link before the attack runs.
    DELEGATION_REQUEST = {
        "grantor_id": "agent_household_butler",
        "grantee_id": "agent_grocery_subagent",
        "reserved_pool": 3000.0,
        "permitted_mccs": ["5411"],          # groceries ONLY - narrower than parent
        "per_transaction_cap": 1500.0,       # and a tighter per-transaction bound
    }

    @staticmethod
    def generate_attack(authority_id: str = "auth_household_grocery_2026") -> List[SyntheticTransaction]:
        sub = ScopeCreepVector.DELEGATION_REQUEST["grantee_id"]
        return [
            # Leg 1: inside the sub-delegation. Honestly not an attack.
            SyntheticTransaction(
                tx_id=f"tx_sub_ok_{_RNG.randrange(10**6):06d}",
                authority_id=authority_id, agent_id=sub,
                rail=PaymentRailType.UPI_CIRCLE, amount=_jitter(900.0, 0.15),
                merchant_id="merch_daily_grocer", merchant_name="Daily Grocer Co-op",
                merchant_mcc="5411",
                items=[CartItem(sku="SKU_GROC_02", name="Weekly Staples",
                                category="GROCERY", unit_price=900.0, quantity=1)],
                is_anomalous_red_attack=False,
            ),
            # Leg 2: outside the SUB-DELEGATION's merchant scope, and inside
            # the parent's.
            #
            # This used to use MCC 5311 with a comment claiming "the parent
            # grant permits 5311". It does not - the household grant is
            # 5411/5499/4900 - so INV_03 fired at the parent level too and the
            # demonstration proved nothing about sub-delegation. 5499 is
            # genuinely permitted to the parent and genuinely denied to this
            # link, so the ONLY thing that refuses this leg is the chain.
            SyntheticTransaction(
                tx_id=f"tx_sub_creep_{_RNG.randrange(10**6):06d}",
                authority_id=authority_id, agent_id=sub,
                rail=PaymentRailType.CARD_TOKEN, amount=_jitter(1300.0, 0.12),
                merchant_id="merch_quickmart_misc", merchant_name="QuickMart Miscellaneous",
                merchant_mcc="5499",
                items=[CartItem(sku="SKU_MISC_01", name="Household Sundries",
                                category="GROCERY", unit_price=1300.0, quantity=1)],
                is_anomalous_red_attack=True,
            ),
            # Leg 3: inside merchant scope, but over the link's per-tx cap.
            SyntheticTransaction(
                tx_id=f"tx_sub_over_{_RNG.randrange(10**6):06d}",
                authority_id=authority_id, agent_id=sub,
                rail=PaymentRailType.UPI_CIRCLE, amount=_jitter(2400.0, 0.10),
                merchant_id="merch_city_supermart", merchant_name="City Supermart",
                merchant_mcc="5411",
                items=[CartItem(sku="SKU_GROC_03", name="Bulk Household Order",
                                category="GROCERY", unit_price=2400.0, quantity=1)],
                is_anomalous_red_attack=True,
            ),
        ]

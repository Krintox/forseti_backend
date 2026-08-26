import random
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Any, Optional
import pandas as pd
import numpy as np

from ..graph_sentinel import PaymentGraph
from ..models.state import DTLGlobalAuthorityState, PaymentRailType
from ..models.transactions import SyntheticTransaction, CartItem
from .feature_schema import DTLFeatureExtractor, ALL_FEATURE_NAMES

class SyntheticMLDatasetBuilder:
    """
    Generates non-circular, temporally structured synthetic payment dataset.
    Features are strictly extracted from raw transaction data and simulated DTL state,
    never from synthetic attack label annotations.
    Supports unseen amount variations, rail permutations, and attack-family holdout splits.
    """
    
    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)
        np.random.seed(seed)
        self.last_graph: Optional[PaymentGraph] = None

    # Attacks that are inherently multi-leg: a single row cannot represent them.
    MULTI_LEG_FAMILIES = {"CROSS_RAIL_SPLIT": (3, 4), "VELOCITY_BURST": (4, 7)}

    # A small pool of "compromised/shared" device fingerprints, standing in
    # for a fraud ring reusing hardware across nominally-unrelated agents.
    # Attacks reach for one far more often than legitimate traffic does
    # (15% vs 3%) - correlated with the label, deliberately not perfectly so,
    # matching the anti-circularity discipline the rest of this generator
    # already applies to stored-value overlap and amount-range overlap.
    SHARED_DEVICE_POOL = ["dev_ring_alpha", "dev_ring_beta", "dev_ring_gamma"]

    def _assign_device(self, auth: DTLGlobalAuthorityState, is_attack: bool) -> str:
        ring_prob = 0.15 if is_attack else 0.03
        if random.random() < ring_prob:
            return random.choice(self.SHARED_DEVICE_POOL)
        return f"dev_primary_{auth.authority_id}"

    # ------------------------------------------------------------------
    # MERCHANT POPULATION  (replaces the previous one-merchant-per-family
    # layout plus its 22% `_diversify_merchant` patch)
    #
    # The earlier design gave every attack family its own dedicated merchant
    # node and routed all legitimate traffic through a single other node. The
    # 22% diversification pass was intended to break that, and did not: it
    # rewrote merchant_id while leaving 78% of each family on its own node AND
    # never touched merchant_mcc at all. The result was measurable - PageRank
    # became the model's #2 SHAP feature by fingerprinting "which merchant
    # node is this", and four of six families were separable by a single
    # categorical fact (an MCC that never occurs in legitimate traffic).
    #
    # This population fixes the mechanism instead of patching the symptom:
    #
    #   * ONE shared population. No merchant belongs to a family. Legitimate
    #     and attack traffic both draw from the same list.
    #   * MCC is a property of the MERCHANT, never of the family. A family
    #     cannot carry a categorical fingerprint it does not choose.
    #   * Attacks CONCENTRATE at high-risk merchants without being EXCLUSIVE
    #     to them, and legitimate traffic reaches those same merchants. That
    #     is what makes in-degree/PageRank a genuine aggregator signal (the
    #     documented mule-hub shape) rather than a label lookup.
    #
    # risk_tier: 0 = ordinary · 1 = elevated (sells liquid value) ·
    #            2 = aggregator hub (many unrelated agents settle here)
    # ------------------------------------------------------------------
    MERCHANT_POPULATION = [
        # In-scope grocery / dining / department - the delegated categories.
        ("merch_fresh_direct",      "Fresh Direct Mart",             "5411", 0),
        ("merch_daily_grocer",      "Daily Grocer Co-op",            "5411", 0),
        ("merch_city_supermart",    "City Supermart",                "5411", 0),
        ("merch_corner_kirana",     "Corner Kirana Store",           "5411", 0),
        ("merch_greenleaf_organics","Greenleaf Organics",            "5411", 0),
        ("merch_bistro_lane",       "Bistro Lane",                   "5812", 0),
        ("merch_cafe_central",      "Cafe Central",                  "5812", 0),
        ("merch_tandoor_house",     "Tandoor House",                 "5812", 0),
        ("merch_metro_dept",        "Metro Department Store",        "5311", 0),
        ("merch_grand_bazaar",      "Grand Bazaar",                  "5311", 0),
        # In-scope, elevated: legitimately sells liquid/stored value too.
        ("merch_megastore_vouch",   "Gourmet Mega Store & Vouchers", "5411", 1),
        ("merch_valuemart_plus",    "ValueMart Plus",                "5411", 1),
        ("merch_hyper_saver",       "HyperSaver Wholesale",          "5311", 1),
        # Aggregator hubs - the mule-hub shape graph centrality exists to find.
        ("merch_citywide_hub",      "CityWide Retail Hub",           "5411", 2),
        ("merch_metro_plaza",       "Metro Shopping Plaza",          "5311", 2),
        ("merch_neighborhood_ctr",  "Neighborhood Super Center",     "5812", 2),
        # Out-of-delegated-scope categories. A real household genuinely shops
        # here sometimes; the delegation simply does not cover it. Legitimate
        # traffic reaches these (see _legit_out_of_scope_rate), which is what
        # stops "MCC outside permitted set" from being a fraud fingerprint.
        ("merch_tech_hardware",     "Enterprise Tech & Hardware",    "5045", 0),
        ("merch_gadget_world",      "Gadget World",                  "5045", 0),
        ("merch_digital_svc",       "Rapid Digital Services",        "5734", 0),
        ("merch_software_mart",     "Software Mart",                 "5734", 1),
        ("merch_micro_pos",         "Automated Micro POS",           "5499", 1),
        ("merch_quickmart_misc",    "QuickMart Miscellaneous",       "5499", 0),
    ]

    IN_SCOPE_MCCS = {"5411", "5812", "5311"}

    # Share of LEGITIMATE traffic that lands on an out-of-delegated-scope MCC.
    # Non-zero on purpose: it is what makes INV_03's false-positive rate real
    # rather than zero, and it breaks the previous 1:1 mapping between
    # "MCC out of scope" and "row is fraud".
    _legit_out_of_scope_rate = 0.08

    # Attack pull toward higher-risk merchants. Correlated with the label,
    # deliberately not deterministic - the same discipline _assign_device
    # already applies at 15%/3%.
    _ATTACK_TIER_WEIGHTS = {0: 1.0, 1: 2.5, 2: 4.0}
    _LEGIT_TIER_WEIGHTS = {0: 1.0, 1: 0.6, 2: 0.5}

    def _select_merchant(
        self,
        is_attack: bool,
        require_in_scope: Optional[bool] = None,
    ) -> Tuple[str, str, str]:
        """
        Draws (merchant_id, merchant_name, mcc) from the shared population.

        `require_in_scope` constrains the draw only where a family's DEFINITION
        depends on it (intent laundering must sit at a compliant merchant;
        scope creep must sit outside the delegated categories). Everything
        else draws freely, so no family owns a merchant or an MCC.
        """
        pool = self.MERCHANT_POPULATION
        if require_in_scope is True:
            pool = [m for m in pool if m[2] in self.IN_SCOPE_MCCS]
        elif require_in_scope is False:
            pool = [m for m in pool if m[2] not in self.IN_SCOPE_MCCS]

        weights_by_tier = self._ATTACK_TIER_WEIGHTS if is_attack else self._LEGIT_TIER_WEIGHTS
        weights = [weights_by_tier[m[3]] for m in pool]
        merchant_id, merchant_name, mcc, _tier = random.choices(pool, weights=weights, k=1)[0]
        return merchant_id, merchant_name, mcc

    def generate_trajectory(
        self,
        num_samples: int = 5000,
        fraud_ratio: float = 0.05,
        holdout_families: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Generates chronological payment transactions with a realistic DTL global
        state progression.

        Two modelling properties matter for scientific validity:

        1. DELEGATION WINDOWS RESET. A grocery budget is granted for a period,
           not for all time. Without a reset, cumulative exposure grows without
           bound and every authority-utilisation feature saturates far above
           1.0, which destroys the signal the DTL features are supposed to
           carry. Each authority therefore re-grants on a fixed cadence.

        2. ATTACKS AND LEGITIMATE TRAFFIC BOTH BURST. Cross-rail splitting is
           emitted as a coherent multi-leg burst on one authority across
           distinct rails inside a short window, so aggregate exposure really
           does cross the ceiling. Legitimate traffic also bursts across rails
           - but stays under the ceiling. Burstiness alone is therefore not a
           label proxy; crossing the GLOBAL ceiling is what separates them,
           which is exactly the invariant FORSETI claims to enforce.
        """
        holdouts = holdout_families or []
        records: List[Dict[str, Any]] = []

        start_time = datetime(2026, 5, 1, 8, 0, 0)
        current_time = start_time

        authorities = {
            f"auth_user_{i:03d}": DTLGlobalAuthorityState(
                authority_id=f"auth_user_{i:03d}",
                principal=f"user_{i:03d}@domain.org",
                agent_id=f"agt_{i:03d}",
                global_budget_ceiling=float(random.choice([5000, 10000, 15000, 20000])),
                permitted_merchant_scopes=["GROCERY", "RETAIL", "FOOD_DELIVERY"],
                permitted_mccs=["5411", "5812", "5311"],
                semantic_exclusions=["GIFT_CARD", "STORED_VALUE", "CRYPTO", "PREPAID_VOUCHER"]
            )
            for i in range(25)
        }

        for _auth in authorities.values():
            _auth.delegation_created_at = start_time

        auth_keys = list(authorities.keys())
        auth_histories: Dict[str, List[Dict]] = {k: [] for k in auth_keys}
        # Simulated delegation window: exposure resets when the window rolls.
        window_started_at: Dict[str, datetime] = {k: start_time for k in auth_keys}
        window_hours = 24.0

        attack_types = [
            "CROSS_RAIL_SPLIT",
            "INTENT_LAUNDERING",
            "BASELINE_POISONING",
            "REVOCATION_FLOOD",
            "VELOCITY_BURST",
            "SCOPE_CREEP"
        ]

        rails_all = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2]
        attack_counter = 0

        # Cross-authority entity graph, built incrementally across the WHOLE
        # trajectory (not per-authority) - the point of Payment Graph Sentinel
        # is exactly the signal that only exists across authorities, e.g. many
        # different agents converging on one merchant, or several agents
        # sharing a device fingerprint.
        graph = PaymentGraph()

        def roll_window(auth_id: str, auth: DTLGlobalAuthorityState, now: datetime) -> None:
            """Re-grants the delegation when its validity window elapses."""
            if (now - window_started_at[auth_id]).total_seconds() >= window_hours * 3600.0:
                auth.cumulative_spent_settled = 0.0
                auth.cumulative_spent_authorized = 0.0
                auth.pending_spend_global = 0.0
                auth.reserved_spend_global = 0.0
                window_started_at[auth_id] = now
                # A rolled window is a fresh grant: re-stamp it so delegation age
                # (and therefore remaining TTL) is measured against this window.
                auth.delegation_created_at = now
                auth_histories[auth_id].clear()

        def emit(auth_id: str, auth: DTLGlobalAuthorityState, tx, family: str,
                 is_attack: bool, now: datetime) -> None:
            """Extracts features BEFORE applying the spend, then books it."""
            history = auth_histories[auth_id]
            # Graph snapshot BEFORE this transaction's own edge is added -
            # otherwise a transaction's features would include its own effect
            # on the graph (e.g. its own device-sharing edge inflating its own
            # graph_device_shared_count).
            graph_feats = graph.snapshot_features(auth.agent_id, tx.merchant_id, tx.device_id)
            features = DTLFeatureExtractor.extract_features(auth, tx, history, graph_features=graph_feats)
            graph.add_transaction(auth.agent_id, tx.merchant_id, tx.device_id)

            # Book the spend AFTER feature extraction so a transaction never sees
            # its own effect on exposure. Settlement is not instant: a share stays
            # in flight, which is exactly the window a racing agent exploits.
            in_flight = tx.amount * 0.35
            auth.cumulative_spent_authorized += tx.amount - in_flight
            auth.pending_spend_global += in_flight
            # Earlier in-flight value settles as new activity arrives.
            settled_now = auth.pending_spend_global * 0.45
            auth.pending_spend_global -= settled_now
            auth.cumulative_spent_authorized += settled_now

            history.append({
                "tx_id": tx.tx_id,
                "rail": tx.rail.value if hasattr(tx.rail, "value") else str(tx.rail),
                "amount": tx.amount,
                "mcc": tx.merchant_mcc,
                "merchant_id": tx.merchant_id,
                "timestamp": now.isoformat()
            })
            if len(history) > 60:
                history.pop(0)

            row = dict(features)
            row["tx_id"] = tx.tx_id
            row["authority_id"] = auth_id
            row["timestamp"] = now.isoformat()
            row["timestamp_unix"] = now.timestamp()
            row["amount"] = float(tx.amount)
            row["is_fraud"] = 1 if is_attack else 0
            row["attack_family"] = family
            row["is_holdout"] = 1 if family in holdouts else 0
            # Raw categorical provenance. NOT features - ALL_FEATURE_NAMES is
            # what train/serve consume, and these are excluded from it. They
            # exist so leakage_audit.py can check whether any single
            # categorical value has become a label lookup, which is exactly
            # the check whose absence let the MCC/merchant fingerprint ship.
            row["merchant_mcc"] = tx.merchant_mcc
            row["merchant_id"] = tx.merchant_id
            row["device_id"] = tx.device_id or ""
            row["rail"] = str(getattr(tx.rail, "value", tx.rail))
            records.append(row)

        while len(records) < num_samples:
            current_time += timedelta(seconds=random.randint(15, 300))
            auth_id = random.choice(auth_keys)
            auth = authorities[auth_id]
            roll_window(auth_id, auth, current_time)

            is_attack = (random.random() < fraud_ratio)

            if is_attack:
                family = attack_types[attack_counter % len(attack_types)]
                attack_counter += 1

                if family in self.MULTI_LEG_FAMILIES:
                    lo, hi = self.MULTI_LEG_FAMILIES[family]
                    n_legs = random.randint(lo, hi)
                    if family == "CROSS_RAIL_SPLIT":
                        # Size the legs so each is individually unremarkable but
                        # the aggregate overshoots the delegated ceiling.
                        headroom = max(500.0, auth.global_budget_ceiling - auth.total_exposure_global)
                        target_total = headroom * random.uniform(1.25, 1.9)
                        leg_rails = random.sample(rails_all, k=min(n_legs, len(rails_all)))
                        while len(leg_rails) < n_legs:
                            leg_rails.append(random.choice(rails_all))
                        for leg_rail in leg_rails:
                            leg_amt = round(target_total / n_legs * random.uniform(0.82, 1.18), 2)
                            tx, fam = self._create_transaction(auth, family, current_time, True)
                            tx.amount = leg_amt
                            tx.rail = leg_rail
                            for item in tx.items:
                                item.unit_price = leg_amt
                            emit(auth_id, auth, tx, fam, True, current_time)
                            current_time += timedelta(seconds=random.randint(20, 110))
                            if len(records) >= num_samples:
                                break
                    else:
                        for _ in range(n_legs):
                            tx, fam = self._create_transaction(auth, family, current_time, True)
                            emit(auth_id, auth, tx, fam, True, current_time)
                            current_time += timedelta(seconds=random.randint(5, 40))
                            if len(records) >= num_samples:
                                break

                elif family == "REVOCATION_FLOOD":
                    # The attack IS the revoke/regrant churn: race the mandate
                    # lifecycle so a stale delegation authorises the spend.
                    hist = auth_histories[auth_id]
                    for _ in range(random.randint(3, 6)):
                        hist.append({"event": "REVOKE", "rail": "AGENTIC_AP2",
                                     "amount": 0.0, "timestamp": current_time.isoformat()})
                        current_time += timedelta(seconds=random.randint(2, 15))
                        hist.append({"event": "REGRANT", "rail": "AGENTIC_AP2",
                                     "amount": 0.0, "timestamp": current_time.isoformat()})
                        current_time += timedelta(seconds=random.randint(2, 15))
                    tx, fam = self._create_transaction(auth, family, current_time, True)
                    emit(auth_id, auth, tx, fam, True, current_time)
                    continue
                    continue

                tx, fam = self._create_transaction(auth, family, current_time, True)
                emit(auth_id, auth, tx, fam, True, current_time)
                continue

            # Legitimate traffic. ~10% of it is a genuine multi-rail burst that
            # stays within the delegated ceiling - the control condition that
            # stops "burst across rails" from being a label proxy.
            if random.random() < 0.10:
                headroom = max(0.0, auth.global_budget_ceiling - auth.total_exposure_global)
                if headroom > 600.0:
                    n_legs = random.randint(2, 3)
                    budget = headroom * random.uniform(0.35, 0.85)
                    leg_rails = random.sample(rails_all, k=min(n_legs, len(rails_all)))
                    for leg_rail in leg_rails:
                        leg_amt = round(budget / n_legs * random.uniform(0.8, 1.2), 2)
                        remaining = max(0.0, auth.global_budget_ceiling - auth.total_exposure_global)
                        if remaining < 80.0:
                            break
                        tx, fam = self._create_transaction(auth, "NONE", current_time, False)
                        tx.amount = max(80.0, min(leg_amt, remaining))
                        tx.rail = leg_rail
                        for item in tx.items:
                            item.unit_price = tx.amount / max(1, len(tx.items))
                        emit(auth_id, auth, tx, fam, False, current_time)
                        current_time += timedelta(seconds=random.randint(25, 130))
                        if len(records) >= num_samples:
                            break
                    continue

            headroom = max(0.0, auth.global_budget_ceiling - auth.total_exposure_global)
            if headroom < 150.0:
                # Budget exhausted for this delegation window. A compliant agent
                # simply stops spending until the window rolls over.
                continue

            if random.random() < 0.04:
                # Ordinary lifecycle churn: a user revoking and re-issuing a
                # delegation is normal behaviour, not an attack signal.
                auth_histories[auth_id].append(
                    {"event": random.choice(["REVOKE", "REGRANT"]),
                     "rail": random.choice(rails_all).value,
                     "amount": 0.0, "timestamp": current_time.isoformat()})

            tx, fam = self._create_transaction(auth, "NONE", current_time, False)
            if tx.amount > headroom:
                tx.amount = round(max(80.0, headroom * random.uniform(0.45, 0.98)), 2)
                for item in tx.items:
                    item.unit_price = tx.amount / max(1, len(tx.items))
            emit(auth_id, auth, tx, fam, False, current_time)

        # One last refresh so the graph's own global metrics (not any already-
        # generated row's features, which were all snapshotted before their
        # own edge was added) reflect the FULL trajectory - useful for
        # introspection/tests via self.last_graph.stats(), not consumed here.
        graph.refresh_global_metrics()
        self.last_graph = graph

        df = pd.DataFrame(records[:num_samples])
        df = df.sort_values(by="timestamp_unix").reset_index(drop=True)
        return df

    def _create_transaction(
        self,
        auth: DTLGlobalAuthorityState,
        attack_family: str,
        timestamp: datetime,
        is_attack: bool
    ) -> Tuple[SyntheticTransaction, str]:
        """
        Builds the transaction, then assigns device identity.

        Merchant identity is no longer patched here: _build_transaction now
        draws it from the shared MERCHANT_POPULATION at construction time, so
        there is no per-family merchant to de-fingerprint after the fact.
        """
        tx, fam = self._build_transaction(auth, attack_family, timestamp, is_attack)
        tx.device_id = self._assign_device(auth, is_attack)
        return tx, fam

    def _build_transaction(
        self,
        auth: DTLGlobalAuthorityState,
        attack_family: str,
        timestamp: datetime,
        is_attack: bool
    ) -> Tuple[SyntheticTransaction, str]:
        """
        Creates synthetic transaction with non-circular variations.
        """
        tx_id = f"tx_syn_{random.randint(100000, 999999)}"
        rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2]

        if not is_attack or attack_family == "NONE":
            # Normal legitimate transaction.
            #
            # ANTI-CIRCULARITY: a naive generator where legitimate carts NEVER
            # contain stored value and laundering attacks ALWAYS do makes
            # semantic_drift_score a perfect label proxy, and the classifier
            # learns the generator instead of the behaviour. Two overlaps are
            # therefore injected on purpose:
            #   (a) ~9% of legitimate carts contain a small, in-scope gift card
            #       (a real household buys birthday vouchers), so a non-zero
            #       drift score is NOT sufficient evidence of fraud;
            #   (b) ~12% of legitimate baskets are large stock-up runs whose
            #       amounts overlap the attack amount range.
            rail = random.choice(rails)
            if random.random() < 0.12:
                # Large legitimate stock-up run overlapping the attack range.
                amount = round(float(random.uniform(0.35, 0.62) * auth.global_budget_ceiling), 2)
            else:
                amount = round(float(np.random.lognormal(mean=6.5, sigma=0.7)), 2)
                amount = max(100.0, min(amount, auth.global_budget_ceiling * 0.4))

            # (c) ~8% of legitimate spend lands on a merchant OUTSIDE the
            #     delegated categories. A household genuinely buys a laptop
            #     charger sometimes. The DTL correctly refuses it (INV_03) and
            #     that is a true positive for the POLICY and a false positive
            #     for FRAUD - the two are different questions, and keeping
            #     them different is what stops "MCC out of scope" from being a
            #     deterministic fraud label.
            want_in_scope = random.random() >= self._legit_out_of_scope_rate
            merchant_id, merchant_name, mcc = self._select_merchant(
                is_attack=False, require_in_scope=want_in_scope
            )

            if random.random() < 0.09:
                # In-scope minor stored-value purchase inside a genuine basket.
                gift_amt = round(amount * random.uniform(0.04, 0.18), 2)
                items = [
                    CartItem(sku="SKU_GROC_01", name="Organic Milk & Eggs", category="GROCERY",
                             unit_price=round(amount - gift_amt, 2), quantity=1),
                    CartItem(sku="SKU_GIFT_SMALL", name="Birthday Gift Voucher", category="GIFT_CARD",
                             unit_price=gift_amt, quantity=1, is_stored_value=True),
                ]
            else:
                items = [
                    CartItem(sku="SKU_GROC_01", name="Organic Milk & Eggs", category="GROCERY",
                             unit_price=amount * 0.6, quantity=1),
                    CartItem(sku="SKU_GROC_02", name="Artisanal Bread", category="GROCERY",
                             unit_price=amount * 0.4, quantity=1),
                ]

            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=rail,
                amount=amount,
                merchant_id=merchant_id,
                merchant_name=merchant_name,
                merchant_mcc=mcc,
                items=items,
                created_at=timestamp
            ), "NONE"

        # Adversarial Attack Variations
        if attack_family == "CROSS_RAIL_SPLIT":
            # Randomized split amounts (e.g. ₹3.2k, ₹4.8k, ₹5.1k)
            # Deliberately overlaps the legitimate amount distribution: each
            # leg looks ordinary in isolation and carries no stored value. Only
            # cross-rail aggregate exposure reveals it.
            amount = round(float(random.uniform(0.28, 0.55) * auth.global_budget_ceiling), 2)
            rail = random.choice(rails)
            items = [CartItem(sku="SKU_SPLIT_01", name="Retail Order Split", category="RETAIL", unit_price=amount, quantity=1)]
            # A splitter's whole technique is looking ordinary per leg, so it
            # shops where ordinary spend happens - drawn from the shared pool
            # with no family-owned merchant and no reserved MCC.
            merchant_id, merchant_name, mcc = self._select_merchant(is_attack=True)
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=rail,
                amount=amount,
                merchant_id=merchant_id,
                merchant_name=merchant_name,
                merchant_mcc=mcc,
                items=items,
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="CROSS_RAIL_SPLIT"
            ), "CROSS_RAIL_SPLIT"

        elif attack_family == "INTENT_LAUNDERING":
            # Legitimate MCC (5411) but contains liquid stored value
            total_amt = round(float(random.uniform(0.35, 0.95) * auth.global_budget_ceiling), 2)
            # Share overlaps the legitimate in-scope voucher band at its lower
            # edge, so the boundary is learned rather than hardcoded.
            gift_amt = round(total_amt * random.uniform(0.22, 0.90), 2)
            groc_amt = round(total_amt - gift_amt, 2)
            
            items = [
                CartItem(sku="SKU_MILK_GEN", name="Organic Milk", category="GROCERY", unit_price=groc_amt, quantity=1),
                CartItem(sku="SKU_GIFT_DIGITAL", name="Amazon Pay Digital Gift Card", category="GIFT_CARD", unit_price=gift_amt, quantity=1, is_stored_value=True)
            ]
            # Laundering is DEFINED by sitting at a compliant merchant while
            # the cart is not - so this family constrains MCC to in-scope. It
            # still draws a real merchant from the shared pool rather than
            # owning one.
            laundering_merchant_id, laundering_merchant_name, laundering_mcc = self._select_merchant(
                is_attack=True, require_in_scope=True
            )
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=random.choice(rails),
                amount=total_amt,
                merchant_id=laundering_merchant_id,
                merchant_name=laundering_merchant_name,
                merchant_mcc=laundering_mcc,
                items=items,
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="INTENT_LAUNDERING"
            ), "INTENT_LAUNDERING"

        elif attack_family == "REVOCATION_FLOOD":
            amount = round(float(random.uniform(0.20, 0.50) * auth.global_budget_ceiling), 2)
            # Previously pinned to MCC 5734, which appeared in NO legitimate
            # row - making this family perfectly separable by one categorical
            # value and producing the 1.000 PR-AUC / mean-probability-1.0
            # "holdout success" that was really a leak readout.
            merchant_id, merchant_name, mcc = self._select_merchant(is_attack=True)
            # Rail was previously pinned to AGENTIC_AP2 for 100% of this
            # family, making `rail` a second categorical fingerprint with
            # recall 1.0. Mandate churn is most natural on the agentic rail,
            # so it stays weighted there - but not exclusively.
            revoc_rail = random.choices(
                [PaymentRailType.AGENTIC_AP2, PaymentRailType.UPI_CIRCLE, PaymentRailType.CARD_TOKEN],
                weights=[0.6, 0.25, 0.15], k=1,
            )[0]
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=revoc_rail,
                amount=amount,
                merchant_id=merchant_id,
                merchant_name=merchant_name,
                merchant_mcc=mcc,
                items=[CartItem(sku="SKU_REVOC_DIG", name="Instant Software Token", category="DIGITAL", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="REVOCATION_FLOOD"
            ), "REVOCATION_FLOOD"

        elif attack_family == "VELOCITY_BURST":
            # Micro-amount probes
            amount = round(float(random.uniform(50.0, 300.0)), 2)
            merchant_id, merchant_name, mcc = self._select_merchant(is_attack=True)
            # Card testing is genuinely card-weighted (that is the credential
            # being validated), but it was previously 100% CARD_TOKEN, which
            # made `rail` a recall-1.0 fingerprint for this family.
            burst_rail = random.choices(
                [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2],
                weights=[0.7, 0.18, 0.12], k=1,
            )[0]
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=burst_rail,
                amount=amount,
                merchant_id=merchant_id,
                merchant_name=merchant_name,
                merchant_mcc=mcc,
                items=[CartItem(sku="SKU_MICRO_PROBE", name="Micro Probe Item", category="MISC", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="VELOCITY_BURST"
            ), "VELOCITY_BURST"

        else: # SCOPE_CREEP or BASELINE_POISONING
            amount = round(float(random.uniform(0.40, 0.80) * auth.global_budget_ceiling), 2)
            # Scope creep is DEFINED by leaving the delegated categories, so
            # this family does constrain MCC to out-of-scope. It is no longer a
            # fingerprint, because ~8% of legitimate traffic lands out of scope
            # too (see _legit_out_of_scope_rate) - the model must now learn
            # scope violation ALONGSIDE other evidence rather than reading one
            # categorical value as the label.
            merchant_id, merchant_name, mcc = self._select_merchant(
                is_attack=True, require_in_scope=False
            )
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=random.choice(rails),
                amount=amount,
                merchant_id=merchant_id,
                merchant_name=merchant_name,
                merchant_mcc=mcc,
                items=[CartItem(sku="SKU_TECH_01", name="High-End Workstation GPU", category="ELECTRONICS", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="SCOPE_CREEP"
            ), "SCOPE_CREEP"

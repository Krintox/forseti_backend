import random
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Any, Optional
import pandas as pd
import numpy as np

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

    # Attacks that are inherently multi-leg: a single row cannot represent them.
    MULTI_LEG_FAMILIES = {"CROSS_RAIL_SPLIT": (3, 4), "VELOCITY_BURST": (4, 7)}

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
            features = DTLFeatureExtractor.extract_features(auth, tx, history)

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

            mcc = random.choice(["5411", "5812", "5311"])

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
                merchant_id="merch_normal_groceries",
                merchant_name="Fresh Direct Mart",
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
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=rail,
                amount=amount,
                merchant_id="merch_split_chain",
                merchant_name="Multi-Store Express",
                merchant_mcc="5311",
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
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=random.choice(rails),
                amount=total_amt,
                merchant_id="merch_laundering_mega",
                merchant_name="Gourmet Mega Store & Vouchers",
                merchant_mcc="5411",
                items=items,
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="INTENT_LAUNDERING"
            ), "INTENT_LAUNDERING"

        elif attack_family == "REVOCATION_FLOOD":
            amount = round(float(random.uniform(0.20, 0.50) * auth.global_budget_ceiling), 2)
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=PaymentRailType.AGENTIC_AP2,
                amount=amount,
                merchant_id="merch_revoc_race",
                merchant_name="Rapid Digital Services",
                merchant_mcc="5734",
                items=[CartItem(sku="SKU_REVOC_DIG", name="Instant Software Token", category="DIGITAL", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="REVOCATION_FLOOD"
            ), "REVOCATION_FLOOD"

        elif attack_family == "VELOCITY_BURST":
            # Micro-amount probes
            amount = round(float(random.uniform(50.0, 300.0)), 2)
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=PaymentRailType.CARD_TOKEN,
                amount=amount,
                merchant_id="merch_probe_burst",
                merchant_name="Automated Micro POS",
                merchant_mcc="5499",
                items=[CartItem(sku="SKU_MICRO_PROBE", name="Micro Probe Item", category="MISC", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="VELOCITY_BURST"
            ), "VELOCITY_BURST"

        else: # SCOPE_CREEP or BASELINE_POISONING
            amount = round(float(random.uniform(0.40, 0.80) * auth.global_budget_ceiling), 2)
            return SyntheticTransaction(
                tx_id=tx_id,
                authority_id=auth.authority_id,
                agent_id=auth.agent_id,
                rail=random.choice(rails),
                amount=amount,
                merchant_id="merch_scope_creep",
                merchant_name="Enterprise Tech & Hardware",
                merchant_mcc="5045",
                items=[CartItem(sku="SKU_TECH_01", name="High-End Workstation GPU", category="ELECTRONICS", unit_price=amount, quantity=1)],
                created_at=timestamp,
                is_anomalous_red_attack=True,
                attack_primitive_type="SCOPE_CREEP"
            ), "SCOPE_CREEP"

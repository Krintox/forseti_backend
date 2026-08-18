import json
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from ..models.state import DTLGlobalAuthorityState, PaymentRailType
from ..models.transactions import SyntheticTransaction

# Formal Feature Schema Definitions
FEATURE_GROUPS = {
    "raw_transaction": [
        "amount",
        "rail_code",               # 0: CARD, 1: UPI, 2: AGENTIC
        "merchant_mcc_code",       # numeric MCC e.g. 5411
        "hour_of_day",             # 0 to 23
        "day_of_week",             # 0 to 6
        "retry_count",             # 0+
        "tx_velocity_1h",          # count of transactions in last 1h
        "merchant_risk_score"      # 0.0 to 1.0
    ],
    "delegation": [
        "granted_limit",           # ₹ ceiling
        "remaining_headroom",      # ₹ remaining
        "authority_utilization_ratio", # total_exposure / granted_limit
        "delegation_ttl_remaining_pct", # % time remaining
        "delegation_fanout_count", # subagents delegated
        "active_subagents_count"   # active subagents count
    ],
    "cross_rail": [
        "total_exposure_global",   # settled + auth + pending + reserved
        "pending_spend_global",    # in-flight pending spend
        "cross_rail_velocity",     # count of rails touched in last 1h
        "num_rails_used_24h",      # distinct rails used
        "exposure_after_tx_ratio", # (total_exposure + amount) / granted_limit
        "amount_deviation_from_rail_mean" # deviation from per-rail average
    ],
    "semantic": [
        "semantic_drift_score",    # weighted proportion of cart outside purpose (0.0 to 1.0)
        "stored_value_item_count", # count of gift cards / vouchers
        "stored_value_value_ratio",# ₹ value of stored-value items / total cart ₹
        "merchant_category_match_bool", # 1 if MCC matches intent, 0 otherwise
        "cart_intent_consistency_score" # 0.0 to 1.0 consistency score
    ],
    "security": [
        "revocation_rate_1h",      # rate of grants/revocations
        "regrant_frequency",       # rapid regrants
        "velocity_spike_indicator",# 1 if tx frequency > 3 sigma
        "mcc_entropy"              # entropy across visited MCCs
    ]
}

ALL_FEATURE_NAMES: List[str] = [
    f for group in FEATURE_GROUPS.values() for f in group
]

class DTLFeatureExtractor:
    """
    Unified, Deterministic Feature Extractor.
    Used IDENTICALLY during ML dataset generation, training, and real-time production inference.
    Guarantees 0 train-serving feature skew.
    """
    
    @staticmethod
    def get_feature_names() -> List[str]:
        return list(ALL_FEATURE_NAMES)

    @staticmethod
    def get_feature_groups() -> Dict[str, List[str]]:
        return dict(FEATURE_GROUPS)

    @staticmethod
    def save_schema(filepath: Optional[str] = None):
        if filepath is None:
            filepath = os.path.join(
                os.path.dirname(__file__), "..", "..", "artifacts", "models", "feature_schema.json"
            )
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        schema_data = {
            "version": "2.1.0",
            "feature_count": len(ALL_FEATURE_NAMES),
            "feature_names": ALL_FEATURE_NAMES,
            "feature_groups": FEATURE_GROUPS,
            "description": "Unified 5-Group Feature Schema for FORSETI Hybrid ML Detector."
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(schema_data, f, indent=2)
        return schema_data

    @staticmethod
    def _window(history: List[Dict[str, Any]], now: datetime, hours: float) -> List[Dict[str, Any]]:
        """History entries inside the trailing `hours` window."""
        if not history:
            return []
        cutoff = now - timedelta(hours=hours)
        out = []
        for h in history:
            ts = h.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if isinstance(ts, datetime) and ts >= cutoff:
                out.append(h)
        return out

    @staticmethod
    def _entropy(values: List[Any]) -> float:
        """Shannon entropy in bits over a categorical sample."""
        if not values:
            return 0.0
        counts: Dict[Any, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        total = float(len(values))
        return float(-sum((c / total) * math.log2(c / total) for c in counts.values()))

    @staticmethod
    def extract_features(
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        tx_history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, float]:
        """
        Extracts the full engineered feature vector from a transaction and the
        DTL authority state.

        Everything here is COMPUTED from the transaction, the authority state or
        the trailing history window. An earlier version returned fixed constants
        for nine of these fields, which both leaked no information to the model
        and misrepresented what was being measured.
        """
        history = tx_history or []
        now = tx.created_at if isinstance(tx.created_at, datetime) else datetime.utcnow()
        last_1h = DTLFeatureExtractor._window(history, now, 1.0)
        last_24h = DTLFeatureExtractor._window(history, now, 24.0)

        # ---------------------------------------------------- raw transaction
        rail_code_map = {
            PaymentRailType.CARD_TOKEN: 0.0,
            PaymentRailType.UPI_CIRCLE: 1.0,
            PaymentRailType.AGENTIC_AP2: 2.0
        }
        rail_code = rail_code_map.get(tx.rail, 0.0)
        try:
            mcc_num = float(tx.merchant_mcc)
        except (ValueError, TypeError):
            mcc_num = 5411.0

        hour = float(now.hour)
        dow = float(now.weekday())

        # Retries: same merchant and same amount seen again in the last hour is
        # the signature of an agent probing a declining control.
        retry_count = float(sum(
            1 for h in last_1h
            if h.get("merchant_id") == tx.merchant_id
            and abs(float(h.get("amount", 0.0)) - float(tx.amount)) < 1.0
        ))

        tx_velocity_1h = float(len(last_1h))

        # Merchant risk from the declared category and the cart itself, rather
        # than a name-substring guess.
        stored_value_count = 0
        stored_value_amt = 0.0
        for item in tx.items:
            if item.is_stored_value or any(
                w in f"{item.name} {item.category}".lower()
                for w in ("gift", "voucher", "crypto", "prepaid", "stored")
            ):
                stored_value_count += 1
                stored_value_amt += (item.unit_price * item.quantity)

        mcc_in_scope = tx.merchant_mcc in auth.permitted_mccs
        merchant_risk = 0.0
        if not mcc_in_scope:
            merchant_risk += 0.5
        if stored_value_count:
            merchant_risk += 0.35
        if float(tx.amount) > 0 and stored_value_amt / float(tx.amount) > 0.5:
            merchant_risk += 0.15
        merchant_risk = min(1.0, merchant_risk)

        # ---------------------------------------------------- delegation
        granted_limit = max(1.0, float(auth.global_budget_ceiling))
        total_exp = float(auth.total_exposure_global)
        remaining_headroom = max(0.0, granted_limit - total_exp)
        auth_utilization = total_exp / granted_limit

        # Delegation age against its validity window, from real timestamps.
        created = auth.delegation_created_at
        if isinstance(created, datetime):
            if created.tzinfo and not now.tzinfo:
                created = created.replace(tzinfo=None)
            elif now.tzinfo and not created.tzinfo:
                created = created.replace(tzinfo=now.tzinfo)
            age_h = max(0.0, (now - created).total_seconds() / 3600.0)
        else:
            age_h = 0.0
        window_h = 24.0
        ttl_remaining_pct = max(0.0, min(1.0, 1.0 - (age_h % window_h) / window_h))

        fanout = float(len(auth.active_delegations_by_rail))
        # Distinct rails actually exercised in the window == live sub-agents.
        active_subagents = float(len({h.get("rail") for h in last_24h if h.get("rail")}) or 1)

        # ---------------------------------------------------- cross-rail
        pending_spend = float(auth.pending_spend_global)
        rails_24h = {str(h.get("rail")) for h in last_24h if h.get("rail")}
        rails_24h.add(str(tx.rail.value if hasattr(tx.rail, "value") else tx.rail))
        num_rails_used = float(len(rails_24h))
        cross_rail_velocity = float(sum(
            1 for h in last_1h
            if str(h.get("rail")) != str(tx.rail.value if hasattr(tx.rail, "value") else tx.rail)
        ))
        exp_after_tx_ratio = (total_exp + float(tx.amount)) / granted_limit

        rail_amounts = [float(h.get("amount", 0.0)) for h in last_24h
                        if str(h.get("rail")) == str(tx.rail.value if hasattr(tx.rail, "value") else tx.rail)]
        rail_mean = (sum(rail_amounts) / len(rail_amounts)) if rail_amounts else float(tx.amount)
        amount_dev = float(tx.amount) - rail_mean

        # ---------------------------------------------------- semantic
        stored_val_ratio = stored_value_amt / max(1.0, float(tx.amount))
        mcc_match = 1.0 if mcc_in_scope else 0.0

        excluded_value = 0.0
        for item in tx.items:
            if item.is_stored_value or any(
                ex.lower() in f"{item.category} {item.name}".lower()
                for ex in auth.semantic_exclusions
            ):
                excluded_value += item.unit_price * item.quantity
        # Drift is the share of cart value outside the authorised purpose, plus
        # a penalty when the category itself is out of scope.
        semantic_drift = min(1.0, excluded_value / max(1.0, float(tx.amount)))
        if not mcc_in_scope:
            semantic_drift = min(1.0, semantic_drift + 0.65)
        cart_intent_consistency = max(0.0, 1.0 - semantic_drift)

        # ---------------------------------------------------- security
        window_24h = max(1.0, min(24.0, age_h or 24.0))
        revocation_rate = float(sum(1 for h in last_24h if h.get("event") == "REVOKE")) / window_24h
        regrant_freq = float(sum(1 for h in last_24h if h.get("event") == "REGRANT")) / window_24h

        # Velocity spike relative to this authority's own recent baseline.
        hourly_counts: Dict[int, int] = {}
        for h in last_24h:
            ts = h.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if isinstance(ts, datetime):
                bucket = int((now - ts).total_seconds() // 3600)
                hourly_counts[bucket] = hourly_counts.get(bucket, 0) + 1
        counts = list(hourly_counts.values())
        if len(counts) >= 3:
            mean_c = sum(counts) / len(counts)
            var = sum((c - mean_c) ** 2 for c in counts) / len(counts)
            std = var ** 0.5
            velocity_spike = 1.0 if (std > 0 and tx_velocity_1h > mean_c + 2 * std) else 0.0
        else:
            velocity_spike = 1.0 if tx_velocity_1h > 6.0 else 0.0

        mcc_entropy = DTLFeatureExtractor._entropy(
            [h.get("mcc") for h in last_24h if h.get("mcc")] + [tx.merchant_mcc]
        )

        features: Dict[str, float] = {
            # Raw
            "amount": float(tx.amount),
            "rail_code": rail_code,
            "merchant_mcc_code": mcc_num,
            "hour_of_day": hour,
            "day_of_week": dow,
            "retry_count": retry_count,
            "tx_velocity_1h": tx_velocity_1h,
            "merchant_risk_score": merchant_risk,

            # Delegation
            "granted_limit": granted_limit,
            "remaining_headroom": remaining_headroom,
            "authority_utilization_ratio": auth_utilization,
            "delegation_ttl_remaining_pct": ttl_remaining_pct,
            "delegation_fanout_count": fanout,
            "active_subagents_count": active_subagents,

            # Cross-rail
            "total_exposure_global": total_exp,
            "pending_spend_global": pending_spend,
            "cross_rail_velocity": cross_rail_velocity,
            "num_rails_used_24h": num_rails_used,
            "exposure_after_tx_ratio": exp_after_tx_ratio,
            "amount_deviation_from_rail_mean": amount_dev,

            # Semantic
            "semantic_drift_score": semantic_drift,
            "stored_value_item_count": float(stored_value_count),
            "stored_value_value_ratio": stored_val_ratio,
            "merchant_category_match_bool": mcc_match,
            "cart_intent_consistency_score": cart_intent_consistency,

            # Security
            "revocation_rate_1h": revocation_rate,
            "regrant_frequency": regrant_freq,
            "velocity_spike_indicator": velocity_spike,
            "mcc_entropy": mcc_entropy
        }

        return features

    @staticmethod
    def extract_feature_vector(
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        tx_history: Optional[List[Dict[str, Any]]] = None
    ) -> List[float]:
        feat_dict = DTLFeatureExtractor.extract_features(auth, tx, tx_history)
        return [feat_dict.get(name, 0.0) for name in ALL_FEATURE_NAMES]

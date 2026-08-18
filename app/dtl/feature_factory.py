from typing import Dict, Any, List
from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction

class DTLFeatureFactory:
    """
    Extracts high-signal ML features from DTL global authority state & transaction context.
    These features represent the proprietary ML moat of the hybrid classifier.
    """
    @staticmethod
    def extract_features(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Dict[str, float]:
        # 1. Base transaction features
        amount = float(tx.amount)
        mcc_int = float(int(tx.merchant_mcc) if tx.merchant_mcc.isdigit() else 5411)
        
        # 2. Item level semantic indicators
        has_stored_value = 1.0 if any(
            item.is_stored_value or "card" in item.name.lower() or "voucher" in item.name.lower() or "gift" in item.name.lower()
            for item in tx.items
        ) else 0.0
        item_count = float(len(tx.items))
        
        # 3. DTL Authority Moat Features
        total_exposure = auth.total_exposure_global
        authority_headroom = auth.authority_headroom
        headroom_ratio = amount / max(1.0, authority_headroom)
        exposure_after_tx_ratio = (total_exposure + amount) / max(1.0, auth.global_budget_ceiling)
        
        # 4. Cross-rail velocity & distribution
        card_delegation = auth.active_delegations_by_rail.get(tx.rail, 10000.0)
        rail_utilization = amount / max(1.0, card_delegation)
        
        # 5. Semantic drift estimator (0.0 to 1.0)
        semantic_drift_score = 0.95 if has_stored_value else (0.85 if exposure_after_tx_ratio > 1.0 else 0.05)

        return {
            "tx_amount": amount,
            "merchant_mcc": mcc_int,
            "has_stored_value_item": has_stored_value,
            "item_count": item_count,
            "cumulative_spent_settled": float(auth.cumulative_spent_settled),
            "cumulative_spent_authorized": float(auth.cumulative_spent_authorized),
            "pending_spend_global": float(auth.pending_spend_global),
            "total_exposure_global": float(total_exposure),
            "authority_headroom": float(authority_headroom),
            "headroom_ratio": float(headroom_ratio),
            "exposure_after_tx_ratio": float(exposure_after_tx_ratio),
            "rail_utilization": float(rail_utilization),
            "semantic_drift_score": float(semantic_drift_score)
        }

    @staticmethod
    def get_feature_names() -> List[str]:
        return [
            "tx_amount",
            "merchant_mcc",
            "has_stored_value_item",
            "item_count",
            "cumulative_spent_settled",
            "cumulative_spent_authorized",
            "pending_spend_global",
            "total_exposure_global",
            "authority_headroom",
            "headroom_ratio",
            "exposure_after_tx_ratio",
            "rail_utilization",
            "semantic_drift_score"
        ]

from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

class PaymentRailType(str, Enum):
    CARD_TOKEN = "CARD_TOKEN"         # Card-tokenization-inspired adapter (MDES concept)
    UPI_CIRCLE = "UPI_CIRCLE"         # UPI-Circle-inspired delegation adapter (NPCI OC 201-B concept)
    AGENTIC_AP2 = "AGENTIC_AP2"       # Agentic / AP2-inspired intent/cart adapter

class TransactionState(str, Enum):
    INITIATED = "INITIATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    SHADOW_EXECUTED = "SHADOW_EXECUTED"

class DefensePolicy(str, Enum):
    STANDARD = "STANDARD"                       # Baseline rules-only
    STRICT_INVARIANT = "STRICT_INVARIANT"       # Active DTL cross-rail constraint checking
    ADAPTIVE_CONTAINMENT = "ADAPTIVE_CONTAINMENT" # Partial auth + shadow execution active
    CAPABILITY_QUARANTINED = "CAPABILITY_QUARANTINED" # Agent spending downgrade
    TIGHTENED_HEADROOM_V2 = "TIGHTENED_HEADROOM_V2" # Blue policy tightened headroom
    STRICT_CATALOG_ATTESTATION = "STRICT_CATALOG_ATTESTATION" # Strict SKU verification
    STEP_UP_VERIFICATION = "STEP_UP_VERIFICATION" # Step-up biometric check

class DTLGlobalAuthorityState(BaseModel):
    """
    Canonical Global State of Delegated Authority.
    Includes Two-Phase Balance tracking to prevent in-flight race conditions.
    """
    authority_id: str
    principal: str
    agent_id: str
    global_budget_ceiling: float = 10000.0  # E.g. ₹10,000 grocery budget
    
    # --- Two-Phase Exposure Breakdown ---
    cumulative_spent_settled: float = 0.0     # Finalized & settled transactions
    cumulative_spent_authorized: float = 0.0  # Locally authorized awaiting capture
    pending_spend_global: float = 0.0         # In-flight transactions currently in validation
    reserved_spend_global: float = 0.0        # Earmarked holds or sub-delegation pools
    
    permitted_merchant_scopes: List[str] = Field(default_factory=lambda: ["GROCERY", "SUPERMARKET", "UTILITIES"])
    permitted_mccs: List[str] = Field(default_factory=lambda: ["5411", "5499", "4900"])
    semantic_exclusions: List[str] = Field(default_factory=lambda: ["STORED_VALUE", "GIFT_CARD", "CRYPTO_TOKEN", "RE_LIQUEFIABLE"])
    
    active_delegations_by_rail: Dict[PaymentRailType, float] = Field(default_factory=lambda: {
        PaymentRailType.CARD_TOKEN: 10000.0,
        PaymentRailType.UPI_CIRCLE: 10000.0,
        PaymentRailType.AGENTIC_AP2: 10000.0
    })
    
    active_policy: DefensePolicy = DefensePolicy.STANDARD
    delegation_created_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_exposure_global(self) -> float:
        """
        Total Global Exposure across all rails:
        settled + authorized + pending + reserved
        """
        return (
            self.cumulative_spent_settled +
            self.cumulative_spent_authorized +
            self.pending_spend_global +
            self.reserved_spend_global
        )

    @property
    def total_exposure(self) -> float:
        """Alias for total_exposure_global"""
        return self.total_exposure_global

    @property
    def per_rail_delegations(self) -> Dict[str, float]:
        """Dictionary of delegations by rail string key"""
        return {str(k.value if hasattr(k, "value") else k): v for k, v in self.active_delegations_by_rail.items()}

    @property
    def authority_headroom(self) -> float:
        """Remaining uncommitted authority headroom"""
        return max(0.0, self.global_budget_ceiling - self.total_exposure_global)


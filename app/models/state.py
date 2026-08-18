from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone

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

class AuthorityDimension(str, Enum):
    """
    The dimensions a user's delegated authority is expressed in.

    FORSETI's thesis is that authority is MULTIDIMENSIONAL: an agent can stay
    inside the money limit and still act outside what the human authorised.
    Every invariant in dtl/invariant_engine.py names the dimension it guards.
    """
    AMOUNT = "AMOUNT"        # aggregate spend across every rail
    PER_TX = "PER_TX"        # size of any single transaction
    RAIL = "RAIL"            # which payment rails may be used at all
    MERCHANT = "MERCHANT"    # merchant category scope (MCC)
    PURPOSE = "PURPOSE"      # economic substance of the basket
    TIME = "TIME"            # validity window of the delegation


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

    # --- Non-monetary dimensions of the same grant -------------------------
    # A ceiling alone cannot express "₹12,000, groceries, UPI only, this week".
    # These fields carry the rest of what the human actually authorised, and
    # each is guarded by its own machine-checkable invariant.
    permitted_rails: List[PaymentRailType] = Field(default_factory=lambda: [
        PaymentRailType.CARD_TOKEN,
        PaymentRailType.UPI_CIRCLE,
        PaymentRailType.AGENTIC_AP2,
    ])
    per_transaction_cap: Optional[float] = None   # None = no per-transaction limit
    validity_window_hours: float = 168.0          # 7 days, the default grocery grant
    economic_purpose: str = "Household groceries and consumables"

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

    # ------------------------------------------------- non-monetary helpers

    @property
    def permitted_rail_values(self) -> List[str]:
        return [str(getattr(r, "value", r)) for r in self.permitted_rails]

    @property
    def expires_at(self) -> datetime:
        """End of the delegation's validity window."""
        created = self.delegation_created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created + timedelta(hours=float(self.validity_window_hours))

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """True once the grant's validity window has elapsed."""
        ref = now or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        return ref > self.expires_at

    def allows_rail(self, rail: Any) -> bool:
        return str(getattr(rail, "value", rail)) in self.permitted_rail_values

    def authority_vector(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        The full grant, one entry per dimension. This is what the UI renders and
        what every invariant is evaluated against - the ceiling is only one row.
        """
        ref = now or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        remaining_h = max(0.0, (self.expires_at - ref).total_seconds() / 3600.0)
        return {
            "AMOUNT": {
                "dimension": AuthorityDimension.AMOUNT.value,
                "granted": round(self.global_budget_ceiling, 2),
                "used": round(self.total_exposure_global, 2),
                "remaining": round(self.authority_headroom, 2),
                "invariant": "INV_01_GLOBAL_BUDGET_EXCEEDED",
                "label": "Aggregate spend across every rail",
            },
            "PER_TX": {
                "dimension": AuthorityDimension.PER_TX.value,
                "granted": self.per_transaction_cap,
                "invariant": "INV_05_PER_TX_CAP_EXCEEDED",
                "label": "Largest single transaction",
                "unconstrained": self.per_transaction_cap is None,
            },
            "RAIL": {
                "dimension": AuthorityDimension.RAIL.value,
                "granted": self.permitted_rail_values,
                "invariant": "INV_04_UNAUTHORIZED_RAIL",
                "label": "Payment rails the agent may use",
                "unconstrained": len(self.permitted_rail_values) >= 3,
            },
            "MERCHANT": {
                "dimension": AuthorityDimension.MERCHANT.value,
                "granted": list(self.permitted_mccs),
                "invariant": "INV_03_UNAUTHORIZED_MCC",
                "label": "Merchant categories in scope",
            },
            "PURPOSE": {
                "dimension": AuthorityDimension.PURPOSE.value,
                "granted": self.economic_purpose,
                "excluded": list(self.semantic_exclusions),
                "invariant": "INV_02_SEMANTIC_INTENT_DRIFT",
                "label": "Economic purpose of the spend",
            },
            "TIME": {
                "dimension": AuthorityDimension.TIME.value,
                "granted_hours": self.validity_window_hours,
                "expires_at": self.expires_at.isoformat(),
                "hours_remaining": round(remaining_h, 2),
                "expired": self.is_expired(ref),
                "invariant": "INV_06_AUTHORITY_EXPIRED",
                "label": "Validity window of the delegation",
            },
        }


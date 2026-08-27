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
    AGENT_SUSPENDED = "AGENT_SUSPENDED" # Mandate paused pending re-consent after repeated offenses

# Policy overlay constants. Module-level so POLICY_LADDER can quote the real
# numbers instead of restating them in prose.
DTL_POLICY_HEADROOM_BUFFER_PCT = 0.10
DTL_POLICY_STEP_UP_PER_TX = 2000.0
DTL_POLICY_QUARANTINED_PER_TX = 1000.0


# The escalation ladder, in the order Blue climbs it. The frontend used to keep
# its own hand-written copy of this list; it drifted, and AGENT_SUSPENDED - the
# TOP rung, the one that matters most - rendered as no active policy at all.
# Serving the ladder from the enum makes that class of drift impossible.
POLICY_LADDER: List[Dict[str, Any]] = [
    {
        "code": DefensePolicy.STANDARD.value,
        "rung": 0,
        "description": "Baseline. Per-rail checks only, no global aggregation.",
        "enforced_effect": "No overlay - the granted authority is enforced as written.",
    },
    {
        "code": DefensePolicy.STRICT_INVARIANT.value,
        "rung": 1,
        "description": (
            "All seven authority-dimension invariants (amount, per-transaction, rail, "
            "merchant, beneficiary, purpose, time) are enforced on every transaction."
        ),
        "enforced_effect": "No overlay - but every dimension is now checked, not just amount.",
    },
    {
        "code": DefensePolicy.ADAPTIVE_CONTAINMENT.value,
        "rung": 2,
        "description": "Partial authorisation and shadow execution are active.",
        "enforced_effect": "Breaching transactions are trimmed to headroom rather than declined outright.",
    },
    {
        "code": DefensePolicy.TIGHTENED_HEADROOM_V2.value,
        "rung": 3,
        "description": "Headroom buffer reduced after a budget-ceiling breach.",
        "enforced_effect": (
            f"Effective ceiling = {int(DTL_POLICY_HEADROOM_BUFFER_PCT * 100)}% below the granted ceiling."
        ),
    },
    {
        "code": DefensePolicy.STRICT_CATALOG_ATTESTATION.value,
        "rung": 4,
        "description": "Item-level attestation required after semantic drift.",
        "enforced_effect": "Baskets must resolve against the attested SKU catalogue, not merchant free text.",
    },
    {
        "code": DefensePolicy.STEP_UP_VERIFICATION.value,
        "rung": 5,
        "description": (
            "Secondary verification required before authorisation - used for "
            "per-transaction cap breaches and lapsed mandates."
        ),
        "enforced_effect": f"Effective per-transaction cap tightened to ₹{int(DTL_POLICY_STEP_UP_PER_TX):,}.",
    },
    {
        "code": DefensePolicy.CAPABILITY_QUARANTINED.value,
        "rung": 6,
        "description": "Agent spending capability has been downgraded after a violation.",
        "enforced_effect": f"Effective per-transaction cap tightened to ₹{int(DTL_POLICY_QUARANTINED_PER_TX):,}.",
    },
    {
        "code": DefensePolicy.AGENT_SUSPENDED.value,
        "rung": 7,
        "description": "Mandate paused pending re-consent after repeated offences.",
        "enforced_effect": "No spend of any size is authorised until the principal re-consents.",
    },
]

assert {r["code"] for r in POLICY_LADDER} == {p.value for p in DefensePolicy}, (
    "POLICY_LADDER must cover every DefensePolicy member exactly once"
)



_POLICY_RUNG: Dict[str, int] = {row["code"]: row["rung"] for row in POLICY_LADDER}


def policy_rung(policy: Any) -> int:
    """Severity index of a policy on the escalation ladder. Unknown -> 0."""
    return _POLICY_RUNG.get(str(getattr(policy, "value", policy)), 0)


def stricter_policy(current: Any, proposed: Any) -> "DefensePolicy":
    """
    Containment never relaxes in response to a NEW violation.

    Blue used to assign the policy a violation indicated, unconditionally. So an
    agent suspended after three rail breaches could trigger a single, different,
    first-occurrence violation and be handed STEP_UP_VERIFICATION - dropping
    four rungs and resuming spend. A fresh breach must never be a route out of
    containment.

    Lowering the rung is a decision only the principal makes, by re-consenting
    (which resets the authority), never a side effect of misbehaving again.
    """
    return current if policy_rung(current) >= policy_rung(proposed) else proposed


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
    BENEFICIARY = "BENEFICIARY"  # who the money may ultimately settle to


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
    # Empty = unconstrained (any beneficiary), matching permitted_mccs' convention
    # below. Populated for grants like bill payments, where the human authorised
    # money to move to ONE specific counterparty, not merely a merchant category.
    beneficiary_scope: List[str] = Field(default_factory=list)

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

    def allows_beneficiary(self, beneficiary: Optional[str]) -> bool:
        """Unconstrained grants (empty scope) allow any/no beneficiary marker."""
        if not self.beneficiary_scope:
            return True
        return beneficiary is not None and beneficiary in self.beneficiary_scope

    # ------------------------------------------------ active-policy overlay
    #
    # The Blue team's escalation ladder used to write `active_policy` and a set
    # of knobs (`headroom_buffer_pct`, `require_step_up`,
    # `require_sku_attestation`) that NOTHING READ. A grep for readers returned
    # display strings and the PQC payload - no authorization check consulted
    # any of them. An agent whose mandate was "suspended" transacted exactly as
    # before, which made the whole three-rung ladder a label change.
    #
    # This overlay is what makes the ladder real: it maps the active policy to
    # the constraints ACTUALLY ENFORCED, and the invariant engine evaluates
    # against these rather than the raw grant.

    # Headroom withheld under TIGHTENED_HEADROOM_V2 (matches the 0.10 the
    # policy adapter has always written into its `changes` dict).
    POLICY_HEADROOM_BUFFER_PCT: float = DTL_POLICY_HEADROOM_BUFFER_PCT
    # Above this, a single transaction needs human step-up under
    # STEP_UP_VERIFICATION. Below it the agent keeps working uninterrupted,
    # which is the containment-without-lockout principle.
    POLICY_STEP_UP_PER_TX: float = DTL_POLICY_STEP_UP_PER_TX
    # Capability quarantine leaves the agent able to transact, but small.
    POLICY_QUARANTINED_PER_TX: float = DTL_POLICY_QUARANTINED_PER_TX

    @property
    def policy_suspends_all_spend(self) -> bool:
        """AGENT_SUSPENDED means exactly that: nothing is authorised."""
        return self.active_policy == DefensePolicy.AGENT_SUSPENDED

    @property
    def effective_ceiling(self) -> float:
        """The ceiling as the ACTIVE POLICY enforces it."""
        if self.active_policy == DefensePolicy.TIGHTENED_HEADROOM_V2:
            return round(self.global_budget_ceiling * (1.0 - self.POLICY_HEADROOM_BUFFER_PCT), 2)
        return self.global_budget_ceiling

    @property
    def effective_per_transaction_cap(self) -> Optional[float]:
        """
        The per-transaction bound after the policy overlay. The tightest of
        the user's own cap and anything the policy imposes.
        """
        caps = [c for c in (
            self.per_transaction_cap,
            self.POLICY_QUARANTINED_PER_TX
            if self.active_policy == DefensePolicy.CAPABILITY_QUARANTINED else None,
            self.POLICY_STEP_UP_PER_TX
            if self.active_policy == DefensePolicy.STEP_UP_VERIFICATION else None,
        ) if c is not None]
        return min(caps) if caps else None

    def policy_overlay(self) -> Dict[str, Any]:
        """What the active policy currently changes, for the UI and events."""
        return {
            "active_policy": str(getattr(self.active_policy, "value", self.active_policy)),
            "suspends_all_spend": self.policy_suspends_all_spend,
            "granted_ceiling": round(self.global_budget_ceiling, 2),
            "effective_ceiling": round(self.effective_ceiling, 2),
            "ceiling_withheld": round(self.global_budget_ceiling - self.effective_ceiling, 2),
            "granted_per_transaction_cap": self.per_transaction_cap,
            "effective_per_transaction_cap": self.effective_per_transaction_cap,
            "requires_sku_attestation": (
                self.active_policy == DefensePolicy.STRICT_CATALOG_ATTESTATION
            ),
        }

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
            "BENEFICIARY": {
                "dimension": AuthorityDimension.BENEFICIARY.value,
                "granted": list(self.beneficiary_scope),
                "invariant": "INV_07_UNAUTHORIZED_BENEFICIARY",
                "label": "Who the money may ultimately settle to",
                "unconstrained": not self.beneficiary_scope,
            },
        }


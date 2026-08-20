from typing import Any, Dict, Optional, List
from datetime import datetime
from ..models.state import DTLGlobalAuthorityState, PaymentRailType, DefensePolicy
from ..models.transactions import SyntheticTransaction, TransactionState
from ..simulator.event_log import AppendOnlyEventLog

class DTLLedger:
    """
    The Canonical Global Delegation-Trust Ledger.
    Tracks global authority, two-phase exposure, and active defense policies.
    """
    def __init__(self, event_log: Optional[AppendOnlyEventLog] = None):
        self.event_log = event_log or AppendOnlyEventLog()
        self.authorities: Dict[str, DTLGlobalAuthorityState] = {}
        self._init_default_authority()

    def _init_default_authority(self):
        default_auth = DTLGlobalAuthorityState(
            authority_id="auth_household_grocery_2026",
            principal="user_shashank_primary",
            agent_id="agent_household_butler",
            global_budget_ceiling=10000.0,
            cumulative_spent_settled=0.0,
            cumulative_spent_authorized=0.0,
            pending_spend_global=0.0,
            reserved_spend_global=0.0,
            active_policy=DefensePolicy.STANDARD
        )
        self.authorities[default_auth.authority_id] = default_auth

    def get_authority(self, authority_id: str) -> Optional[DTLGlobalAuthorityState]:
        return self.authorities.get(authority_id)

    def register_pending_spend(self, authority_id: str, amount: float):
        auth = self.get_authority(authority_id)
        if auth:
            auth.pending_spend_global += amount
            auth.last_updated_at = datetime.utcnow()

    def finalize_authorized_spend(self, authority_id: str, amount: float):
        auth = self.get_authority(authority_id)
        if auth:
            auth.pending_spend_global = max(0.0, auth.pending_spend_global - amount)
            auth.cumulative_spent_authorized += amount
            auth.last_updated_at = datetime.utcnow()

    def finalize_settled_spend(self, authority_id: str, amount: float):
        auth = self.get_authority(authority_id)
        if auth:
            auth.cumulative_spent_authorized = max(0.0, auth.cumulative_spent_authorized - amount)
            auth.cumulative_spent_settled += amount
            auth.last_updated_at = datetime.utcnow()

    def reset_authority(self, authority_id: str, budget: float = 10000.0,
                        profile: Optional[Dict[str, Any]] = None):
        """
        Re-grants the authority from scratch.

        `profile` carries the NON-monetary dimensions of the grant (permitted
        rails, per-transaction cap, MCC scope, validity window, purpose). A
        delegation is not just a ceiling, so re-granting must be able to restate
        all of it - that is how the arena models "₹12,000, groceries, UPI only,
        this week" as opposed to "₹12,000".
        """
        fields: Dict[str, Any] = dict(
            authority_id=authority_id,
            principal="user_shashank_primary",
            agent_id="agent_household_butler",
            global_budget_ceiling=budget,
            cumulative_spent_settled=0.0,
            cumulative_spent_authorized=0.0,
            pending_spend_global=0.0,
            reserved_spend_global=0.0,
            active_policy=DefensePolicy.STANDARD,
        )
        allowed = set(DTLGlobalAuthorityState.model_fields.keys())
        for key, value in (profile or {}).items():
            if key in allowed and value is not None:
                fields[key] = value
        self.authorities[authority_id] = DTLGlobalAuthorityState(**fields)
        return self.authorities[authority_id]

    def update_authority_scope(self, authority_id: str,
                               profile: Dict[str, Any],
                               allow_none: bool = False) -> Optional[DTLGlobalAuthorityState]:
        """
        Applies dimension changes to a LIVE grant, preserving exposure already
        booked so an operator can watch headroom and scope tighten in real time.

        By default a None value means "leave this dimension alone", so a partial
        update (e.g. only permitted_rails) cannot silently wipe the others.
        `allow_none=True` instead treats None as an explicit value, which is the
        only way to CLEAR an optional dimension - restoring per_transaction_cap
        to "unconstrained" was otherwise impossible, so a cap imposed by one
        attack vector's profile stayed in force for every later round.
        """
        auth = self.get_authority(authority_id)
        if auth is None:
            return None
        allowed = set(DTLGlobalAuthorityState.model_fields.keys())
        for key, value in profile.items():
            if key in allowed and (allow_none or value is not None):
                setattr(auth, key, value)
        auth.last_updated_at = datetime.utcnow()
        return auth

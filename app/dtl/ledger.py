from typing import Dict, Optional, List
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

    def reset_authority(self, authority_id: str, budget: float = 10000.0):
        self.authorities[authority_id] = DTLGlobalAuthorityState(
            authority_id=authority_id,
            principal="user_shashank_primary",
            agent_id="agent_household_butler",
            global_budget_ceiling=budget,
            cumulative_spent_settled=0.0,
            cumulative_spent_authorized=0.0,
            pending_spend_global=0.0,
            reserved_spend_global=0.0,
            active_policy=DefensePolicy.STANDARD
        )

import threading
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime
from ..models.state import DTLGlobalAuthorityState, PaymentRailType, DefensePolicy
from ..models.transactions import SyntheticTransaction, TransactionState
from ..simulator.event_log import AppendOnlyEventLog

class DTLLedger:
    """
    The Canonical Global Delegation-Trust Ledger.
    Tracks global authority, two-phase exposure, and active defense policies.

    EXPOSURE LIFECYCLE. Four buckets, mirroring how an issuer actually accounts
    for delegated spend:

        reserve_hold()            -> pending    (authorization hold placed;
                                                 funds set aside and therefore
                                                 UNAVAILABLE to any other rail)
        finalize_authorized_spend -> authorized (hold converted on approval)
        finalize_settled_spend    -> settled    (funds actually moved)
        release_hold()            -> (freed)    (declined/contained: hold drops)

    All four are counted by `total_exposure_global`, which is what INV_01
    evaluates against. Counting only settled money would leave a window in
    which three rails each approve against the same unspent headroom - the
    exact race this ledger exists to close.

    CONCURRENCY. `try_reserve` performs the check and the hold as ONE atomic
    operation under `_lock`. That is the part that actually prevents the race:
    checking headroom and then separately placing a hold is a classic
    check-then-act bug, and two rails interleaving between those steps will
    both pass a check that only one of them should have. `test_dtl_defense.py`
    exercises this with genuinely concurrent threads rather than asserting it.
    """
    def __init__(self, event_log: Optional[AppendOnlyEventLog] = None):
        self.event_log = event_log or AppendOnlyEventLog()
        self.authorities: Dict[str, DTLGlobalAuthorityState] = {}
        # Re-entrant so a mutating method may call another without deadlock.
        self._lock = threading.RLock()
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
        """Places an authorization hold without checking headroom first."""
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth:
                auth.pending_spend_global += amount
                auth.last_updated_at = datetime.utcnow()

    def try_reserve(self, authority_id: str, amount: float) -> Tuple[bool, Dict[str, float]]:
        """
        ATOMIC check-and-hold. Returns (granted, exposure_snapshot).

        This is the operation that actually closes the cross-rail race. Doing
        it as two steps - read headroom, then place a hold - is check-then-act:
        two rails can both read the same headroom before either writes, and
        both approve. Holding `_lock` across the check AND the write makes the
        pair indivisible, so the second rail observes the first one's hold.

        Granting is deliberately conservative: a hold is placed only if the
        FULL amount fits. Partial capping is the cost governor's decision, not
        the ledger's - the ledger's job is to never let aggregate exposure
        exceed the grant.
        """
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth is None:
                return False, {}
            projected = auth.total_exposure_global + amount
            granted = projected <= auth.global_budget_ceiling
            if granted:
                auth.pending_spend_global += amount
                auth.last_updated_at = datetime.utcnow()
            return granted, {
                "ceiling": auth.global_budget_ceiling,
                "exposure_before": round(auth.total_exposure_global - (amount if granted else 0.0), 2),
                "projected": round(projected, 2),
                "headroom_after": round(auth.authority_headroom, 2),
            }

    def release_hold(self, authority_id: str, amount: float):
        """
        Drops an authorization hold that will not become a charge - declined by
        the rail, or contained by the governor. Without this, a contained
        transaction would leave its hold consuming headroom forever and the
        agent would be slowly starved of a grant it never actually spent.
        """
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth:
                auth.pending_spend_global = max(0.0, auth.pending_spend_global - amount)
                auth.last_updated_at = datetime.utcnow()

    def finalize_authorized_spend(self, authority_id: str, amount: float):
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth:
                auth.pending_spend_global = max(0.0, auth.pending_spend_global - amount)
                auth.cumulative_spent_authorized += amount
                auth.last_updated_at = datetime.utcnow()

    def finalize_settled_spend(self, authority_id: str, amount: float):
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth:
                auth.cumulative_spent_authorized = max(0.0, auth.cumulative_spent_authorized - amount)
                auth.cumulative_spent_settled += amount
                auth.last_updated_at = datetime.utcnow()

    def credit_refund(self, authority_id: str, amount: float) -> float:
        """
        Books a REFUND: money moving back toward the principal, which RELEASES
        consumed authority rather than consuming more of it.

        This existed nowhere. Every transaction that passed the invariants was
        booked as positive exposure regardless of direction, so a Rs 5,000
        refund raised the agent's consumed authority by Rs 5,000 - in a system
        whose entire thesis is aggregate exposure arithmetic. A refund is not a
        purchase, and netting it correctly is the difference between an
        accounting model and a counter.

        Unwinds in lifecycle order (pending first, then authorized, then
        settled), because a refund cancels the most recent, least-final
        commitment first. Returns the amount actually credited: an authority
        cannot be refunded more than it ever spent.
        """
        with self._lock:
            auth = self.get_authority(authority_id)
            if auth is None or amount <= 0:
                return 0.0
            remaining = min(amount, auth.total_exposure_global)
            credited = remaining

            take = min(remaining, auth.pending_spend_global)
            auth.pending_spend_global -= take
            remaining -= take

            take = min(remaining, auth.cumulative_spent_authorized)
            auth.cumulative_spent_authorized -= take
            remaining -= take

            take = min(remaining, auth.cumulative_spent_settled)
            auth.cumulative_spent_settled -= take

            auth.last_updated_at = datetime.utcnow()
            return round(credited, 2)

    def exposure_breakdown(self, authority_id: str) -> Dict[str, float]:
        """The four buckets, for the UI and for assertions about the lifecycle."""
        auth = self.get_authority(authority_id)
        if auth is None:
            return {}
        return {
            "settled": round(auth.cumulative_spent_settled, 2),
            "authorized": round(auth.cumulative_spent_authorized, 2),
            "pending": round(auth.pending_spend_global, 2),
            "reserved": round(auth.reserved_spend_global, 2),
            "total": round(auth.total_exposure_global, 2),
            "ceiling": round(auth.global_budget_ceiling, 2),
            "headroom": round(auth.authority_headroom, 2),
        }

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

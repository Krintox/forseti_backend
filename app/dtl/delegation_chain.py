"""
Agent-to-agent delegation chains.

WHY THIS EXISTS. Three separate findings shared one root cause: the project
talked about sub-agents without modelling them.

  * SCOPE_CREEP claimed "sub-agent delegation widens the economic scope beyond
    the parent grant" and was implemented as a single out-of-category purchase
    with `agent_id="agent_sub_delegate_level3"`. The sub-agent was a STRING.
    Nothing issued it, nothing scoped it, nothing chained authority to it -
    rename the field and the vector is identical.
  * AUTHORITY_IMPERSONATION set `self_approved=True` and its detector's first
    line read `self_approved`. The attack declared its own attack primitive in
    a field one rename away from `please_detect_me`.
  * `reserved_spend_global` was permanently 0.00, because its only honest
    source - authority carved out for a sub-delegate - did not exist.

Agent-to-agent delegation is also the genuinely novel security problem in
agentic payments (surface S7 in this project's own taxonomy), so modelling it
as a naming convention was the largest missed opportunity in the codebase.

WHAT A CHAIN IS. A link records that `grantor` gave `grantee` a NARROWED slice
of an authority, and carries an attestation binding those facts together:

    principal ──grants──▶ agent_butler ──sub-delegates──▶ agent_grocery_bot
                 (root)                    (child, scope ⊆ parent)

Two properties are enforced structurally rather than by self-declaration:

  1. MONOTONIC NARROWING. A child link can only ever be a subset of its
     parent - fewer rails, tighter caps, a smaller pool, an earlier expiry.
     Widening is refused at issuance, so "scope creep" becomes a real
     mechanism with a real failure point instead of a relabelled purchase.
  2. DISTINCT PRINCIPALS + VERIFIABLE ATTESTATION. A link whose grantor is its
     own grantee is refused (four-eyes). A link presented with an attestation
     that does not recompute is refused as forged. Neither check reads a
     self-declared "I am impersonating" flag - a forger's whole objective is
     that the ledger does NOT record it, so detection must rest on something
     the forger cannot produce.

SYNTHETIC SCOPE. The attestation is a SHA-256 digest over the link's binding
fields plus a process-local issuer secret. It is not a signature, there is no
PKI, and an attacker with the process memory could mint one. It models the
PROPERTY (an approval must be producible only by the approver) rather than the
credential format - the same honesty line the tokenization module draws.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..models.state import DTLGlobalAuthorityState, PaymentRailType

# Process-local issuer secret. Regenerated per process on purpose: an
# attestation is not meant to survive a restart, and hardcoding one would make
# every deployment forge-compatible with every other.
_ISSUER_SECRET = os.urandom(16).hex()


@dataclass
class DelegationLink:
    """One hop: grantor gave grantee a narrowed slice of `authority_id`."""

    link_id: str
    authority_id: str
    grantor_id: str
    grantee_id: str
    # Narrowed dimensions. Absent key = inherit the parent's value unchanged.
    permitted_rails: Optional[List[PaymentRailType]] = None
    permitted_mccs: Optional[List[str]] = None
    per_transaction_cap: Optional[float] = None
    # Authority carved out of the parent for this sub-delegate. This is what
    # populates `reserved_spend_global` on the parent authority.
    reserved_pool: float = 0.0
    spent_from_pool: float = 0.0
    parent_link_id: Optional[str] = None
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revoked: bool = False
    attestation: str = ""

    def binding_payload(self) -> str:
        rails = ",".join(sorted(str(getattr(r, "value", r)) for r in (self.permitted_rails or [])))
        mccs = ",".join(sorted(self.permitted_mccs or []))
        return (
            f"{self.link_id}|{self.authority_id}|{self.grantor_id}|{self.grantee_id}|"
            f"{rails}|{mccs}|{self.per_transaction_cap}|{self.reserved_pool:.2f}|"
            f"{self.parent_link_id}"
        )

    def compute_attestation(self) -> str:
        return hashlib.sha256(
            f"{self.binding_payload()}|{_ISSUER_SECRET}".encode()
        ).hexdigest()

    def attestation_valid(self) -> bool:
        return bool(self.attestation) and self.attestation == self.compute_attestation()

    @property
    def pool_remaining(self) -> float:
        return max(0.0, self.reserved_pool - self.spent_from_pool)


class ChainViolation(Exception):
    """Raised when a link cannot be issued because it would widen authority."""


class DelegationChainRegistry:
    """
    Holds the links for one ledger and answers "may THIS agent do THIS?".

    Deliberately separate from DTLLedger: the ledger answers questions about
    an authority's aggregate exposure, this answers questions about who inside
    that authority is allowed to act.
    """

    def __init__(self) -> None:
        self._links: Dict[str, DelegationLink] = {}
        self._by_grantee: Dict[str, str] = {}

    # ------------------------------------------------------------- issuing

    def issue(
        self,
        auth: DTLGlobalAuthorityState,
        *,
        grantor_id: str,
        grantee_id: str,
        reserved_pool: float,
        permitted_rails: Optional[List[PaymentRailType]] = None,
        permitted_mccs: Optional[List[str]] = None,
        per_transaction_cap: Optional[float] = None,
        parent_link_id: Optional[str] = None,
    ) -> DelegationLink:
        """
        Issues a sub-delegation, refusing anything that would WIDEN authority.

        This is the check that makes scope creep a real mechanism: an attacker
        cannot simply declare a broader sub-agent, because issuance compares
        the requested scope against the parent's and refuses a superset.
        """
        if grantor_id == grantee_id:
            raise ChainViolation(
                f"separation of duties: {grantor_id} cannot delegate to itself"
            )

        parent = self._links.get(parent_link_id) if parent_link_id else None
        if parent_link_id and parent is None:
            raise ChainViolation(f"unknown parent link {parent_link_id}")
        if parent is not None and parent.grantee_id != grantor_id:
            raise ChainViolation(
                f"{grantor_id} cannot delegate from a link granted to {parent.grantee_id}"
            )

        # --- monotonic narrowing against the effective parent scope ---
        parent_rails = (
            parent.permitted_rails if parent and parent.permitted_rails is not None
            else list(auth.permitted_rails)
        )
        parent_mccs = (
            parent.permitted_mccs if parent and parent.permitted_mccs is not None
            else list(auth.permitted_mccs)
        )
        parent_cap = (
            parent.per_transaction_cap if parent and parent.per_transaction_cap is not None
            else auth.per_transaction_cap
        )
        parent_pool = parent.pool_remaining if parent else auth.authority_headroom

        if permitted_rails is not None:
            widened = [r for r in permitted_rails
                       if str(getattr(r, "value", r)) not in
                       {str(getattr(p, "value", p)) for p in parent_rails}]
            if widened:
                raise ChainViolation(
                    f"sub-delegation would widen rail scope with {widened}"
                )
        if permitted_mccs is not None:
            widened_mccs = [m for m in permitted_mccs if m not in set(parent_mccs)]
            if widened_mccs:
                raise ChainViolation(
                    f"sub-delegation would widen merchant scope with {widened_mccs}"
                )
        if per_transaction_cap is not None and parent_cap is not None and per_transaction_cap > parent_cap:
            raise ChainViolation(
                f"sub-delegation per-transaction cap {per_transaction_cap} exceeds parent {parent_cap}"
            )
        if reserved_pool > parent_pool + 1e-9:
            raise ChainViolation(
                f"sub-delegation pool {reserved_pool:.2f} exceeds available parent authority "
                f"{parent_pool:.2f}"
            )

        link = DelegationLink(
            link_id=f"link_{uuid.uuid4().hex[:10]}",
            authority_id=auth.authority_id,
            grantor_id=grantor_id,
            grantee_id=grantee_id,
            permitted_rails=list(permitted_rails) if permitted_rails is not None else None,
            permitted_mccs=list(permitted_mccs) if permitted_mccs is not None else None,
            per_transaction_cap=per_transaction_cap,
            reserved_pool=round(reserved_pool, 2),
            parent_link_id=parent_link_id,
        )
        link.attestation = link.compute_attestation()

        self._links[link.link_id] = link
        self._by_grantee[grantee_id] = link.link_id

        if parent is not None:
            parent.spent_from_pool += link.reserved_pool
        else:
            # A root sub-delegation carves its pool out of the authority, which
            # is exactly what `reserved_spend_global` is for.
            auth.reserved_spend_global += link.reserved_pool
        return link

    # ------------------------------------------------------------ querying

    def link_for(self, agent_id: str) -> Optional[DelegationLink]:
        link_id = self._by_grantee.get(agent_id)
        return self._links.get(link_id) if link_id else None

    def get(self, link_id: str) -> Optional[DelegationLink]:
        return self._links.get(link_id)

    def all_links(self) -> List[DelegationLink]:
        return list(self._links.values())

    def revoke(self, link_id: str, auth: Optional[DTLGlobalAuthorityState] = None) -> bool:
        link = self._links.get(link_id)
        if link is None or link.revoked:
            return False
        link.revoked = True
        if auth is not None and link.parent_link_id is None:
            auth.reserved_spend_global = max(
                0.0, auth.reserved_spend_global - link.pool_remaining
            )
        return True

    def register_external(self, link: DelegationLink) -> None:
        """
        Accepts a link object built OUTSIDE this registry - which is how a
        forged or self-issued link enters the system. Deliberately does NOT
        validate: `evaluate_action` is where forgery is caught, so that the
        detection path is exercised rather than bypassed at the door.
        """
        self._links[link.link_id] = link
        self._by_grantee[link.grantee_id] = link.link_id

    # ---------------------------------------------------------- evaluating

    def evaluate_action(
        self, auth: DTLGlobalAuthorityState, agent_id: str, amount: float,
        rail: Any = None, mcc: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """
        Returns (allowed, violation_code, detail) for a sub-agent's action.

        The agent's OWN link is checked, then every ancestor, because a chain is
        only as wide as its narrowest hop. An agent acting directly under the
        principal has no link and is governed by the authority itself.
        """
        link = self.link_for(agent_id)
        if link is None:
            return True, None, {"reason": "no sub-delegation link; acts under the root authority"}

        hops: List[DelegationLink] = []
        seen: set = set()
        cursor: Optional[DelegationLink] = link
        while cursor is not None and cursor.link_id not in seen:
            seen.add(cursor.link_id)
            hops.append(cursor)
            cursor = self._links.get(cursor.parent_link_id) if cursor.parent_link_id else None

        for hop in hops:
            # Forgery / self-approval are structural, not self-declared.
            if not hop.attestation_valid():
                return False, "CHAIN_ATTESTATION_INVALID", {
                    "link_id": hop.link_id,
                    "grantor": hop.grantor_id,
                    "grantee": hop.grantee_id,
                    "reason": ("the link's attestation does not recompute - it was not issued by "
                               "the grantor it names"),
                }
            if hop.grantor_id == hop.grantee_id:
                return False, "CHAIN_SELF_GRANTED", {
                    "link_id": hop.link_id,
                    "grantor": hop.grantor_id,
                    "reason": "an agent cannot be its own approver of record (separation of duties)",
                }
            if hop.revoked:
                return False, "CHAIN_LINK_REVOKED", {"link_id": hop.link_id}

            if hop.permitted_rails is not None and rail is not None:
                allowed_rails = {str(getattr(r, "value", r)) for r in hop.permitted_rails}
                if str(getattr(rail, "value", rail)) not in allowed_rails:
                    return False, "CHAIN_RAIL_OUT_OF_SCOPE", {
                        "link_id": hop.link_id, "permitted": sorted(allowed_rails),
                    }
            if hop.permitted_mccs is not None and mcc is not None and mcc not in hop.permitted_mccs:
                return False, "CHAIN_MERCHANT_OUT_OF_SCOPE", {
                    "link_id": hop.link_id, "permitted": hop.permitted_mccs, "presented": mcc,
                }
            if hop.per_transaction_cap is not None and amount > hop.per_transaction_cap:
                return False, "CHAIN_PER_TX_EXCEEDED", {
                    "link_id": hop.link_id, "cap": hop.per_transaction_cap, "amount": amount,
                }
            if amount > hop.pool_remaining + 1e-9:
                return False, "CHAIN_POOL_EXHAUSTED", {
                    "link_id": hop.link_id,
                    "pool_remaining": round(hop.pool_remaining, 2),
                    "amount": amount,
                }

        return True, None, {"link_id": link.link_id, "hops": len(hops)}

    def consume(self, agent_id: str, amount: float) -> None:
        """Books a successful spend against the agent's pool and its ancestors."""
        link = self.link_for(agent_id)
        seen: set = set()
        while link is not None and link.link_id not in seen:
            seen.add(link.link_id)
            link.spent_from_pool = min(link.reserved_pool, link.spent_from_pool + amount)
            link = self._links.get(link.parent_link_id) if link.parent_link_id else None

    def snapshot(self) -> List[Dict[str, Any]]:
        """UI/event view of the chain."""
        return [
            {
                "link_id": l.link_id,
                "grantor": l.grantor_id,
                "grantee": l.grantee_id,
                "parent_link_id": l.parent_link_id,
                "reserved_pool": round(l.reserved_pool, 2),
                "pool_remaining": round(l.pool_remaining, 2),
                "permitted_rails": [str(getattr(r, "value", r)) for r in (l.permitted_rails or [])],
                "permitted_mccs": l.permitted_mccs,
                "per_transaction_cap": l.per_transaction_cap,
                "revoked": l.revoked,
                "attestation_valid": l.attestation_valid(),
            }
            for l in self._links.values()
        ]

"""
Synthetic tokenized-payment credential model, inspired by token lifecycle and
scoped-authorization concepts in real payment tokenization schemes (EMVCo
Payment Tokenisation, network token vaults). This is NOT an implementation of
any real network's token service - see the module docstring in lifecycle.py
for what it is instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from ..models.state import PaymentRailType


class TokenStatus(str, Enum):
    ISSUED = "ISSUED"      # minted, not yet activated for use
    ACTIVE = "ACTIVE"       # usable, spendable up to its remaining ceiling
    SCOPED = "SCOPED"       # momentarily bound to one in-flight scope check
    USED = "USED"           # exhausted its amount ceiling; terminal
    REVOKED = "REVOKED"     # withdrawn before use or exhaustion; terminal
    EXPIRED = "EXPIRED"     # validity window elapsed; terminal


TERMINAL_STATUSES = (TokenStatus.USED, TokenStatus.REVOKED, TokenStatus.EXPIRED)


class TokenizedPaymentCredential(BaseModel):
    """
    A scoped, time-boxed credential an agent presents to a rail instead of
    raw delegation details. Its own scope is clamped to the DTL authority it
    was issued from at issuance time (see lifecycle.issue_token) - AND every
    use is re-checked against the LIVE authority at use time, so a token can
    never authorize more than the delegation currently allows, even if the
    delegation was tightened or revoked after the token was minted.
    """

    token_id: str
    issuer: str = "FORSETI_SYNTHETIC_TOKEN_ISSUER"
    agent_id: str
    principal_id: str
    authority_id: str

    scope: str
    allowed_rails: List[PaymentRailType]
    merchant_scope: List[str] = Field(default_factory=list)   # MCCs; empty = unconstrained
    purpose_scope: str = "Household groceries and consumables"
    amount_ceiling: float
    per_transaction_limit: Optional[float] = None

    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status: TokenStatus = TokenStatus.ISSUED
    revocation_state: Optional[str] = None

    cumulative_used: float = 0.0
    use_count: int = 0

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        ref = now or datetime.now(timezone.utc)
        expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        return ref > expires

    @property
    def remaining_ceiling(self) -> float:
        return max(0.0, self.amount_ceiling - self.cumulative_used)


class TokenScopeViolation(BaseModel):
    """Evidence that a proposed use fell outside the token's own scope OR the live DTL authority behind it."""

    proof_id: str
    token_id: str
    tx_id: Optional[str] = None
    violation_code: str
    explanation: str

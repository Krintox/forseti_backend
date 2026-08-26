"""
Synthetic tokenized-payment credential lifecycle.

FORSETI includes a synthetic scoped-token model that demonstrates how
tokenized payment credentials can inherit and enforce delegated authority. It
is NOT an implementation of Mastercard MDES, Visa Token Service, or any other
real network token vault - "token_id" here is an opaque synthetic string, not
a network-issued DPAN/token reference, and no cryptographic provisioning flow
is modelled. What IS modelled, deterministically, is the part relevant to
FORSETI's thesis:

    TOKEN SCOPE  ->  DTL AUTHORITY  ->  PAYMENT ACTION

A token's own scope (rails, merchant categories, amount ceiling, per-
transaction limit, validity window) is clamped to the live DTL delegation at
ISSUANCE time (issue_token), and every USE is independently re-checked
against the delegation's CURRENT state (use_token) - so a token cannot
authorize an action outside its delegation even if that delegation has since
been tightened or revoked. The token is never itself a second source of
authority; it is a scoped view onto one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from ..dtl.invariant_engine import DTLInvariantEngine
from ..models.state import DTLGlobalAuthorityState, PaymentRailType
from ..models.transactions import SyntheticTransaction
from .models import TERMINAL_STATUSES, TokenizedPaymentCredential, TokenScopeViolation, TokenStatus

# Stateless - one shared instance is correct and avoids re-constructing the
# engine on every token use.
_INVARIANTS = DTLInvariantEngine()


def _violation(token_id: str, *, code: str, explanation: str, tx_id: Optional[str] = None) -> TokenScopeViolation:
    return TokenScopeViolation(
        proof_id=f"proof_token_{uuid.uuid4().hex[:8]}",
        token_id=token_id, tx_id=tx_id,
        violation_code=code, explanation=explanation,
    )


def issue_token(
    auth: DTLGlobalAuthorityState,
    *,
    agent_id: str,
    principal_id: str,
    scope: str,
    validity_hours: float = 24.0,
    amount_ceiling: Optional[float] = None,
    per_transaction_limit: Optional[float] = None,
    allowed_rails: Optional[List[PaymentRailType]] = None,
    merchant_scope: Optional[List[str]] = None,
    purpose_scope: Optional[str] = None,
) -> TokenizedPaymentCredential:
    """
    Mints a token whose scope is clamped to the live delegation - a caller
    cannot request a wider ceiling, a rail, or a validity window than the
    authority it is drawn from actually grants. This is the FIRST of two
    enforcement points (see use_token for the second, at spend time).
    """
    requested_ceiling = auth.authority_headroom if amount_ceiling is None else amount_ceiling
    clamped_ceiling = max(0.0, min(requested_ceiling, auth.authority_headroom))

    requested_rails = list(allowed_rails) if allowed_rails else list(auth.permitted_rails)
    clamped_rails = [r for r in requested_rails if auth.allows_rail(r)]

    clamped_per_tx = per_transaction_limit
    if auth.per_transaction_cap is not None:
        clamped_per_tx = (
            auth.per_transaction_cap if clamped_per_tx is None
            else min(clamped_per_tx, auth.per_transaction_cap)
        )

    requested_validity = timedelta(hours=validity_hours)
    authority_remaining = auth.expires_at - datetime.now(timezone.utc)
    clamped_expiry = datetime.now(timezone.utc) + min(requested_validity, max(authority_remaining, timedelta(0)))

    return TokenizedPaymentCredential(
        token_id=f"tok_{uuid.uuid4().hex[:12]}",
        agent_id=agent_id,
        principal_id=principal_id,
        authority_id=auth.authority_id,
        scope=scope,
        allowed_rails=clamped_rails,
        merchant_scope=list(merchant_scope) if merchant_scope is not None else list(auth.permitted_mccs),
        purpose_scope=purpose_scope or auth.economic_purpose,
        amount_ceiling=round(clamped_ceiling, 2),
        per_transaction_limit=clamped_per_tx,
        expires_at=clamped_expiry,
        status=TokenStatus.ISSUED,
    )


def activate(token: TokenizedPaymentCredential) -> TokenizedPaymentCredential:
    if token.status != TokenStatus.ISSUED:
        raise ValueError(f"Cannot activate a token in status {token.status}")
    token.status = TokenStatus.ACTIVE
    return token


def revoke(token: TokenizedPaymentCredential, reason: str) -> TokenizedPaymentCredential:
    if token.status in TERMINAL_STATUSES:
        return token
    token.status = TokenStatus.REVOKED
    token.revocation_state = reason
    return token


def check_and_expire(
    token: TokenizedPaymentCredential, now: Optional[datetime] = None
) -> TokenizedPaymentCredential:
    if token.status not in TERMINAL_STATUSES and token.is_expired(now):
        token.status = TokenStatus.EXPIRED
    return token


def use_token(
    token: TokenizedPaymentCredential,
    auth: DTLGlobalAuthorityState,
    tx: SyntheticTransaction,
) -> Tuple[bool, Optional[TokenScopeViolation]]:
    """
    The SECOND enforcement point: independently re-checks the token's own
    scope AND the live DTL authority at the moment of use, not only at
    issuance. A token whose static fields would allow this transaction is
    still refused if the delegation behind it no longer would.
    """
    check_and_expire(token)

    if token.status == TokenStatus.EXPIRED:
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_EXPIRED",
            explanation=f"Token expired at {token.expires_at.isoformat()}; presented after its validity window.",
        )
    if token.status in (TokenStatus.REVOKED,):
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_REVOKED",
            explanation=f"Token was revoked ({token.revocation_state or 'no reason recorded'}).",
        )
    if token.status == TokenStatus.USED:
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_EXHAUSTED",
            explanation="Token has already reached its amount ceiling and is terminal.",
        )
    if token.status not in (TokenStatus.ACTIVE, TokenStatus.ISSUED):
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_NOT_ACTIVE",
            explanation=f"Token is in status {token.status}, not ACTIVE.",
        )

    token.status = TokenStatus.SCOPED  # momentarily bound to this use's scope check

    # ---- the token's OWN static scope
    if tx.rail not in token.allowed_rails:
        token.status = TokenStatus.ACTIVE
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_RAIL_OUT_OF_SCOPE",
            explanation=(
                f"Token allows {[r.value for r in token.allowed_rails]}; transaction presented on "
                f"{tx.rail.value if hasattr(tx.rail, 'value') else tx.rail}."
            ),
        )
    if token.merchant_scope and tx.merchant_mcc not in token.merchant_scope:
        token.status = TokenStatus.ACTIVE
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_MERCHANT_OUT_OF_SCOPE",
            explanation=f"Token merchant scope {token.merchant_scope} excludes MCC {tx.merchant_mcc}.",
        )
    if token.per_transaction_limit is not None and tx.amount > token.per_transaction_limit:
        token.status = TokenStatus.ACTIVE
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_PER_TX_LIMIT_EXCEEDED",
            explanation=f"Rs {tx.amount:,.2f} exceeds the token's per-transaction limit of "
                        f"Rs {token.per_transaction_limit:,.2f}.",
        )
    if tx.amount > token.remaining_ceiling:
        token.status = TokenStatus.ACTIVE
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_AMOUNT_CEILING_EXCEEDED",
            explanation=f"Rs {tx.amount:,.2f} exceeds the token's remaining ceiling of "
                        f"Rs {token.remaining_ceiling:,.2f} (of {token.amount_ceiling:,.2f} total).",
        )

    # ---- the LIVE DTL authority behind the token.
    #
    # This is the enforcement point that makes a token a scoped VIEW onto a
    # delegation rather than a second, independent grant. It runs the FULL
    # invariant engine - the same object the arena uses - rather than a
    # hand-picked subset.
    #
    # It previously checked exactly two things: rail scope and headroom. The
    # docstring claimed a token "cannot authorize an action outside its
    # delegation even if that delegation has since been tightened or revoked",
    # and that claim was false for five of the seven dimensions. An expired
    # delegation demonstrably still authorised spend through this path,
    # because the token carried its OWN frozen `expires_at` and nothing ever
    # consulted `auth.is_expired()`.
    #
    # Re-implementing a subset of the invariants here was the bug. Delegating
    # to the engine means the token layer cannot drift from the DTL again, and
    # any invariant added later is enforced here for free.
    violations = _INVARIANTS.evaluate_all(auth, tx)
    if violations:
        token.status = TokenStatus.ACTIVE
        breached = ", ".join(v.invariant_code for v in violations)
        first = violations[0]
        return False, _violation(
            token.token_id, tx_id=tx.tx_id, code="TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY",
            explanation=(
                f"The token's own scope permits this action, but the LIVE delegation does not: "
                f"{breached}. {first.explanation}"
            ),
        )

    token.cumulative_used = round(token.cumulative_used + tx.amount, 2)
    token.use_count += 1
    token.status = TokenStatus.USED if token.remaining_ceiling <= 0 else TokenStatus.ACTIVE
    return True, None

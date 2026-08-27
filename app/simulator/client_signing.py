"""
Real signature verification for the synthetic rails.

What was wrong
--------------
`SyntheticTransaction.client_signature` defaulted to the literal string
`"ed25519_sig_valid"`, and all three rail adapters "verified" it like this:

    if not tx.client_signature or "invalid" in tx.client_signature:
        return False, "missing or malformed client signature"

That is a substring test on a field the transaction sets about itself. It is the
same defect adversarial review identified in `attack_primitive_type` and
`self_approved`: the attacker declares whether it should be caught, and the
defender agrees. A red-team vector could not fail this check unless it opted in,
and no amount of tampering with the amount, the merchant or the beneficiary
would fail it at all.

What this is
------------
An HMAC-SHA256 over the transaction's canonical economic content, keyed per
agent. Modest, but genuinely a signature: the rail recomputes the tag from the
fields it is looking at, and any mismatch means the bytes that were signed are
not the bytes being authorised.

The properties that follow are real, not asserted:

  * tampering with amount, merchant, MCC, rail, beneficiary or the cart AFTER
    signing invalidates the tag - the rail rejects on arithmetic, not on a label
  * an agent that does not hold the key cannot mint a valid tag
  * replacing the signature with junk fails, and so does removing it

What this is NOT
----------------
Not EMV. Not a payment-network cryptogram. Not ARQC, not a DPAN cryptogram, not
AP2's mandate signing. Those are network-specific constructions this project
does not implement and must not claim - the rails here model authorization
LOGIC. HMAC is chosen precisely because it is honest about being a shared-secret
integrity check rather than dressed up as something it is not.

The only post-quantum cryptography in this repository remains the ML-DSA-44
audit layer (`crypto/mldsa_audit.py`), which signs the proof log. This module is
deliberately separate from it so neither claim contaminates the other.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..models.transactions import SyntheticTransaction

#: Per-process demonstration secret. Random by default for the same reason the
#: PQC key is: a copy of this repository must not hold a running instance's key.
#: `FORSETI_CLIENT_SIGNING_SEED` makes it deterministic for tests.
_SEED = os.environ.get("FORSETI_CLIENT_SIGNING_SEED")
_ROOT_SECRET = _SEED.encode("utf-8") if _SEED else os.urandom(32)

SIGNATURE_PREFIX = "hmac-sha256:"


def _agent_key(agent_id: str) -> bytes:
    """Derives a per-agent key, so one agent cannot mint another's signatures."""
    return hmac.new(_ROOT_SECRET, agent_id.encode("utf-8"), hashlib.sha256).digest()


def canonical_payload(tx: "SyntheticTransaction") -> bytes:
    """
    The economic content a signature commits to.

    Deliberately excludes anything the RAIL decides afterwards - state, status
    strings, timestamps of authorisation - so a legitimate authorisation does not
    invalidate the signature that authorised it. It includes everything an
    attacker would want to change in flight.
    """
    items = "|".join(
        f"{i.sku}:{i.category}:{i.unit_price:.2f}x{i.quantity}"
        for i in sorted(tx.items or [], key=lambda i: (i.sku or "", i.name or ""))
    )
    parts = [
        tx.tx_id or "",
        tx.authority_id or "",
        tx.agent_id or "",
        str(getattr(tx.rail, "value", tx.rail) or ""),
        f"{float(tx.amount):.2f}",
        tx.currency or "",
        tx.merchant_id or "",
        tx.merchant_mcc or "",
        tx.vpa_delegate or "",
        items,
    ]
    return "\x1f".join(parts).encode("utf-8")


def sign(tx: "SyntheticTransaction") -> str:
    """Produces the tag an honest client would attach."""
    tag = hmac.new(_agent_key(tx.agent_id or ""), canonical_payload(tx), hashlib.sha256)
    return SIGNATURE_PREFIX + tag.hexdigest()


def verify(tx: "SyntheticTransaction") -> Tuple[bool, Optional[str]]:
    """
    Recomputes the tag over what the rail is actually being asked to authorise.

    Returns (ok, reason_if_not). Compared with `hmac.compare_digest`, not `==`.
    """
    supplied = tx.client_signature
    if not supplied:
        return False, "no client signature present"
    if not supplied.startswith(SIGNATURE_PREFIX):
        return False, f"unrecognised signature scheme (expected {SIGNATURE_PREFIX}...)"
    expected = sign(tx)
    if not hmac.compare_digest(supplied, expected):
        return False, "signature does not match the transaction it is attached to"
    return True, None


def sign_in_place(tx: "SyntheticTransaction") -> "SyntheticTransaction":
    """Convenience for generators: sign whatever was just built."""
    tx.client_signature = sign(tx)
    return tx


def tamper_after_signing(tx: "SyntheticTransaction", **changes: Any) -> "SyntheticTransaction":
    """
    Mutates a signed transaction WITHOUT re-signing.

    This is what an in-flight tamper looks like, and it exists so tests and
    red-team vectors can produce a genuinely invalid signature instead of
    writing the word "invalid" into the field and trusting the rail to notice.
    """
    for field, value in changes.items():
        setattr(tx, field, value)
    return tx

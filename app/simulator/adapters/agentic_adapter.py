import hashlib
from typing import List, Optional, Tuple
from .base import BaseRailAdapter
from ...models.transactions import CartItem, SyntheticTransaction
from ...models.state import TransactionState, PaymentRailType
from datetime import datetime
from ..client_signing import verify as verify_client_signature


class AgenticAp2Adapter(BaseRailAdapter):
    """
    Agentic / AP2-Inspired Payment Adapter.

    Models the Intent Mandate -> Cart Mandate binding that AP2 expresses as
    signed Verifiable Credentials: the buyer-side agent verifies that the cart
    it is about to pay for is the cart the signed intent authorised.

    WHAT THIS PREVIOUSLY DID, AND WHY IT WAS WRONG. The adapter FABRICATED both
    mandate hashes whenever they were absent, never compared them to anything,
    and then reported "Valid AP2 W3C Verifiable Credential mandate chain, hash
    verified" on screen during the demo. It asserted a verification that could
    not fail because it was never performed.

    What it does now:
      * `compute_intent_hash` commits to (purpose, ceiling);
      * `compute_cart_hash` commits to (intent hash, amount, canonicalised cart
        line items) - so ANY substitution of the basket after the intent was
        signed changes the hash;
      * when a transaction presents a chain, it is RECOMPUTED and COMPARED, and
        a mismatch is refused;
      * when a transaction presents no chain, the adapter says so plainly
        rather than inventing one and calling it verified.

    This remains a synthetic model: the hashes are SHA-256 over canonical
    strings, not W3C Verifiable Credentials, and there are no issuer keys or
    signatures. It models the BINDING PROPERTY, not the credential format.
    """

    def __init__(self, local_limit: float = 10000.0):
        super().__init__(PaymentRailType.AGENTIC_AP2.value, local_limit)

    # ------------------------------------------------------- mandate chain

    @staticmethod
    def compute_intent_hash(purpose: str, ceiling: float) -> str:
        """Commits to what the human authorised, before any cart exists."""
        return hashlib.sha256(f"INTENT|{purpose}|{ceiling:.2f}".encode()).hexdigest()

    @staticmethod
    def compute_cart_hash(intent_hash: str, amount: float, items: List[CartItem]) -> str:
        """
        Commits to the intent AND the exact basket. Canonicalised by SKU so
        line ordering cannot change the digest - otherwise a reordering would
        read as a substitution.
        """
        canonical = "|".join(
            f"{i.sku}:{i.category}:{i.unit_price:.2f}x{i.quantity}"
            for i in sorted(items, key=lambda x: x.sku)
        )
        return hashlib.sha256(
            f"CART|{intent_hash}|{amount:.2f}|{canonical}".encode()
        ).hexdigest()

    def verify_mandate_chain(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        """
        Returns (chain_ok, human_readable_status).

        `chain_ok` is False ONLY on a genuine mismatch. A transaction that
        presents no chain is not a failure - it is an unsigned mandate, and the
        status string says exactly that instead of claiming a verification.
        """
        if not tx.intent_mandate_hash or not tx.cart_mandate_hash:
            return True, "no mandate chain presented (unsigned agentic payment)"

        expected = self.compute_cart_hash(
            tx.intent_mandate_hash, tx.amount, list(tx.items)
        )
        if expected != tx.cart_mandate_hash:
            return False, (
                "cart mandate does not match the signed intent - the basket changed "
                "after the intent was authorised"
            )
        return True, "intent -> cart mandate binding recomputed and matched"

    # ------------------------------------------------------- authorization

    def validate_and_authorize_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        # 1. Local AP2 session limit.
        if (self.local_spent + tx.amount) > self.local_limit:
            return False, f"LOCAL_AP2_REJECT: Amount ₹{tx.amount:.2f} would take this cycle's rail spend to ₹{self.local_spent + tx.amount:.2f}, past the AP2 session mandate limit ₹{self.local_limit:.2f}"

        # 2. Mandate-chain binding - actually verified when one is presented.
        chain_ok, chain_status = self.verify_mandate_chain(tx)
        if not chain_ok:
            return False, f"LOCAL_AP2_REJECT: {chain_status}"

        # 3. Signature presence. As with the card rail, this is a field check
        #    and is not described as cryptographic verification, because no key
        #    or signature exists here to verify.
        # A REAL check: recompute the HMAC over the canonical economic
        # content of this transaction. Previously this was `"invalid" in
        # tx.client_signature` - a substring test on a field the
        # transaction set about itself, which no in-flight tampering
        # could break. See simulator/client_signing.py.
        signature_ok, signature_problem = verify_client_signature(tx)
        if not signature_ok:
            return False, f"LOCAL_AP2_REJECT: {signature_problem}"

        # Success locally
        self.local_spent += tx.amount
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = f"AP2 session mandate within limit; {chain_status}"
        tx.state = TransactionState.AUTHORIZED
        tx.authorized_at = datetime.utcnow()
        return True, tx.local_rail_message

    def capture_local(self, tx: SyntheticTransaction) -> bool:
        if tx.state == TransactionState.AUTHORIZED:
            tx.state = TransactionState.SETTLED
            tx.settled_at = datetime.utcnow()
            return True
        return False

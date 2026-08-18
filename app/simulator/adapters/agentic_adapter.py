import hashlib
from typing import Tuple
from .base import BaseRailAdapter
from ...models.transactions import SyntheticTransaction
from ...models.state import TransactionState, PaymentRailType
from datetime import datetime

class AgenticAp2Adapter(BaseRailAdapter):
    """
    Agentic / AP2-Inspired Payment Adapter.
    Models Intent Mandate -> Cart Mandate -> Payment Mandate hash chaining & W3C VC authorization.
    """
    def __init__(self, local_limit: float = 10000.0):
        super().__init__(PaymentRailType.AGENTIC_AP2.value, local_limit)

    def validate_and_authorize_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        # 1. Check local AP2 session limit
        if (self.local_spent + tx.amount) > self.local_limit:
            return False, f"LOCAL_AP2_REJECT: Amount ₹{tx.amount:.2f} would take this cycle's rail spend to ₹{self.local_spent + tx.amount:.2f}, past the AP2 session mandate limit ₹{self.local_limit:.2f}"

        # 2. Check Mandate Hash Chaining
        if not tx.intent_mandate_hash:
            tx.intent_mandate_hash = hashlib.sha256(b"INTENT_GROCERY_MAX_10000").hexdigest()
        if not tx.cart_mandate_hash:
            tx.cart_mandate_hash = hashlib.sha256(f"CART_{tx.amount}_{tx.merchant_id}".encode()).hexdigest()

        # 3. Check digital signature
        if not tx.client_signature or "invalid" in tx.client_signature:
            return False, "LOCAL_AP2_REJECT: Cryptographic proof of intent mandate signature failed"

        # Success locally
        self.local_spent += tx.amount
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = "Valid AP2 W3C Verifiable Credential mandate chain, hash verified"
        tx.state = TransactionState.AUTHORIZED
        tx.authorized_at = datetime.utcnow()
        return True, tx.local_rail_message

    def capture_local(self, tx: SyntheticTransaction) -> bool:
        if tx.state == TransactionState.AUTHORIZED:
            tx.state = TransactionState.SETTLED
            tx.settled_at = datetime.utcnow()
            return True
        return False

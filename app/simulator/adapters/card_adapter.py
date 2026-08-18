from typing import Tuple
from .base import BaseRailAdapter
from ...models.transactions import SyntheticTransaction
from ...models.state import TransactionState, PaymentRailType
from datetime import datetime

class CardTokenAdapter(BaseRailAdapter):
    """
    Card-Tokenization-Inspired Synthetic Adapter.
    Models tokenized DPAN validation, single-transaction & local cycle limits, and MCC scoping.
    """
    def __init__(self, local_limit: float = 10000.0):
        super().__init__(PaymentRailType.CARD_TOKEN.value, local_limit)
        self.allowed_mccs = {"5411", "5499", "4900", "5311"} # Grocery, Supermarket, Utilities, Dept Store

    def validate_and_authorize_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        # 1. Check MCC scope
        if tx.merchant_mcc not in self.allowed_mccs:
            return False, f"LOCAL_CARD_REJECT: MCC {tx.merchant_mcc} not permitted in token scope"

        # 2. Check local card limit
        if (self.local_spent + tx.amount) > self.local_limit:
            return False, f"LOCAL_CARD_REJECT: Amount ₹{tx.amount} would take this cycle\'s rail spend to ₹{self.local_spent + tx.amount:.2f}, past the local card ceiling ₹{self.local_limit:.2f}"

        # 3. Check digital signature / cryptogram
        if not tx.client_signature or "invalid" in tx.client_signature:
            return False, "LOCAL_CARD_REJECT: Invalid cryptographic token signature"

        # Success locally
        self.local_spent += tx.amount
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = "Valid token cryptogram, permitted MCC 5411, within local card limit"
        tx.state = TransactionState.AUTHORIZED
        tx.authorized_at = datetime.utcnow()
        return True, tx.local_rail_message

    def capture_local(self, tx: SyntheticTransaction) -> bool:
        if tx.state == TransactionState.AUTHORIZED:
            tx.state = TransactionState.SETTLED
            tx.settled_at = datetime.utcnow()
            return True
        return False

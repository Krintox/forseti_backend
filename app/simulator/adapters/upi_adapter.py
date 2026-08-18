from typing import Tuple
from .base import BaseRailAdapter
from ...models.transactions import SyntheticTransaction
from ...models.state import TransactionState, PaymentRailType
from datetime import datetime

class UpiCircleAdapter(BaseRailAdapter):
    """
    UPI-Circle-Inspired Delegation Adapter.
    Models secondary device/app delegation, primary-secondary VPA linkage, and monthly delegate caps.
    """
    def __init__(self, local_limit: float = 10000.0):
        super().__init__(PaymentRailType.UPI_CIRCLE.value, local_limit)

    def validate_and_authorize_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        # 1. Check local UPI delegate limit
        if (self.local_spent + tx.amount) > self.local_limit:
            return False, f"LOCAL_UPI_REJECT: Amount ₹{tx.amount:.2f} would take this cycle's rail spend to ₹{self.local_spent + tx.amount:.2f}, past the monthly delegate limit ₹{self.local_limit:.2f}"

        # 2. Check delegate VPA presence
        if not tx.vpa_delegate:
            tx.vpa_delegate = "agent_butler@npci_uap"

        # 3. Check digital signature
        if not tx.client_signature or "invalid" in tx.client_signature:
            return False, "LOCAL_UPI_REJECT: Invalid device MPIN / signature token"

        # Success locally
        self.local_spent += tx.amount
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = "Valid UPI Circle delegate mandate, within local ₹10k monthly cap"
        tx.state = TransactionState.AUTHORIZED
        tx.authorized_at = datetime.utcnow()
        return True, tx.local_rail_message

    def capture_local(self, tx: SyntheticTransaction) -> bool:
        if tx.state == TransactionState.AUTHORIZED:
            tx.state = TransactionState.SETTLED
            tx.settled_at = datetime.utcnow()
            return True
        return False

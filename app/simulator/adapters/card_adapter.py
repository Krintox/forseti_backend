from typing import Tuple
from .base import BaseRailAdapter
from ...models.transactions import SyntheticTransaction
from ...models.state import TransactionState, PaymentRailType
from datetime import datetime

class CardTokenAdapter(BaseRailAdapter):
    """
    Card-Tokenization-Inspired Synthetic Adapter.

    Models tokenized DPAN validation, single-transaction & local cycle limits,
    and MCC scoping at the token.

    NOTE ON WHAT IS AND IS NOT NOVEL HERE. Merchant-category restriction on a
    network token is a real, shipped, commercially available control - it is
    set at PROVISIONING time and Mastercard sells the product that does it.
    This adapter therefore enforces it, and FORSETI must not claim MCC scope as
    something "only the delegation knows": the rail in this very repository
    checks it, twenty lines from here.

    Two things remain genuinely unsolved, and those are INV_03's actual scope:
      * the other two rails here (UPI Circle, agentic AP2) have no MCC concept
        at all, so a category bound expressed by the USER is unenforceable on
        them;
      * a token's allow-list is fixed at provisioning. If the principal
        NARROWS their delegation afterwards, no rail re-scopes the already
        issued token.
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

        # 3. Signature presence check.
        #
        # Deliberately NOT described as a cryptogram verification. This is a
        # substring test against a field the simulator sets, and there is no
        # EMV cryptogram, no ARQC, no key, and nothing to verify against - so
        # calling it "Valid token cryptogram" on screen (as this previously
        # did) asserted a check that never happened. The synthetic rails model
        # authorization LOGIC, not payment cryptography; the only genuine
        # cryptography in this project is the ML-DSA-44 audit layer.
        if not tx.client_signature or "invalid" in tx.client_signature:
            return False, "LOCAL_CARD_REJECT: missing or malformed client signature field"

        # Success locally
        self.local_spent += tx.amount
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = (
            f"Signature field present, MCC {tx.merchant_mcc} inside this token's provisioned "
            f"scope, within local card cycle limit ₹{self.local_limit:,.0f}"
        )
        tx.state = TransactionState.AUTHORIZED
        tx.authorized_at = datetime.utcnow()
        return True, tx.local_rail_message

    def capture_local(self, tx: SyntheticTransaction) -> bool:
        if tx.state == TransactionState.AUTHORIZED:
            tx.state = TransactionState.SETTLED
            tx.settled_at = datetime.utcnow()
            return True
        return False

from typing import Tuple, Optional
from ..models.transactions import SyntheticTransaction, CartItem
from ..models.state import DTLGlobalAuthorityState, TransactionState, DefensePolicy
from ..models.proofs import SemanticDriftProof

class AdversarialCostGovernor:
    """
    Executes Graceful Economic Containment:
    Instead of hard-blocking the entire user card / killing the AI butler, it executes:
    1. Partial Authorization: Approves legitimate grocery items (e.g. ₹1,000 milk/eggs).
    2. Shadow Quarantine: Routes unauthorized liquid/gift card items to decoy ledger.
    3. Capability Degradation: Downgrades the agent's single-tx limit to prevent further drain.
    """
    def __init__(self):
        pass

    def apply_containment(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        proof: SemanticDriftProof
    ) -> Tuple[SyntheticTransaction, str]:
        """
        Applies economic containment without breaking legitimate commerce.
        """
        # Separate legitimate grocery items from stored-value items
        legitimate_items = []
        quarantined_items = []
        legit_total = 0.0
        quarantine_total = 0.0

        for item in tx.items:
            if item.sku in proof.violated_skus or item.is_stored_value:
                quarantined_items.append(item)
                quarantine_total += item.total_price
            else:
                legitimate_items.append(item)
                legit_total += item.total_price

        if legitimate_items and quarantine_total > 0:
            # PARTIAL AUTHORIZATION ACTION
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                f"PARTIAL_AUTH: Approved ₹{legit_total:.2f} for {len(legitimate_items)} genuine grocery items; "
                f"Quarantined ₹{quarantine_total:.2f} stored-value gift cards to shadow sandbox."
            )
            # Downgrade agent capability
            auth.active_policy = DefensePolicy.CAPABILITY_QUARANTINED
            auth.global_budget_ceiling = max(0.0, auth.global_budget_ceiling - legit_total)
            return tx, tx.containment_action

        elif proof.invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED":
            # OVER-BUDGET SPLIT CONTAINMENT
            available_headroom = auth.authority_headroom
            if available_headroom > 0:
                tx.state = TransactionState.QUARANTINED
                tx.containment_action = (
                    f"HEADROOM_CAP: Partial authorization of ₹{available_headroom:.2f} granted; "
                    f"Excess ₹{tx.amount - available_headroom:.2f} held in pending verification."
                )
                auth.cumulative_spent_authorized += available_headroom
                return tx, tx.containment_action
            else:
                tx.state = TransactionState.QUARANTINED
                tx.containment_action = "CAPABILITY_CONTAINED: Authority ceiling reached. Agent spend quarantined without user lockout."
                return tx, tx.containment_action

        else:
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = "SHADOW_QUARANTINE: Transaction routed to decoy sandbox. Legitimate user account unaffected."
            return tx, tx.containment_action

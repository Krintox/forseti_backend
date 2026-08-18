"""
Graceful economic containment.

A blanket block is itself an attack surface: flood an authorizer with
revocations and you lock the real customer out of their own money. The governor
therefore answers each authority violation with the SMALLEST response that
restores the grant, and the response depends on WHICH dimension was violated:

    PURPOSE  (INV_02) -> partial authorization: clear the genuine basket value,
                         quarantine only the stored-value portion
    AMOUNT   (INV_01) -> headroom cap: authorise what is still inside the grant
    PER_TX   (INV_05) -> step-up: the user, not the agent, approves this one
    RAIL     (INV_04) -> rail-scope block: nothing is booked, the permitted
                         rails stay usable
    MERCHANT (INV_03) -> scope quarantine to a shadow ledger
    TIME     (INV_06) -> hold pending re-consent

Only the AMOUNT and PURPOSE paths book money. Scope violations consume no
authority at all, which is the point: the agent keeps its remaining grant and
the user keeps their working payment instruments.
"""

from typing import Optional, Tuple

from ..models.proofs import SemanticDriftProof
from ..models.state import DefensePolicy, DTLGlobalAuthorityState, TransactionState
from ..models.transactions import CartItem, SyntheticTransaction


class AdversarialCostGovernor:
    """Turns a proven authority violation into a proportionate action."""

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
        Returns the mutated transaction and a human-readable action string.
        """
        code = proof.invariant_code

        # ---------------------------------------------- RAIL scope violation
        # The user authorised other rails; those must keep working. Nothing is
        # booked against the ceiling, because no authority was legitimately used.
        if code == "INV_04_UNAUTHORIZED_RAIL":
            tx.state = TransactionState.QUARANTINED
            permitted = ", ".join(auth.permitted_rail_values) or "none"
            rail = str(getattr(tx.rail, "value", tx.rail))
            tx.containment_action = (
                f"RAIL_SCOPE_BLOCK: ₹{tx.amount:,.2f} on {rail} refused - that rail is outside the "
                f"delegation. Permitted rails ({permitted}) remain fully usable and no headroom was "
                f"consumed."
            )
            auth.active_policy = DefensePolicy.STRICT_INVARIANT
            return tx, tx.containment_action

        # ------------------------------------------ PER-TX cap: escalate, not block
        # The grant is intact; only this one action is too large to be autonomous.
        if code == "INV_05_PER_TX_CAP_EXCEEDED":
            cap = auth.per_transaction_cap or 0.0
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                f"STEP_UP_REQUIRED: ₹{tx.amount:,.2f} exceeds the ₹{cap:,.2f} per-transaction cap. "
                f"Held for user confirmation rather than declined - the agent may still transact "
                f"up to ₹{cap:,.2f} without interruption."
            )
            auth.active_policy = DefensePolicy.STEP_UP_VERIFICATION
            return tx, tx.containment_action

        # ------------------------------------------------- TIME: re-consent
        if code == "INV_06_AUTHORITY_EXPIRED":
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                f"RE_CONSENT_HOLD: the delegation lapsed at {auth.expires_at.isoformat()}. "
                f"₹{tx.amount:,.2f} is held pending a fresh grant; the user's own instruments are "
                f"untouched."
            )
            auth.active_policy = DefensePolicy.STEP_UP_VERIFICATION
            return tx, tx.containment_action

        # ------------------------------------------ MERCHANT scope violation
        if code == "INV_03_UNAUTHORIZED_MCC":
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                f"SCOPE_QUARANTINE: MCC {tx.merchant_mcc} is outside the delegated categories "
                f"{auth.permitted_mccs}. ₹{tx.amount:,.2f} routed to the shadow ledger; in-scope "
                f"merchants continue to clear normally."
            )
            auth.active_policy = DefensePolicy.STRICT_CATALOG_ATTESTATION
            return tx, tx.containment_action

        # --------------------------------- PURPOSE drift: split the basket
        legitimate_items: list[CartItem] = []
        quarantined_items: list[CartItem] = []
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

        # ------------------------------------- AMOUNT: cap at real headroom
        elif code == "INV_01_GLOBAL_BUDGET_EXCEEDED":
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

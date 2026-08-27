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
    BENEFICIARY (INV_07) -> counterparty-scope block: nothing is booked, the
                         delegation for OTHER beneficiaries stays usable

Only the AMOUNT and PURPOSE paths book money. Scope violations consume no
authority at all, which is the point: the agent keeps its remaining grant and
the user keeps their working payment instruments.
"""

from typing import List, Optional, Tuple

from ..models.proofs import SemanticDriftProof
from ..models.state import DefensePolicy, DTLGlobalAuthorityState, TransactionState
from ..models.transactions import CartItem, SyntheticTransaction


class AdversarialCostGovernor:
    """Turns a proven authority violation into a proportionate action."""

    # When a transaction violates SEVERAL dimensions at once, which response
    # wins must be a stated policy, not an accident of INVARIANT_REGISTRY
    # ordering. Scope violations outrank economic ones because they are
    # absolute: if the rail, beneficiary, merchant category or validity window
    # is wrong, no amount of the transaction is authorised, so there is nothing
    # to partially clear. Only once the action is in-scope does it become a
    # question of how much may be booked.
    _PRECEDENCE = (
        "INV_06_AUTHORITY_EXPIRED",          # expired grant authorises nothing
        "INV_04_UNAUTHORIZED_RAIL",          # wrong road
        "INV_07_UNAUTHORIZED_BENEFICIARY",   # wrong counterparty
        "INV_03_UNAUTHORIZED_MCC",           # wrong merchant category
        "INV_05_PER_TX_CAP_EXCEEDED",        # in scope, single action too large
        "INV_02_SEMANTIC_INTENT_DRIFT",      # in scope, basket partly outside purpose
        "INV_01_GLOBAL_BUDGET_EXCEEDED",     # in scope, aggregate too large
    )

    def __init__(self):
        pass

    def select_governing_proof(
        self, violations: List[SemanticDriftProof]
    ) -> Optional[SemanticDriftProof]:
        """
        Picks which violation drives containment, by the stated precedence
        above rather than by whichever happened to be evaluated first.

        This matters: a cart that breaches BOTH the ceiling and the purpose
        used to be contained by whichever invariant the registry listed
        earlier, which is not a policy anyone chose.
        """
        if not violations:
            return None
        by_code = {v.invariant_code: v for v in violations}
        for code in self._PRECEDENCE:
            if code in by_code:
                return by_code[code]
        return violations[0]

    def apply_containment(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        proof: SemanticDriftProof
    ) -> Tuple[SyntheticTransaction, str]:
        """
        Applies economic containment without breaking legitimate commerce.
        Returns the mutated transaction and a human-readable action string.

        INVARIANT UPHELD BY EVERY BRANCH: this method never mutates
        `auth.global_budget_ceiling`. The ceiling is the principal's grant.
        Containment may book exposure against it, refuse to book anything, or
        escalate policy - it may not quietly redefine what was granted. A
        regression test pins this, because the original implementation did
        exactly that and the PQC layer then signed the falsified amount.
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
            tx.containment_code = "RAIL_SCOPE_BLOCK"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
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
            tx.containment_code = "STEP_UP_REQUIRED"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
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
            tx.containment_code = "RE_CONSENT_HOLD"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
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
            tx.containment_code = "SCOPE_QUARANTINE"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
            return tx, tx.containment_action

        # ---------------------------------------------- BENEFICIARY violation
        # Rail, amount and merchant category can all be in scope; only the
        # settlement counterparty is not who the grant named. Like the RAIL
        # branch above, nothing is booked - authorised beneficiaries stay
        # fully usable and no headroom is consumed.
        if code == "INV_07_UNAUTHORIZED_BENEFICIARY":
            tx.state = TransactionState.QUARANTINED
            scope = ", ".join(auth.beneficiary_scope) or "none"
            tx.containment_action = (
                f"BENEFICIARY_SCOPE_BLOCK: ₹{tx.amount:,.2f} to "
                f"{tx.vpa_delegate or 'an unrecorded beneficiary'} refused - that counterparty is "
                f"outside the delegation. Authorised beneficiaries ({scope}) remain fully usable and "
                f"no headroom was consumed."
            )
            auth.active_policy = DefensePolicy.STRICT_INVARIANT
            tx.containment_code = "BENEFICIARY_SCOPE_BLOCK"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
            return tx, tx.containment_action

        # --------------------------------- PURPOSE drift: split the basket
        #
        # Explicitly guarded by its invariant code. It previously was not, so
        # this block ran for ANY violation that fell through the scope branches
        # above and returned before the AMOUNT branch below could be reached.
        # A genuine budget breach whose cart happened to contain a gift card
        # was therefore answered by "approve the milk" - a Rs 9,500 spend
        # against Rs 1,000 of headroom cleared Rs 1,000 of groceries and never
        # took the headroom-cap path at all.
        if code == "INV_02_SEMANTIC_INTENT_DRIFT":
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
                # The legitimate remainder is still bound by the ceiling: a
                # purpose violation does not entitle the agent to spend past
                # its grant, so the cleared amount is capped at real headroom.
                bookable = min(legit_total, max(0.0, auth.authority_headroom))
                tx.state = TransactionState.QUARANTINED
                capped_note = (
                    "" if bookable >= legit_total
                    else f" Cleared amount capped at Rs {bookable:,.2f} by remaining headroom."
                )
                tx.containment_action = (
                    f"PARTIAL_AUTH: Approved Rs {bookable:,.2f} for {len(legitimate_items)} genuine "
                    f"in-purpose item(s); quarantined Rs {quarantine_total:,.2f} of stored value to "
                    f"the shadow sandbox.{capped_note}"
                )
                auth.active_policy = DefensePolicy.CAPABILITY_QUARANTINED
                # BOOK the approved spend as exposure. This previously
                # SUBTRACTED it from global_budget_ceiling instead, which
                # silently rewrote the principal's grant: approving Rs 1,000 of
                # groceries turned a Rs 10,000 delegation into a Rs 9,000 one
                # without asking anybody, and crypto/mldsa_audit.py then signed
                # that falsified ceiling into the tamper-evident audit trail.
                # Headroom coincidentally matched, which is why it went unseen.
                auth.cumulative_spent_authorized += bookable
                tx.containment_code = "PARTIAL_AUTH"
                tx.authorized_amount = round(bookable, 2)
                tx.quarantined_amount = round(float(tx.amount) - bookable, 2)
                return tx, tx.containment_action

            # Whole basket is stored value: nothing legitimate to clear.
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                f"FULL_QUARANTINE: the entire Rs {quarantine_total:,.2f} basket is outside the "
                f"delegated purpose; nothing was booked and the grant is untouched."
            )
            auth.active_policy = DefensePolicy.STRICT_CATALOG_ATTESTATION
            tx.containment_code = "FULL_QUARANTINE"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
            return tx, tx.containment_action

        # ------------------------------------- AMOUNT: cap at real headroom
        if code == "INV_01_GLOBAL_BUDGET_EXCEEDED":
            available_headroom = max(0.0, auth.authority_headroom)
            if available_headroom > 0:
                tx.state = TransactionState.QUARANTINED
                tx.containment_action = (
                    f"HEADROOM_CAP: partial authorization of Rs {available_headroom:,.2f} granted; "
                    f"excess Rs {tx.amount - available_headroom:,.2f} held in pending verification."
                )
                auth.cumulative_spent_authorized += available_headroom
                tx.containment_code = "HEADROOM_CAP"
                tx.authorized_amount = round(available_headroom, 2)
                tx.quarantined_amount = round(float(tx.amount) - available_headroom, 2)
                return tx, tx.containment_action
            tx.state = TransactionState.QUARANTINED
            tx.containment_action = (
                "CAPABILITY_CONTAINED: authority ceiling reached. Agent spend quarantined "
                "without user lockout."
            )
            tx.containment_code = "CAPABILITY_CONTAINED"
            tx.authorized_amount = 0.0
            tx.quarantined_amount = round(float(tx.amount), 2)
            return tx, tx.containment_action

        tx.state = TransactionState.QUARANTINED
        tx.containment_action = (
            "SHADOW_QUARANTINE: transaction routed to the decoy sandbox. The principal's "
            "instruments and remaining grant are unaffected."
        )
        tx.containment_code = "SHADOW_QUARANTINE"
        tx.authorized_amount = 0.0
        tx.quarantined_amount = round(float(tx.amount), 2)
        return tx, tx.containment_action

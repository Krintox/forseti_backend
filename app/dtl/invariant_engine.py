"""
The Delegation-Trust Ledger invariant engine.

FORSETI's claim is NOT "we add up the balances from three payment systems".
It is that a delegated agent may act only within the authority a human granted,
and that authority is MULTIDIMENSIONAL:

        USER AUTHORITY
              |
   +-----+----+----+------+--------+------+
   |     |         |      |        |      |
 AMOUNT PER_TX   RAIL  MERCHANT PURPOSE  TIME

An attacker does not have to exceed the money limit to act outside the grant.
Spending ₹12,000 of a ₹12,000 grocery budget on gift cards, on a rail the user
never authorised, or a day after the mandate lapsed are all authority
violations, and each is caught here by its own machine-checkable predicate.

Every invariant is deterministic arithmetic or set membership over the grant and
the transaction. Nothing here is learned, so none of it depends on having seen
the attack family before - that property is the reason this engine exists
alongside the ML detector rather than being replaced by it.

Evaluation order is deliberate and reflects how a real authorizer reasons:
an expired grant authorises nothing, then scope (rail, per-transaction size,
merchant category), then economic substance, then the aggregate ceiling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..models.proofs import SemanticDriftProof
from ..models.state import AuthorityDimension, DefensePolicy, DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction
from .sku_catalogue import classify_item

# Registry rendered by the UI and by docs. One row per dimension of authority.
INVARIANT_REGISTRY: List[Dict[str, str]] = [
    {
        # Not an authority DIMENSION but an authority STATE: the Blue team's
        # escalation ladder, enforced. Listed first because a suspended mandate
        # authorises nothing at any amount on any rail.
        "code": "INV_08_MANDATE_SUSPENDED",
        # NOT an authority dimension - a policy STATE. Tagged so the registry
        # keeps the "exactly one invariant per dimension" property for the
        # seven real dimensions while still surfacing this check in the UI.
        "kind": "policy_state",
        "dimension": AuthorityDimension.TIME.value,
        "question": "Is the mandate currently suspended by an active containment policy?",
        "expression": "ASSERT authority.active_policy != AGENT_SUSPENDED",
        "severity": "CRITICAL",
    },
    {
        "code": "INV_06_AUTHORITY_EXPIRED",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.TIME.value,
        "question": "Is the delegation still inside its validity window?",
        "expression": "ASSERT now <= delegation_created_at + validity_window_hours",
        "severity": "HIGH",
    },
    {
        "code": "INV_04_UNAUTHORIZED_RAIL",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.RAIL.value,
        "question": "Did the user authorise this payment rail at all?",
        "expression": "ASSERT tx.rail IN authority.permitted_rails",
        "severity": "HIGH",
    },
    {
        "code": "INV_05_PER_TX_CAP_EXCEEDED",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.PER_TX.value,
        "question": "Is this single transaction inside the per-transaction cap?",
        "expression": "ASSERT tx.amount <= authority.per_transaction_cap",
        "severity": "MEDIUM",
    },
    {
        "code": "INV_03_UNAUTHORIZED_MCC",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.MERCHANT.value,
        "question": "Is the merchant category inside the delegated scope?",
        "expression": "ASSERT tx.merchant_mcc IN authority.permitted_mccs",
        "severity": "HIGH",
    },
    {
        "code": "INV_07_UNAUTHORIZED_BENEFICIARY",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.BENEFICIARY.value,
        "question": "Is the settlement counterparty inside the delegated beneficiary scope?",
        "expression": "ASSERT tx.vpa_delegate IN authority.beneficiary_scope",
        "severity": "HIGH",
    },
    {
        "code": "INV_02_SEMANTIC_INTENT_DRIFT",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.PURPOSE.value,
        "question": "Does the basket match the authorised economic purpose?",
        "expression": "ASSERT cart.items.category NOT IN semantic_exclusions",
        "severity": "CRITICAL",
    },
    {
        "code": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "kind": "authority_dimension",
        "dimension": AuthorityDimension.AMOUNT.value,
        "question": "Does aggregate exposure across ALL rails stay inside the ceiling?",
        "expression": "ASSERT (settled + authorized + pending + reserved + new_tx) <= global_budget_ceiling",
        "severity": "HIGH",
    },
]


def _rail_value(tx: SyntheticTransaction) -> str:
    return str(getattr(tx.rail, "value", tx.rail))



def _scope_breach_magnitude(auth, tx) -> float:
    """
    Drift magnitude for a categorical (in/out) dimension.

    Rail, merchant and beneficiary scope are genuinely binary - the rail either
    is permitted or it is not - so a fabricated gradient would be dishonest.
    What DOES vary is how much authority the breach put at stake, so the score
    is a floor of 0.5 (it is unambiguously a breach) plus up to 0.5 more for
    the share of the grant involved. A Rs 50 out-of-scope payment and a Rs
    9,000 one are both violations; they are not equally consequential.
    """
    ceiling = max(1.0, float(auth.global_budget_ceiling))
    return round(min(1.0, 0.5 + 0.5 * (float(tx.amount) / ceiling)), 4)


class DTLInvariantEngine:
    """
    Evaluates the delegated-authority invariants over a transaction.

    `evaluate_invariants` keeps the historical (is_valid, first_proof) contract.
    `evaluate_all` returns every dimension that was violated, which is what the
    arena streams so a judge can see that one transaction can break the grant in
    more than one way at once.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------- public

    def evaluate_invariants(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[SemanticDriftProof]]:
        """Returns (is_valid, first_violated_proof) in registry order."""
        proofs = self.evaluate_all(auth, tx, now=now)
        if proofs:
            return False, proofs[0]
        return True, None

    def evaluate_all(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        now: Optional[datetime] = None,
    ) -> List[SemanticDriftProof]:
        """Every violated dimension, ordered as INVARIANT_REGISTRY."""
        checks = (
            self._check_agent_suspended,
            self._check_time,
            self._check_rail,
            self._check_per_tx_cap,
            self._check_mcc,
            self._check_beneficiary,
            self._check_semantic_purpose,
            self._check_global_budget,
        )
        proofs: List[SemanticDriftProof] = []
        for check in checks:
            proof = check(auth, tx, now)
            if proof is not None:
                proofs.append(proof)
        return proofs

    @staticmethod
    def registry() -> List[Dict[str, str]]:
        return [dict(row) for row in INVARIANT_REGISTRY]

    # ------------------------------------------------------------ helpers

    def _proof(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        *,
        code: str,
        dimension: str,
        severity: str,
        authorized: str,
        actual: str,
        drift_score: float,
        expression: str,
        explanation: str,
        violated_skus: Optional[List[str]] = None,
        prefix: str = "proof",
    ) -> SemanticDriftProof:
        return SemanticDriftProof(
            proof_id=f"{prefix}_{uuid.uuid4().hex[:8]}",
            authority_id=auth.authority_id,
            tx_id=tx.tx_id,
            invariant_code=code,
            authority_dimension=dimension,
            severity=severity,
            authorized_intent_summary=authorized,
            actual_cart_summary=actual,
            drift_score=drift_score,
            violated_skus=violated_skus if violated_skus is not None else [i.sku for i in tx.items],
            local_rail_statuses={_rail_value(tx): tx.local_rail_status},
            cumulative_spend_before=auth.total_exposure_global,
            attempted_amount=tx.amount,
            global_ceiling=auth.global_budget_ceiling,
            total_exposure_after=auth.total_exposure_global + tx.amount,
            invariant_expression=expression,
            explanation=explanation,
        )

    # ---------------------------------------------------------- invariants

    def _check_agent_suspended(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_08 - the active POLICY, enforced.

        This is the check whose absence the adversarial review named as its
        runner-up stage question: "show me the line where AGENT_SUSPENDED
        causes a transaction to be rejected." There was no such line. Blue's
        three-rung escalation ladder wrote `active_policy` and a set of knobs
        that no authorization path ever read, so an agent whose mandate was
        "suspended" transacted exactly as before and the ladder was a label.

        It is checked FIRST because a suspended mandate authorises nothing, at
        any amount, on any rail - the same logic that puts expiry first.
        """
        if not auth.policy_suspends_all_spend:
            return None
        return self._proof(
            auth, tx, prefix="proof_policy",
            code="INV_08_MANDATE_SUSPENDED",
            dimension=AuthorityDimension.TIME.value,
            severity="CRITICAL",
            authorized="Mandate suspended pending fresh re-consent from the principal",
            actual=f"Agent attempted ₹{tx.amount:,.2f} on {_rail_value(tx)} while suspended",
            drift_score=1.0,
            expression="ASSERT authority.active_policy != AGENT_SUSPENDED",
            explanation=(
                f"The delegation's active policy is AGENT_SUSPENDED, reached after repeated "
                f"containments of the same invariant this session. Soft containment was tried "
                f"and repeatedly bypassed by strategy changes, so the mandate is paused until "
                f"the principal re-consents. No amount is authorised while it holds - this is "
                f"the top rung of the escalation ladder, and it is enforced here rather than "
                f"displayed."
            ),
        )

    def _check_time(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """INV_06 - TIME. An elapsed mandate authorises nothing, at any amount."""
        ref = now or datetime.now(timezone.utc)
        if not auth.is_expired(ref):
            return None
        expired_at = auth.expires_at
        return self._proof(
            auth, tx, prefix="proof_expiry",
            code="INV_06_AUTHORITY_EXPIRED",
            dimension=AuthorityDimension.TIME.value,
            severity="HIGH",
            authorized=f"Valid for {auth.validity_window_hours:.0f}h from grant, until {expired_at.isoformat()}",
            actual=f"Transaction presented after the delegation lapsed",
            # Magnitude = how far past expiry, relative to the window itself.
            drift_score=round(min(1.0, max(0.15, (
                (ref - expired_at).total_seconds() / 3600.0
            ) / max(1.0, float(auth.validity_window_hours or 1.0)))), 4),
            expression="ASSERT now <= delegation_created_at + validity_window_hours",
            explanation=(
                f"The delegation expired at {expired_at.isoformat()}. The agent still holds a "
                f"syntactically valid token, and every rail would still honour it, but the human's "
                f"authority ended before this transaction. Amount is irrelevant here."
            ),
        )

    def _check_rail(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_04 - RAIL. The dimension a pure ceiling model cannot express.

        "₹12,000, UPI only" does NOT mean every rail may spend ₹12,000. Rail
        selection is itself part of the grant, so a card leg is a violation even
        when not one rupee of the ceiling has been used.
        """
        if auth.allows_rail(tx.rail):
            return None
        rail = _rail_value(tx)
        permitted = auth.permitted_rail_values
        return self._proof(
            auth, tx, prefix="proof_rail",
            code="INV_04_UNAUTHORIZED_RAIL",
            dimension=AuthorityDimension.RAIL.value,
            severity="HIGH",
            authorized=f"Rails permitted: {', '.join(permitted) if permitted else 'none'}",
            actual=f"Agent attempted ₹{tx.amount:,.2f} on {rail}",
            drift_score=_scope_breach_magnitude(auth, tx),
            expression="ASSERT tx.rail IN authority.permitted_rails",
            explanation=(
                f"The user delegated spend on {', '.join(permitted) or 'no rail'}, not on {rail}. "
                f"The {rail} adapter approves because its own limit and merchant checks pass - it has "
                f"no way to know the grant excluded it. Rail scope lives in the delegation, so only "
                f"the DTL can enforce it."
            ),
        )

    def _check_per_tx_cap(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_05 - PER_TX. A grant can bound single-transaction size independently
        of the total.

        SCOPE OF THE CLAIM. Per-transaction caps are NOT novel per-rail: UPI
        Circle mandates Rs 5,000 for a delegated secondary user, and the UPI
        adapter here enforces exactly that. What no scheme provides is the
        USER'S OWN bound applied across every rail at once - "nothing above
        Rs 3,000" has to hold on card and on the agentic rail too, and neither
        has any representation of the other legs. That cross-rail consistency
        is what this invariant adds, and it is the only thing it should be
        claimed to add.
        """
        # The EFFECTIVE cap: the tightest of the user's own bound and anything
        # the active policy imposes (step-up threshold, capability quarantine).
        # Reading `per_transaction_cap` directly was what made the Blue ladder
        # inert - the policy could tighten this and nothing would notice.
        cap = auth.effective_per_transaction_cap
        if cap is None or tx.amount <= cap:
            return None
        policy_imposed = (
            auth.per_transaction_cap is None or cap < auth.per_transaction_cap
        )
        return self._proof(
            auth, tx, prefix="proof_pertx",
            code="INV_05_PER_TX_CAP_EXCEEDED",
            dimension=AuthorityDimension.PER_TX.value,
            severity="MEDIUM",
            authorized=(
                f"No single transaction above ₹{cap:,.2f}"
                + (f" (tightened from ₹{auth.per_transaction_cap:,.2f} by active policy "
                   f"{getattr(auth.active_policy, 'value', auth.active_policy)})"
                   if policy_imposed and auth.per_transaction_cap is not None
                   else f" (imposed by active policy "
                        f"{getattr(auth.active_policy, 'value', auth.active_policy)})"
                   if policy_imposed else "")
            ),
            actual=f"Single transaction of ₹{tx.amount:,.2f} on {_rail_value(tx)}",
            # Magnitude = how far over the cap, relative to the cap.
            drift_score=round(min(1.0, (float(tx.amount) - cap) / max(1.0, cap)), 4),
            expression="ASSERT tx.amount <= authority.per_transaction_cap",
            explanation=(
                f"₹{tx.amount:,.2f} exceeds the ₹{cap:,.2f} per-transaction cap by "
                f"₹{tx.amount - cap:,.2f}. The aggregate budget may be untouched - this dimension "
                f"bounds how much authority one action may consume at once, which is what limits "
                f"the blast radius of a single compromised instruction."
            ),
        )

    def _check_mcc(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_03 - MERCHANT. Previously documented but never enforced at runtime;
        the offline probe in detector/baselines.py already scored it, so the
        engine and the measurement now agree.
        """
        if not auth.permitted_mccs or tx.merchant_mcc in auth.permitted_mccs:
            return None
        return self._proof(
            auth, tx, prefix="proof_mcc",
            code="INV_03_UNAUTHORIZED_MCC",
            dimension=AuthorityDimension.MERCHANT.value,
            severity="HIGH",
            authorized=f"Merchant categories in scope: {', '.join(auth.permitted_mccs)}",
            actual=f"{tx.merchant_name} presented MCC {tx.merchant_mcc}",
            drift_score=_scope_breach_magnitude(auth, tx),
            expression="ASSERT tx.merchant_mcc IN authority.permitted_mccs",
            explanation=(
                f"MCC {tx.merchant_mcc} ({tx.merchant_name}) is outside the delegated merchant "
                f"scope {auth.permitted_mccs}. The rail approves because the card and the merchant "
                f"are both valid; only the delegation knows which categories the human meant."
            ),
        )

    def _check_beneficiary(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_07 - BENEFICIARY. A grant can name WHO the money may settle to, not
        just how much, on what rail, and at what merchant category. "Pay the
        electricity board" does not authorise the same amount landing at a
        different VPA that merely also carries an in-scope MCC - the rail and
        the merchant look identical to a legitimate payment.
        """
        if auth.allows_beneficiary(tx.vpa_delegate):
            return None
        scope = list(auth.beneficiary_scope)
        return self._proof(
            auth, tx, prefix="proof_beneficiary",
            code="INV_07_UNAUTHORIZED_BENEFICIARY",
            dimension=AuthorityDimension.BENEFICIARY.value,
            severity="HIGH",
            authorized=f"Beneficiary restricted to: {', '.join(scope) if scope else 'none'}",
            actual=f"Settlement routed to {tx.vpa_delegate or 'an unrecorded beneficiary'}",
            drift_score=_scope_breach_magnitude(auth, tx),
            expression="ASSERT tx.vpa_delegate IN authority.beneficiary_scope",
            explanation=(
                f"The delegation restricts settlement to {', '.join(scope) or 'no beneficiary'}, but "
                f"this transaction settles to {tx.vpa_delegate or 'an unrecorded counterparty'}. Rail, "
                f"amount and merchant category can all be legitimate while the money still ends up "
                f"somewhere the human never authorised - the rail has no concept of 'the same "
                f"counterparty the user meant'."
            ),
        )

    def _check_semantic_purpose(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """
        INV_02 - PURPOSE. The merchant is legitimate, the MCC is in scope, the
        amount fits - and the basket still converts the grant into liquid value.

        TRUST DIRECTION. This check reasons over an ATTESTED catalogue
        (`sku_catalogue.py`), not over the merchant's free-text category and
        `is_stored_value` flag. Those are supplied by the party being defended
        against, and a keyword list over them was trivially evadable: renaming
        a gift card to "Prepaid Value Instrument" and clearing the flag made
        this invariant silent. The SKU's attested nature decides; a merchant
        mislabelling an attested liquid instrument is now itself recorded as
        evidence rather than believed.
        """
        violated_skus: List[str] = []
        misdeclared: List[str] = []
        unattested: List[str] = []
        bases: List[str] = []

        for item in tx.items:
            verdict = classify_item(item.sku, item.category, item.is_stored_value)
            if not verdict["attested"]:
                unattested.append(item.sku)

            excluded_by_grant = str(verdict["category"]).upper() in {
                ex.upper() for ex in auth.semantic_exclusions
            }
            if verdict["is_liquid"] or excluded_by_grant:
                violated_skus.append(item.sku)
                bases.append(f"{item.sku}: {verdict['basis']}")
                if verdict["misdeclared"]:
                    misdeclared.append(item.sku)

        # Under STRICT_CATALOG_ATTESTATION the operator has said unverifiable
        # line items are not acceptable, so an unattested SKU is a violation in
        # its own right. This is what makes that policy load-bearing instead of
        # a label - see feedback/policy_adapter.py.
        strict = auth.active_policy == DefensePolicy.STRICT_CATALOG_ATTESTATION
        if strict and unattested:
            for sku in unattested:
                if sku not in violated_skus:
                    violated_skus.append(sku)
                    bases.append(f"{sku}: UNATTESTED under STRICT_CATALOG_ATTESTATION")

        if not violated_skus:
            return None

        detail = ""
        if misdeclared:
            detail = (
                f" The merchant declared {', '.join(misdeclared)} as ordinary merchandise while the "
                f"catalogue attests it is liquid value - a misdeclaration, not a disagreement."
            )
        elif unattested and strict:
            detail = (
                f" {', '.join(unattested)} carry no independent attestation, and the active policy "
                f"is STRICT_CATALOG_ATTESTATION."
            )

        return self._proof(
            auth, tx, prefix="proof_drift",
            code="INV_02_SEMANTIC_INTENT_DRIFT",
            dimension=AuthorityDimension.PURPOSE.value,
            severity="CRITICAL",
            authorized=f"{auth.economic_purpose} (budget ₹{auth.global_budget_ceiling:,.2f})",
            actual=(f"Basket contains attested liquid-value instruments ({', '.join(violated_skus)}) "
                    f"under MCC {tx.merchant_mcc}"),
            # Magnitude = the SHARE of cart value that is outside the purpose.
            # A Rs 300 voucher inside a Rs 9,000 grocery run is real drift, and
            # meaningfully less drift than a near-total conversion.
            drift_score=round(min(1.0, max(0.1, sum(
                i.unit_price * i.quantity for i in tx.items if i.sku in violated_skus
            ) / max(1.0, float(tx.amount)))), 4),
            expression="ASSERT attested_category(sku) NOT IN semantic_exclusions",
            explanation=(
                f"Transaction passes the local rail MCC check ({tx.merchant_mcc}) but converts the "
                f"authorised budget into liquid value ({', '.join(violated_skus)}). Amount, rail and "
                f"merchant category are all within the grant; the economic substance of the basket "
                f"is not. Decided on independent attestation rather than the merchant's own label - "
                f"{'; '.join(bases)}.{detail}"
            ),
            violated_skus=violated_skus,
        )

    def _check_global_budget(self, auth, tx, now) -> Optional[SemanticDriftProof]:
        """INV_01 - AMOUNT. The cross-rail aggregate no single rail can compute."""
        # Evaluated against the EFFECTIVE ceiling, so TIGHTENED_HEADROOM_V2
        # actually withholds headroom instead of only renaming the policy.
        new_exposure = auth.total_exposure_global + tx.amount
        effective = auth.effective_ceiling
        if new_exposure <= effective:
            return None
        withheld = auth.global_budget_ceiling - effective
        return self._proof(
            auth, tx, prefix="proof_budget",
            code="INV_01_GLOBAL_BUDGET_EXCEEDED",
            dimension=AuthorityDimension.AMOUNT.value,
            severity="HIGH",
            authorized=(
                f"Total aggregate spend <= ₹{effective:,.2f}"
                + (f" (₹{withheld:,.2f} of the ₹{auth.global_budget_ceiling:,.2f} grant withheld "
                   f"by active policy {getattr(auth.active_policy, 'value', auth.active_policy)})"
                   if withheld > 0 else "")
            ),
            actual=f"Cross-rail split attempt pushing total exposure to ₹{new_exposure:,.2f}",
            # Magnitude = the overshoot, relative to the grant.
            drift_score=round(min(1.0, max(0.05, (new_exposure - effective) / max(1.0, effective))), 4),
            expression="ASSERT (cumulative_settled + authorized + pending + reserved + new_tx) <= global_budget_ceiling",
            explanation=(
                f"Individually valid on {_rail_value(tx)} (₹{tx.amount:,.2f} sits inside that rail's "
                f"own limit), but aggregate exposure across every rail reaches ₹{new_exposure:,.2f} "
                f"against a ₹{auth.global_budget_ceiling:,.2f} grant - over by "
                f"₹{new_exposure - auth.global_budget_ceiling:,.2f}."
            ),
        )

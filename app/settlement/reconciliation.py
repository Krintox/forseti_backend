"""
Settlement Reconciliation Engine (Kill Chain stages 10-11).

Everything in dtl/invariant_engine.py answers "was this ONE transaction
authorised, evaluated against the grant as it stood at that moment?" That
question is well-posed for a single transaction and blind by construction to
what happens AFTER authorization: two legs can each be individually
authorised, locally valid, and pass every one of the seven authority-dimension
invariants, and the post-authorization lifecycle can still disagree about
what actually happened to the money.

This module is a THIRD parallel concern, alongside DTL invariants (authority
dimensions) and Deception Lab (agent reasoning integrity):

    DTL invariants   -> was this transaction inside the grant at auth time?
    Deception Lab    -> was the agent's own reasoning fed a false premise?
    Reconciliation   -> do the post-authorization books agree with each other?

Two distinct failure modes are modelled, matching Kill Chain stages 10 and 11:

    SETTLEMENT_CONFLICT   - the SAME obligation is captured on one rail and
                             refunded on a DIFFERENT rail. A refund must trace
                             back to the instrument that captured the funds;
                             one that does not is a cross-rail conflict, not a
                             legitimate reversal.
    RECONCILIATION_DRIFT  - the SAME obligation is captured more than once on
                             the SAME rail (a duplicated/replayed settlement
                             message). The reconciled total no longer matches
                             the single settlement the delegation authorised.

Both are deterministic set/count checks over `obligation_id`-linked legs,
exactly like an invariant - nothing here is learned or estimated.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from ..models.proofs import SettlementReconciliationProof
from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction

_CAPTURE_ACTIONS = ("CAPTURE", "DUPLICATE_CAPTURE")


def _rail(tx: SyntheticTransaction) -> str:
    return str(getattr(tx.rail, "value", tx.rail))


def _group_by_obligation(txs: List[SyntheticTransaction]) -> Dict[str, List[SyntheticTransaction]]:
    groups: Dict[str, List[SyntheticTransaction]] = {}
    for tx in txs:
        if not tx.obligation_id:
            continue
        groups.setdefault(tx.obligation_id, []).append(tx)
    return groups


def detect_settlement_conflict(
    auth: DTLGlobalAuthorityState, txs: List[SyntheticTransaction]
) -> Optional[SettlementReconciliationProof]:
    """
    Kill Chain stage 10. Fires when one leg of an obligation is CAPTURED on
    one rail while another leg of the SAME obligation is REFUNDED on a
    DIFFERENT rail - two locally valid instructions producing an
    economically inconsistent final state for one authorised obligation.
    """
    for obligation_id, legs in _group_by_obligation(txs).items():
        captures = [t for t in legs if t.settlement_action in _CAPTURE_ACTIONS]
        refunds = [t for t in legs if t.settlement_action == "REFUND"]
        if not captures or not refunds:
            continue
        capture = captures[0]
        conflicting_refund = next((r for r in refunds if _rail(r) != _rail(capture)), None)
        if conflicting_refund is None:
            continue

        return SettlementReconciliationProof(
            proof_id=f"proof_settlement_{uuid.uuid4().hex[:8]}",
            authority_id=auth.authority_id,
            obligation_id=obligation_id,
            conflict_code="RECON_01_SETTLEMENT_CONFLICT",
            kill_chain_stage="SETTLEMENT_CONFLICT",
            severity="HIGH",
            leg_tx_ids=[capture.tx_id, conflicting_refund.tx_id],
            leg_summary=(
                f"{_rail(capture)} reports CAPTURED Rs {capture.amount:,.2f}; "
                f"{_rail(conflicting_refund)} reports REFUNDED Rs {conflicting_refund.amount:,.2f} "
                f"for the same obligation ({obligation_id})."
            ),
            canonical_expectation=(
                "A refund must trace back to the same rail/instrument that captured the funds; "
                "one obligation cannot be simultaneously settled and reversed across two "
                "uncoordinated rails."
            ),
            observed_mismatch=(
                f"{_rail(capture)}={capture.settlement_action} vs "
                f"{_rail(conflicting_refund)}={conflicting_refund.settlement_action} for "
                f"obligation {obligation_id}."
            ),
            economic_exposure_at_risk=round(capture.amount, 2),
            explanation=(
                f"Rail {_rail(capture)} and rail {_rail(conflicting_refund)} each independently "
                f"processed a locally valid settlement instruction for obligation {obligation_id}. "
                f"Neither rail can see the other's ledger, so neither one alone can detect the "
                f"conflict - only a canonical view across both settlement legs can. Every "
                f"authority-dimension invariant was satisfied at authorization time for both legs; "
                f"this is a post-authorization lifecycle failure, not a delegated-authority "
                f"violation."
            ),
        )
    return None


def detect_reconciliation_drift(
    auth: DTLGlobalAuthorityState, txs: List[SyntheticTransaction]
) -> Optional[SettlementReconciliationProof]:
    """
    Kill Chain stage 11. Fires when the SAME obligation is captured more than
    once on the SAME rail - a duplicated or replayed settlement message that
    inflates the reconciled total beyond the single obligation the delegation
    actually authorised.
    """
    for obligation_id, legs in _group_by_obligation(txs).items():
        captures = [t for t in legs if t.settlement_action in _CAPTURE_ACTIONS]
        if len(captures) < 2:
            continue

        by_rail: Dict[str, List[SyntheticTransaction]] = {}
        for t in captures:
            by_rail.setdefault(_rail(t), []).append(t)
        duplicated_rail = next((r for r, ts in by_rail.items() if len(ts) >= 2), None)
        if duplicated_rail is None:
            continue

        dup_legs = sorted(by_rail[duplicated_rail], key=lambda t: t.tx_id)
        first, excess = dup_legs[0], dup_legs[1:]
        excess_total = sum(t.amount for t in excess)
        reconciled_total = sum(t.amount for t in dup_legs)

        return SettlementReconciliationProof(
            proof_id=f"proof_reconciliation_{uuid.uuid4().hex[:8]}",
            authority_id=auth.authority_id,
            obligation_id=obligation_id,
            conflict_code="RECON_02_RECONCILIATION_DRIFT",
            kill_chain_stage="RECONCILIATION_DRIFT",
            severity="HIGH",
            leg_tx_ids=[t.tx_id for t in dup_legs],
            leg_summary=(
                f"{_rail(first)} reports {len(dup_legs)} settlement applications "
                f"(Rs {reconciled_total:,.2f} total) for a single obligation ({obligation_id})."
            ),
            canonical_expectation=(
                f"One authorised obligation should reconcile to exactly one settlement of "
                f"Rs {first.amount:,.2f}."
            ),
            observed_mismatch=(
                f"Rs {reconciled_total:,.2f} reconciled on {_rail(first)} against an authorised "
                f"obligation of Rs {first.amount:,.2f} - a duplicate/replayed settlement message "
                f"added Rs {excess_total:,.2f} that was never authorised as a second transaction."
            ),
            economic_exposure_at_risk=round(excess_total, 2),
            explanation=(
                f"The rail's local ledger applied {len(dup_legs)} settlement events for one "
                f"obligation ({obligation_id}) instead of one. Each individual settlement message "
                f"was locally well-formed - the failure only becomes visible when the obligation's "
                f"own reconciliation total is checked against what the delegation actually "
                f"authorised, which no single rail-local view does."
            ),
        )
    return None


def evaluate_all(
    auth: DTLGlobalAuthorityState, txs: List[SyntheticTransaction]
) -> List[SettlementReconciliationProof]:
    """Runs both reconciliation checks; a round can trip at most one of each."""
    proofs: List[SettlementReconciliationProof] = []
    for detector in (detect_settlement_conflict, detect_reconciliation_drift):
        result = detector(auth, txs)
        if result is not None:
            proofs.append(result)
    return proofs


def apply_settlement_containment(proof: SettlementReconciliationProof) -> str:
    """
    Proportionate response, mirroring dtl/cost_governor.py's style: the
    smallest action that resolves the inconsistency without touching
    authority the obligation never even implicated.
    """
    if proof.conflict_code == "RECON_01_SETTLEMENT_CONFLICT":
        return (
            f"SETTLEMENT_HOLD: obligation {proof.obligation_id} frozen pending manual "
            f"reconciliation - the conflicting cross-rail leg is held, the original capture is "
            f"not disturbed, and Rs {proof.economic_exposure_at_risk:,.2f} is quarantined from "
            f"further movement until the two rails' ledgers are reconciled."
        )
    return (
        f"DUPLICATE_SETTLEMENT_REVERSED: obligation {proof.obligation_id}'s excess settlement "
        f"application(s) are reversed; Rs {proof.economic_exposure_at_risk:,.2f} of "
        f"over-recognised exposure is removed and the reconciled total is restored to the single "
        f"authorised obligation amount."
    )

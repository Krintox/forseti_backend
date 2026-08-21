"""
Deterministic detectors, one per Deception Lab attack primitive.

Each detector takes the live authority grant and a transaction, and returns a
DeceptionProof only if that specific attack primitive is present. Detection is
defense-in-depth observability, not the security boundary itself: the DTL
invariant engine and the Intent Firewall never read `injected_payload`,
`tool_reported_category`, or `claimed_prior_authorization` for any
authorization decision, so none of these fields could change an outcome even
undetected. That property - not merely "we spotted it" - is what these tests
in tests/test_deception_lab.py pin.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from ..models.proofs import DeceptionProof
from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction

# Deliberately small and literal: these are the phrase shapes a compromised
# merchant/tool result would use to try to talk the AGENT'S OWN reasoning
# into acting outside its authority. The list exists for detection/alerting
# only - see module docstring for why it is not a security boundary.
_INJECTION_PATTERNS = [
    re.compile(r"system\s*(override|message|prompt)", re.IGNORECASE),
    re.compile(r"ignore\s+(the\s+)?(user'?s?|previous|prior)\s+.*(instruction|restriction|limit)", re.IGNORECASE),
    re.compile(r"authoriz(e|ed|ation)\s+(increase|override|bypass)", re.IGNORECASE),
    re.compile(r"approve\s+immediately", re.IGNORECASE),
]


def _proof(tx_id: str, *, deception_type: str, severity: str,
           deceptive_input: str, ground_truth_check: str, explanation: str) -> DeceptionProof:
    return DeceptionProof(
        proof_id=f"proof_deception_{uuid.uuid4().hex[:8]}",
        tx_id=tx_id,
        deception_type=deception_type,
        severity=severity,
        deceptive_input=deceptive_input,
        ground_truth_check=ground_truth_check,
        explanation=explanation,
    )


def detect_prompt_injection(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Optional[DeceptionProof]:
    """Attack 1: a merchant/tool result tries to talk the agent past its authority."""
    payload = tx.injected_payload
    if not payload:
        return None
    if not any(p.search(payload) for p in _INJECTION_PATTERNS):
        return None
    return _proof(
        tx.tx_id,
        deception_type="PROMPT_INJECTION",
        severity="HIGH",
        deceptive_input=payload,
        ground_truth_check=(
            f"DTL evaluated tx_id={tx.tx_id} against the signed grant using only "
            f"amount/rail/mcc/items - free text from a merchant is never parsed as an "
            f"instruction anywhere in the authorization path."
        ),
        explanation=(
            "The merchant response contains an instruction-shaped override attempt. "
            "Detected here for alerting; the outcome does not depend on detection - no "
            "invariant, firewall check, or cost-governor decision anywhere in the stack "
            "reads this field."
        ),
    )


def detect_tool_output_poisoning(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Optional[DeceptionProof]:
    """Attack 2: a search/catalogue tool misreports what the cart actually contains."""
    reported = tx.tool_reported_category
    if not reported:
        return None
    ground_truth_flagged = [
        item.sku for item in tx.items
        if item.is_stored_value or item.category.upper() != reported.upper()
    ]
    if not ground_truth_flagged:
        return None
    return _proof(
        tx.tx_id,
        deception_type="TOOL_OUTPUT_POISONING",
        severity="HIGH",
        deceptive_input=f"Tool reported category='{reported}' for this cart",
        ground_truth_check=(
            f"Raw CartItem records show {ground_truth_flagged} do not match '{reported}' "
            f"(or are marked is_stored_value=true) - the DTL semantic-drift check reads "
            f"these SKU-level fields directly, never the tool's summary category."
        ),
        explanation=(
            f"The agent's product-search tool reported this cart as '{reported}', but the "
            f"underlying line items say otherwise. An agent reasoning only from the tool's "
            f"summary would approve; the ground-truth cart data contradicts it."
        ),
    )


def detect_context_poisoning(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Optional[DeceptionProof]:
    """Attack 3: the agent's memory/context claims a stale, more permissive authorization."""
    claim = tx.claimed_prior_authorization
    if not claim:
        return None
    claimed_ceiling = claim.get("ceiling")
    if claimed_ceiling is None or claimed_ceiling <= auth.global_budget_ceiling:
        return None
    return _proof(
        tx.tx_id,
        deception_type="CONTEXT_MEMORY_POISONING",
        severity="CRITICAL",
        deceptive_input=(
            f"Agent context claims: '{claim.get('note', 'user previously approved a higher limit')}' "
            f"(claimed ceiling Rs {claimed_ceiling:,.2f})"
        ),
        ground_truth_check=(
            f"Live signed DTL grant states Rs {auth.global_budget_ceiling:,.2f} "
            f"(authority_id={auth.authority_id}). The claimed prior approval is not present in "
            f"the ledger and is never consulted - only the current signed grant is."
        ),
        explanation=(
            "The agent's own context/memory asserts a higher limit than the delegation the "
            "DTL actually holds. Token and delegation validity are different clocks; a stale "
            "or fabricated memory of approval cannot substitute for the live grant, which is "
            "the only thing any invariant is evaluated against."
        ),
    )


def detect_self_approval(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Optional[DeceptionProof]:
    """Attack 4: a subordinate/compromised agent approves its own escalation."""
    if not tx.self_approved:
        return None
    if tx.approving_agent_id is None or tx.approving_agent_id != tx.agent_id:
        return None
    return _proof(
        tx.tx_id,
        deception_type="AUTHORITY_IMPERSONATION",
        severity="CRITICAL",
        deceptive_input=(
            f"agent_id={tx.agent_id} recorded itself as approving_agent_id for its own escalation"
        ),
        ground_truth_check=(
            "Delegation-issuing authority and the agent executing a transaction are required "
            "to be distinct principals; an agent cannot be its own approver of record."
        ),
        explanation=(
            f"Agent '{tx.agent_id}' is both the initiator AND the recorded approver of this "
            f"transaction's authority escalation - there is no separate human or higher-trust "
            f"principal in the loop, which is the signature of a compromised or over-privileged "
            f"sub-agent minting its own authority."
        ),
    )


_DETECTORS = (
    detect_prompt_injection,
    detect_tool_output_poisoning,
    detect_context_poisoning,
    detect_self_approval,
)


def evaluate_all(auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> List[DeceptionProof]:
    """Runs every detector; a single transaction can trip more than one."""
    proofs: List[DeceptionProof] = []
    for detector in _DETECTORS:
        result = detector(auth, tx)
        if result is not None:
            proofs.append(result)
    return proofs

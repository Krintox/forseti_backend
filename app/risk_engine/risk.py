"""
Composite risk vector computation.

Every component is read from data the round already produced; nothing is
re-simulated or estimated. The overall score is an EQUAL-WEIGHTED mean,
deliberately - there is no labelled dataset of "true" incident severity to
fit weights against, so presenting a tuned-looking weighted formula would
overstate the rigor behind it. This mirrors the honesty distinction the rest
of the project draws between measured and illustrative numbers.
"""

from __future__ import annotations

from typing import Any, Dict


def compute_unified_risk(round_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Synthesises: DTL invariant outcome, Intent Firewall drift, Deception Lab
    detection, ML detector probability, and kill-chain attack_chain_score -
    all already computed elsewhere in this round. `deterministic_override` is
    always True when the DTL actually contained the round, making explicit
    that THIS SCORE NEVER DECIDED THE OUTCOME - the invariant did, before this
    composite was even computed.
    """
    kill_chain = round_result.get("kill_chain") or {}
    firewall = round_result.get("firewall_verdicts") or []
    deception = round_result.get("deception_verdicts") or []
    settlement = round_result.get("settlement_verdict") or {}
    chain_violations = round_result.get("chain_violations") or []
    steps = round_result.get("step_results") or []

    # ------------------------------------------------------------ components
    #
    # REBUILT FOR INDEPENDENCE. The previous five components were not
    # independent: `dtl_invariant_risk` was `1.0 if detected else 0.0`;
    # `kill_chain_risk` was `attack_chain_score`, itself ~70% that same
    # boolean; and `intent_firewall_risk` was the max of hardcoded per-invariant
    # constants, non-zero exactly when `detected` was true. Three of five terms
    # traced back to ONE boolean, so averaging them was a weighted vote in
    # which one voter held three ballots.
    #
    # Each term below now measures something the others do not, and each is a
    # MAGNITUDE rather than a restatement of the outcome.

    # 1. How much of the attempted objective breached authority (magnitude, not
    #    "did anything fire").
    attempted = sum(float((s.get("tx") or {}).get("amount", 0.0) or 0.0) for s in steps)
    prevented = float(kill_chain.get("economic_exposure_prevented_inr", 0.0) or 0.0)
    authority_breach_severity = min(1.0, prevented / attempted) if attempted > 0 else 0.0

    # 2. WHICH dimensions drifted, and how far - now a real magnitude because
    #    drift_score is computed from breach size rather than a constant.
    drift_scores = [float(v.get("overall_drift_score", 0.0)) for v in firewall]
    intent_drift_severity = max(drift_scores) if drift_scores else 0.0

    # 3. Agent-integrity: was the agent fed a false premise. Independent of
    #    whether the resulting action was inside the grant.
    deception_lab_risk = 1.0 if any(
        v.get("verdict") == "DECEPTION_DETECTED" for v in deception
    ) else 0.0

    # 4. The only learned signal, and genuinely independent of the rest.
    ml_probs = [
        float(s["ml_probability"]) for s in steps
        if isinstance(s.get("ml_probability"), (int, float))
    ]
    ml_anomaly_risk = max(ml_probs) if ml_probs else 0.0

    # 5. Post-authorization / delegation-structure integrity. Neither is
    #    implied by any authority-dimension outcome, which is exactly why they
    #    are separate mechanisms.
    structural_integrity_risk = 0.0
    if settlement.get("verdict") == "CONFLICT_DETECTED":
        structural_integrity_risk = 1.0
    elif chain_violations:
        structural_integrity_risk = 0.85

    components = {
        "authority_breach_severity": round(authority_breach_severity, 4),
        "intent_drift_severity": round(intent_drift_severity, 4),
        "deception_lab_risk": round(deception_lab_risk, 4),
        "ml_anomaly_risk": round(ml_anomaly_risk, 4),
        "structural_integrity_risk": round(structural_integrity_risk, 4),
    }
    overall = sum(components.values()) / len(components)

    return {
        "overall_risk_score": round(overall, 4),
        "confidence": round(len(ml_probs) / max(1, len(steps)), 4) if steps else 0.0,
        "risk_components": components,
        "deterministic_override": bool(round_result.get("detected")),
        "weighting": "equal-weighted mean - no labelled severity dataset exists to fit weights against",
        "component_independence": (
            "Each component measures something the others do not: economic magnitude of the "
            "breach, which authority dimensions drifted and how far, whether the agent's own "
            "reasoning was deceived, the learned anomaly score, and post-authorization / "
            "delegation-structure integrity. An earlier revision averaged five terms of which "
            "three were near-deterministic functions of a single `detected` boolean, which made "
            "the mean a weighted vote with one voter holding three ballots."
        ),
        "note": (
            "A synthesis of signals this round already produced, not a new detector. "
            "The DTL invariant decided the outcome before this score was computed."
        ),
    }

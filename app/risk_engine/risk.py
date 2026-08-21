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
    steps = round_result.get("step_results") or []

    dtl_invariant_risk = 1.0 if round_result.get("detected") else 0.0

    drift_scores = [float(v.get("overall_drift_score", 0.0)) for v in firewall]
    intent_firewall_risk = max(drift_scores) if drift_scores else 0.0

    deception_lab_risk = 1.0 if any(v.get("verdict") == "DECEPTION_DETECTED" for v in deception) else 0.0

    ml_probs = [
        float(s["ml_probability"]) for s in steps
        if isinstance(s.get("ml_probability"), (int, float))
    ]
    ml_anomaly_risk = max(ml_probs) if ml_probs else 0.0

    kill_chain_risk = float(kill_chain.get("attack_chain_score", 0.0) or 0.0)

    components = {
        "dtl_invariant_risk": round(dtl_invariant_risk, 4),
        "intent_firewall_risk": round(intent_firewall_risk, 4),
        "deception_lab_risk": round(deception_lab_risk, 4),
        "ml_anomaly_risk": round(ml_anomaly_risk, 4),
        "kill_chain_risk": round(kill_chain_risk, 4),
    }
    overall = sum(components.values()) / len(components)

    return {
        "overall_risk_score": round(overall, 4),
        "confidence": round(len(ml_probs) / max(1, len(steps)), 4) if steps else 0.0,
        "risk_components": components,
        "deterministic_override": bool(round_result.get("detected")),
        "weighting": "equal-weighted mean - no labelled severity dataset exists to fit weights against",
        "note": (
            "A synthesis of signals this round already produced, not a new detector. "
            "The DTL invariant decided the outcome before this score was computed."
        ),
    }

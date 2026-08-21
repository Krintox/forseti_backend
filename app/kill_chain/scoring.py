"""
Per-round and per-session kill-chain scoring.

Every number here is read from data the round already produced (the event
log's `offset_ms` timestamps, the INVARIANT_VIOLATION proof's own `overshoot`
field, `step_results`' recorded rails) - nothing is re-simulated or
estimated. `blast_radius_score` and `attack_chain_score` ARE heuristic
composites (there is no external ground truth for either), and are labelled
as such below rather than presented as a measured quantity - the same
honesty-policy distinction the rest of the project draws between "measured"
and "illustrative."
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .stages import KILL_CHAIN_STAGES, stage_for_strategy

_TOTAL_RAILS = 3  # CARD_TOKEN, UPI_CIRCLE, AGENTIC_AP2


def _first_offset(events: List[Dict[str, Any]], event_type: str, predicate=None) -> Optional[float]:
    for e in events:
        if e.get("event_type") != event_type:
            continue
        if predicate is not None and not predicate(e):
            continue
        return e.get("offset_ms")
    return None


def score_round(round_summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Kill-chain metrics for one completed round. Returns a dict even when the
    strategy has no stage mapping yet (`stage` is then None) - callers should
    not assume every round is scoreable into the 11-stage taxonomy.
    """
    strategy = round_summary.get("strategy", "")
    stage = stage_for_strategy(strategy)
    step_results = round_summary.get("step_results", [])

    # round_summary["events"] is the RECORDER'S CUMULATIVE timeline (it is
    # only cleared on an explicit orchestrator.reset()), so running several
    # rounds back to back means it holds every earlier round's events too.
    # Without filtering to this round's own round_id, the offset lookups
    # below would find the FIRST round's ATTACK_STARTED, not this one's.
    round_number = round_summary.get("round_number")
    events = [e for e in round_summary.get("events", []) if e.get("round_id") == round_number]

    detected = bool(round_summary.get("detected", False))
    contained = round_summary.get("winner") == "BLUE"

    started_at = _first_offset(events, "ATTACK_STARTED")
    detected_at = _first_offset(events, "INVARIANT_VIOLATION")
    if detected_at is None:
        detected_at = _first_offset(
            events, "DECEPTION_LAB_VERDICT",
            predicate=lambda e: e.get("payload", {}).get("verdict") == "DECEPTION_DETECTED",
        )
    time_to_detection_ms = (
        round(detected_at - started_at, 3)
        if started_at is not None and detected_at is not None and detected_at >= started_at
        else None
    )

    economic_exposure_prevented_inr = round(sum(
        float(e.get("payload", {}).get("overshoot", 0.0) or 0.0)
        for e in events if e.get("event_type") == "INVARIANT_VIOLATION"
    ), 2)

    rails_touched = {
        (sr.get("tx", {}) or {}).get("rail")
        for sr in step_results
        if (sr.get("tx", {}) or {}).get("rail")
    }
    # Heuristic, not measured: fraction of the 3 rails this round's objective
    # spread across. A single-rail attack scores low blast radius even if its
    # rupee amount is large; that is the intended meaning of "blast radius"
    # here - lateral spread, not size.
    blast_radius_score = round(min(1.0, len(rails_touched) / _TOTAL_RAILS), 3)

    # Heuristic composite, not a measured quantity: rewards containment,
    # rewards catching it fast (relative to a 5s round budget), and rewards
    # having prevented nonzero exposure. Deliberately simple and deterministic
    # so it's reproducible, not a tuned score.
    speed_component = 0.0
    if time_to_detection_ms is not None:
        speed_component = max(0.0, 1.0 - min(1.0, time_to_detection_ms / 5000.0))
    attack_chain_score = round(
        0.5 * (1.0 if contained else 0.0)
        + 0.3 * speed_component
        + 0.2 * (1.0 if economic_exposure_prevented_inr > 0 else 0.0),
        3,
    )

    return {
        "strategy": strategy,
        "stage": stage,
        "detected": detected,
        "contained": contained,
        "time_to_detection_ms": time_to_detection_ms,
        "economic_exposure_prevented_inr": economic_exposure_prevented_inr,
        "blast_radius_score": blast_radius_score,
        "attack_chain_score": attack_chain_score,
        "rails_touched": sorted(rails_touched),
    }


def coverage(round_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Session-level rollup: which of the 11 stages has this session actually
    exercised, and of those, how many were contained. A round with no stage
    mapping is counted in `unmapped_rounds` and excluded from stage coverage.
    """
    total_stages = len(KILL_CHAIN_STAGES)
    reached: Dict[str, Dict[str, Any]] = {}
    unmapped_rounds = 0

    for score in round_scores:
        stage = score.get("stage")
        if not stage:
            unmapped_rounds += 1
            continue
        code = stage["code"]
        entry = reached.setdefault(code, {
            "code": code, "label": stage["label"], "attempts": 0, "contained": 0,
        })
        entry["attempts"] += 1
        if score.get("contained"):
            entry["contained"] += 1

    stages_reached = len(reached)
    stages_contained = sum(1 for s in reached.values() if s["contained"] > 0)

    return {
        "total_stages": total_stages,
        "stages_reached": stages_reached,
        "stages_contained": stages_contained,
        "coverage_pct": round(100.0 * stages_reached / total_stages, 1),
        "containment_pct_of_reached": round(100.0 * stages_contained / stages_reached, 1) if stages_reached else 0.0,
        "by_stage": sorted(reached.values(), key=lambda s: s["code"]),
        "unmapped_rounds": unmapped_rounds,
    }

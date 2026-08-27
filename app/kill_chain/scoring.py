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
    # CONTAINMENT is read from the outcome, not from `winner == "BLUE"`.
    #
    # Blue now also wins rounds it DETECTED without containing - a deception
    # flagged on a transaction that breached no dimension of the grant. Deriving
    # containment from the winner marked those `contained: true` while their
    # score was 0.0, because nothing was prevented and no invariant fired. A
    # round cannot be both fully contained and worth nothing.
    outcome = round_summary.get("outcome")
    contained = (
        outcome == "CONTAINED" if outcome
        else round_summary.get("winner") == "BLUE"
    )

    started_at = _first_offset(events, "ATTACK_STARTED")
    detected_at = _first_offset(events, "INVARIANT_VIOLATION")
    if detected_at is None:
        detected_at = _first_offset(
            events, "DECEPTION_LAB_VERDICT",
            predicate=lambda e: e.get("payload", {}).get("verdict") == "DECEPTION_DETECTED",
        )
    if detected_at is None:
        detected_at = _first_offset(
            events, "SETTLEMENT_RECONCILIATION_VERDICT",
            predicate=lambda e: e.get("payload", {}).get("verdict") == "CONFLICT_DETECTED",
        )
    time_to_detection_ms = (
        round(detected_at - started_at, 3)
        if started_at is not None and detected_at is not None and detected_at >= started_at
        else None
    )

    economic_exposure_prevented_inr = round(sum(
        float(e.get("payload", {}).get("overshoot", 0.0) or 0.0)
        for e in events if e.get("event_type") == "INVARIANT_VIOLATION"
    ) + sum(
        float(e.get("payload", {}).get("economic_exposure_at_risk", 0.0) or 0.0)
        for e in events if e.get("event_type") == "SETTLEMENT_RECONCILIATION_VERDICT"
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

    # ---------------------------------------------------------- chain score
    #
    # REBUILT. The previous formula was
    #     0.5*contained + 0.3*speed + 0.2*(exposure_prevented > 0)
    # in which `contained` and `exposure_prevented > 0` are very nearly the
    # SAME boolean (exposure is prevented almost exactly when an invariant
    # fires, which is almost exactly when the round is contained). So 70% of
    # the score was one boolean counted twice, and the remaining 30% was a
    # stopwatch on `asyncio.sleep` - `time_to_detection_ms` is the gap between
    # two events whose spacing is set by the orchestrator's presentation
    # pacing constants, so "detection speed" measured the animation.
    #
    # The replacement uses three genuinely independent quantities, none of
    # which is wall-clock:
    #
    #   exposure     - what SHARE of the attempted objective was stopped
    #                  (a magnitude, not a boolean: stopping Rs 200 of a
    #                  Rs 12,000 objective is not the same as stopping all of it)
    #   earliness    - at WHICH STEP detection happened, as a fraction of the
    #                  steps attempted. Step index is set by the attack's own
    #                  structure, not by presentation pacing, so this is a real
    #                  property of the defence: catching a 4-leg split on leg 2
    #                  is genuinely better than catching it on leg 4.
    #
    # SECOND correction, after a follow-up review: an intermediate version kept
    # `contained` as a third term at 0.50. That was still half the score being
    # one boolean - and worse, a boolean that is very nearly a THRESHOLD VIEW of
    # `exposure_component`: a round is contained almost exactly when exposure
    # was prevented. Counting both meant the same fact set half the score and
    # then contributed to the other half as well.
    #
    # So the boolean is gone from the score. `contained` is still reported,
    # because it is the plainest thing a judge wants to know - but it is a FACT
    # about the round, not a term. What remains are two magnitudes that can move
    # independently: an attack can be fully stopped late (high exposure, low
    # earliness) or caught on step one after most of the money already moved
    # (low exposure, high earliness), and the score distinguishes them.
    #
    # Still a heuristic composite with no external ground truth, and still
    # labelled as one.
    attempted_total = sum(
        float((sr.get("tx", {}) or {}).get("amount", 0.0) or 0.0) for sr in step_results
    ) or 0.0
    exposure_component = (
        min(1.0, economic_exposure_prevented_inr / attempted_total)
        if attempted_total > 0 else 0.0
    )

    detected_step = next(
        (i for i, sr in enumerate(step_results, start=1) if sr.get("proof")), None
    )
    earliness_component = (
        1.0 - ((detected_step - 1) / max(1, len(step_results)))
        if detected_step is not None else 0.0
    )

    attack_chain_score = round(
        0.60 * exposure_component
        + 0.40 * earliness_component,
        3,
    )

    return {
        "strategy": strategy,
        "stage": stage,
        "detected": detected,
        "contained": contained,
        # RETAINED but RENAMED and demoted. This is the gap between two paced
        # events, so it measures the presentation timeline, not the engine. It
        # is reported for the event log and is deliberately NOT a term in
        # attack_chain_score any more. The inline engine's real latency is
        # measured properly in artifacts/benchmark/latency.json (p99 ~0.9 ms),
        # which is three orders of magnitude away from this number - conflating
        # the two was the risk.
        "wall_clock_to_detection_ms_presentation_paced": time_to_detection_ms,
        "detected_at_step": detected_step,
        "steps_attempted": len(step_results),
        "exposure_prevented_share": round(exposure_component, 3),
        "earliness_share": round(earliness_component, 3),
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

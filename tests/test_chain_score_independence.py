"""
`attack_chain_score` has now been corrected twice for the same defect, so it is
worth pinning properly.

  v1: 0.40*detected + 0.30*contained + 0.30*speed
      -> `detected` and `contained` are very nearly the same boolean, and
         "speed" was a stopwatch on the orchestrator's presentation sleeps.
         70% of the score was one boolean counted twice.

  v2: 0.50*contained + 0.30*exposure + 0.20*earliness
      -> better, but half the score was STILL a boolean - and one that is
         essentially a threshold view of the exposure term sitting next to it.
         A follow-up review called this out as "still 50% did-we-win".

  v3: 0.60*exposure + 0.40*earliness
      -> two magnitudes that move independently. `contained` is reported as a
         fact about the round, not as a term in its score.

These tests assert the v3 property directly: the score must be able to
distinguish two rounds that share a `contained` value, and must respond to each
input on its own.
"""

from typing import Any, Dict, List

import pytest

from app.kill_chain.scoring import score_round


def _round(*, amounts: List[float], detected_at: int | None, contained: bool,
           prevented: float) -> Dict[str, Any]:
    """
    Builds the shape score_round() consumes.

    `detected_at` is a 1-based step index, or None for undetected. A step counts
    as the detection point when it carries a proof.
    """
    steps = []
    for i, amount in enumerate(amounts, start=1):
        step: Dict[str, Any] = {"tx": {"amount": amount}}
        if detected_at is not None and i == detected_at:
            step["proof"] = {"invariant_code": "INV_01_GLOBAL_BUDGET_EXCEEDED"}
        steps.append(step)

    # Prevented exposure is DERIVED from the `overshoot` on INVARIANT_VIOLATION
    # events, not read off the round summary. An earlier draft of this fixture
    # passed `economic_exposure_prevented_inr` as a key and every exposure
    # assertion silently scored zero - the tests failed for the right reason.
    events: List[Dict[str, Any]] = []
    if detected_at is not None and prevented:
        events.append({
            "event_type": "INVARIANT_VIOLATION",
            # `round_id` is REQUIRED: score_round filters the recorder's
            # cumulative timeline down to this round, so an event without it is
            # silently dropped. Omitting it was the second way this fixture
            # scored zero exposure while looking correct.
            "round_id": 1,
            "payload": {"overshoot": prevented,
                        "invariant_code": "INV_01_GLOBAL_BUDGET_EXCEEDED"},
        })
    return {
        "strategy": "CROSS_RAIL_SPLIT",
        "winner": "BLUE" if contained else "RED",
        "detected": detected_at is not None,
        "round_number": 1,
        "events": events,
        "step_results": steps,
    }


def _score(**kw) -> float:
    return score_round(_round(**kw))["attack_chain_score"]


class TestTheScoreIsNotADisguisedBoolean:
    def test_two_contained_rounds_can_score_differently(self):
        """
        The v2 failure in one assertion: when `contained` carried 0.50, two
        contained rounds could differ by at most 0.50. Here, one is stopped
        entirely on the first leg and the other barely and late.
        """
        early_and_total = _score(
            amounts=[4000.0, 4000.0, 4000.0], detected_at=1,
            contained=True, prevented=12000.0,
        )
        late_and_partial = _score(
            amounts=[4000.0, 4000.0, 4000.0], detected_at=3,
            contained=True, prevented=500.0,
        )
        assert early_and_total > late_and_partial
        assert early_and_total - late_and_partial > 0.5, (
            "two contained rounds barely separate - the score is still dominated "
            "by the containment outcome"
        )

    def test_containment_alone_does_not_move_the_score(self):
        """Flipping ONLY the contained flag must change nothing."""
        common = dict(amounts=[4000.0, 4000.0], detected_at=2, prevented=4000.0)
        assert _score(contained=True, **common) == _score(contained=False, **common)


class TestEachInputMovesTheScoreOnItsOwn:
    def test_preventing_more_exposure_scores_higher(self):
        common = dict(amounts=[4000.0, 4000.0, 4000.0], detected_at=2, contained=True)
        assert _score(prevented=12000.0, **common) > _score(prevented=4000.0, **common)

    def test_catching_it_earlier_scores_higher(self):
        common = dict(amounts=[4000.0, 4000.0, 4000.0, 4000.0],
                      contained=True, prevented=8000.0)
        assert _score(detected_at=1, **common) > _score(detected_at=4, **common)

    def test_the_two_terms_are_not_the_same_quantity(self):
        """Late-but-total and early-but-partial are genuinely different rounds."""
        late_total = _score(amounts=[4000.0] * 4, detected_at=4,
                            contained=True, prevented=16000.0)
        early_partial = _score(amounts=[4000.0] * 4, detected_at=1,
                               contained=True, prevented=2000.0)
        assert late_total != early_partial


class TestBoundsAndDegenerateRounds:
    def test_a_perfect_round_scores_1_and_a_total_miss_scores_0(self):
        assert _score(amounts=[1000.0], detected_at=1, contained=True, prevented=1000.0) == 1.0
        assert _score(amounts=[1000.0], detected_at=None, contained=False, prevented=0.0) == 0.0

    @pytest.mark.parametrize("prevented", [0.0, 1000.0, 6000.0, 12000.0, 99999.0])
    def test_the_score_stays_within_bounds(self, prevented):
        s = _score(amounts=[4000.0] * 3, detected_at=2, contained=True, prevented=prevented)
        assert 0.0 <= s <= 1.0, f"score {s} outside [0, 1]"

    def test_a_round_with_no_steps_does_not_explode(self):
        result = score_round({
            "strategy": "CROSS_RAIL_SPLIT", "winner": "NONE", "detected": False,
            "round_number": 1, "events": [], "step_results": [],
        })
        assert 0.0 <= result["attack_chain_score"] <= 1.0

    def test_over_prevention_cannot_push_the_score_above_one(self):
        """Prevented > attempted is a bookkeeping error, not a 1.4 score."""
        assert _score(amounts=[100.0], detected_at=1, contained=True, prevented=10_000.0) == 1.0


class TestContainedIsStillReported:
    """Removing it from the SCORE must not remove it from the OUTPUT."""

    def test_contained_remains_a_reported_fact(self):
        result = score_round(_round(amounts=[1000.0], detected_at=1,
                                    contained=True, prevented=1000.0))
        assert "contained" in result
        assert isinstance(result["contained"], bool)

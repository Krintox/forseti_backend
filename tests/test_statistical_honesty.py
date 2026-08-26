"""
The headline table compares five architectures on a 64-transaction slice, and
for a while it read a 0.016 difference as "within 0.016 of its own seen-family
score, which is what generalisation actually looks like."

At n=64 a 95% Wilson interval on a recall near 0.83 is about +/-0.09. That
sentence was claiming to resolve a difference five times smaller than the
measurement error, on our own data, in the headline. Nobody had computed an
interval, so nobody could see it.

These tests pin the discipline that replaced it:

  1. the interval arithmetic is correct and bounded to [0, 1]
  2. every published cross-rail recall carries an interval and its n
  3. the separation we DO claim (aggregate feature vs no aggregate feature)
     survives the intervals
  4. the comparison we DECLINED to claim (held-out vs seen for the classifier)
     genuinely cannot be resolved at this n - so the retraction was warranted
     rather than performative
  5. the generated claim string says so
"""

import io
import json
import os

import pytest

from app.detector.baselines import _overlap, recall_with_ci, wilson_interval

ARTIFACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "artifacts", "evaluation", "baselines.json",
)


@pytest.fixture(scope="module")
def headline():
    if not os.path.exists(ARTIFACT):
        pytest.skip("baselines.json not built - run `python tasks.py all`")
    with io.open(ARTIFACT, encoding="utf-8") as f:
        return json.load(f)["headline_finding"]


class TestTheIntervalArithmetic:
    def test_it_never_leaves_the_unit_interval(self):
        """The normal approximation returns bounds above 1.0 here; Wilson must not."""
        for n in (5, 10, 64, 500):
            for successes in (0, 1, n - 1, n):
                low, high = wilson_interval(successes, n)
                assert 0.0 <= low <= high <= 1.0, f"{successes}/{n} -> [{low}, {high}]"

    def test_it_is_symmetric_under_relabelling(self):
        low, high = wilson_interval(11, 64)
        flipped_low, flipped_high = wilson_interval(64 - 11, 64)
        assert pytest.approx(1 - high, abs=1e-9) == flipped_low
        assert pytest.approx(1 - low, abs=1e-9) == flipped_high

    def test_it_narrows_as_n_grows(self):
        widths = [wilson_interval(round(0.83 * n), n) for n in (16, 64, 256, 1024)]
        halfwidths = [(h - l) / 2 for l, h in widths]
        assert halfwidths == sorted(halfwidths, reverse=True)

    def test_a_degenerate_sample_does_not_raise(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)
        assert recall_with_ci({"recall": 0.5, "n": 0}) is None
        assert recall_with_ci(None) is None

    def test_the_reported_success_count_is_consistent_with_the_recall(self):
        band = recall_with_ci({"recall": 0.8281, "n": 64})
        assert band["caught"] == 53
        assert band["n"] == 64
        assert band["ci95"][0] < band["recall"] < band["ci95"][1]


class TestEveryPublishedRecallCarriesItsUncertainty:
    def test_the_artifact_publishes_intervals(self, headline):
        assert "cross_rail_split_recall_ci95" in headline, (
            "point estimates shipped without intervals - that is how the "
            "overclaim happened the first time"
        )

    @pytest.mark.parametrize("condition", ["held_out", "seen"])
    def test_every_architecture_reports_n_and_an_interval(self, headline, condition):
        bands = headline["cross_rail_split_recall_ci95"][condition]
        assert bands, f"no intervals published for {condition}"
        for arch, band in bands.items():
            assert band["n"] > 0, f"{arch} reports no sample size"
            assert len(band["ci95"]) == 2
            assert band["ci95"][0] <= band["recall"] <= band["ci95"][1], (
                f"{arch}: point estimate outside its own interval"
            )


class TestTheClaimsMatchWhatTheDataSupports:
    def test_the_aggregate_feature_separation_is_real(self, headline):
        """This one we DO claim - it must survive the intervals."""
        held = headline["cross_rail_split_recall_ci95"]["held_out"]
        with_dtl, without_dtl = held["hybrid_dtl_ml"], held["ml_without_dtl"]
        assert not _overlap(with_dtl, without_dtl), (
            "the headline separation is inside the noise; the README claims it is not"
        )

    def test_the_generalisation_gap_is_NOT_resolvable(self, headline):
        """
        This one we deliberately do NOT claim. If a future run makes it
        resolvable, the README's careful hedge becomes an UNDERclaim and should
        be revisited - so this test failing is informative either way.
        """
        ci = headline["cross_rail_split_recall_ci95"]
        held, seen = ci["held_out"]["hybrid_dtl_ml"], ci["seen"]["hybrid_dtl_ml"]
        assert _overlap(held, seen), (
            "held-out and seen are now separable at this n - the README says they "
            "are not, and should be updated"
        )

    def test_the_invariants_two_columns_are_identical_not_merely_close(self, headline):
        out = headline["cross_rail_split_recall_when_family_held_out"]["dtl_invariant_only"]
        seen = headline["cross_rail_split_recall_when_family_seen"]["dtl_invariant_only"]
        assert out == seen, (
            "holdout-independence is an identity, not a measurement - if these "
            "differ, the invariant is reading something it should not"
        )

    def test_the_generated_claim_declines_to_prove_generalisation(self, headline):
        claim = headline["claim"]
        assert "cannot resolve" in claim
        assert "n=64" in claim or "n=" in claim
        assert "by construction" in claim.lower()

    def test_the_generated_claim_is_not_the_old_hardcoded_sentence(self, headline):
        assert "Per-transaction ML cannot detect cross-rail splitting" not in headline["claim"], (
            "the claim reverted to a hardcoded sentence the numbers contradict"
        )

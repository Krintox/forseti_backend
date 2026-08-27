"""
Containment must never relax in response to a NEW violation.

Found by tracing the escalation ladder by hand. Blue assigned whatever policy a
violation indicated, unconditionally - so an agent SUSPENDED after three rail
breaches could trigger one different, first-occurrence violation and be handed
STEP_UP_VERIFICATION instead, dropping four rungs and resuming spend.

Misbehaving again was a route OUT of containment. These tests close it.
"""

import pytest

from app.arena.orchestrator import AUTHORITY_ID
from app.dtl.ledger import DTLLedger
from app.feedback.policy_adapter import BluePolicyAdapter
from app.models.state import DefensePolicy, policy_rung, stricter_policy

ALL_INVARIANTS = [
    "INV_01_GLOBAL_BUDGET_EXCEEDED",
    "INV_02_SEMANTIC_INTENT_DRIFT",
    "INV_03_UNAUTHORIZED_MCC",
    "INV_04_UNAUTHORIZED_RAIL",
    "INV_05_PER_TX_CAP_EXCEEDED",
    "INV_06_AUTHORITY_EXPIRED",
    "INV_07_UNAUTHORIZED_BENEFICIARY",
]


def _auth():
    return DTLLedger().get_authority(AUTHORITY_ID)


class TestTheRungHelper:
    def test_the_ladder_orders_severity(self):
        assert policy_rung(DefensePolicy.STANDARD) == 0
        assert policy_rung(DefensePolicy.AGENT_SUSPENDED) == max(
            policy_rung(p) for p in DefensePolicy
        )

    def test_stricter_policy_keeps_the_higher_rung(self):
        assert stricter_policy(DefensePolicy.AGENT_SUSPENDED,
                               DefensePolicy.STEP_UP_VERIFICATION) == DefensePolicy.AGENT_SUSPENDED
        assert stricter_policy(DefensePolicy.STANDARD,
                               DefensePolicy.STEP_UP_VERIFICATION) == DefensePolicy.STEP_UP_VERIFICATION

    def test_an_unknown_policy_does_not_outrank_a_real_one(self):
        assert stricter_policy(DefensePolicy.AGENT_SUSPENDED, "NOT_A_POLICY") == DefensePolicy.AGENT_SUSPENDED


class TestANewViolationNeverRelaxesContainment:
    @pytest.mark.parametrize("code", ALL_INVARIANTS)
    def test_no_invariant_can_lower_the_policy_from_suspended(self, code):
        """The exact bypass: get suspended, then trip something else once."""
        auth = _auth()
        BluePolicyAdapter.adapt_policy(auth, "INV_04_UNAUTHORIZED_RAIL", violation_count=3)
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED

        BluePolicyAdapter.adapt_policy(auth, code, violation_count=1)
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED, (
            f"a single {code} dropped the agent out of suspension"
        )

    @pytest.mark.parametrize("code", ALL_INVARIANTS)
    def test_the_rung_never_decreases_across_a_long_mixed_sequence(self, code):
        auth = _auth()
        rungs = [policy_rung(auth.active_policy)]
        for n, other in enumerate(ALL_INVARIANTS * 2, start=1):
            BluePolicyAdapter.adapt_policy(auth, other, violation_count=(n % 4) + 1)
            rungs.append(policy_rung(auth.active_policy))
        assert rungs == sorted(rungs), f"policy moved DOWN the ladder: {rungs}"

    def test_escalation_still_works_upward(self):
        auth = _auth()
        seen = []
        for n in (1, 2, 3):
            BluePolicyAdapter.adapt_policy(auth, "INV_04_UNAUTHORIZED_RAIL", violation_count=n)
            seen.append(auth.active_policy)
        assert seen[0] != seen[-1], "the ladder stopped climbing"
        assert seen[-1] == DefensePolicy.AGENT_SUSPENDED
        assert [policy_rung(p) for p in seen] == sorted(policy_rung(p) for p in seen)


class TestOnlyReConsentLowersIt:
    def test_resetting_the_authority_clears_containment(self):
        """A fresh grant is the principal re-consenting - that MAY lower it."""
        led = DTLLedger()
        auth = led.get_authority(AUTHORITY_ID)
        BluePolicyAdapter.adapt_policy(auth, "INV_04_UNAUTHORIZED_RAIL", violation_count=3)
        assert auth.active_policy == DefensePolicy.AGENT_SUSPENDED

        led.reset_authority(AUTHORITY_ID)
        assert led.get_authority(AUTHORITY_ID).active_policy != DefensePolicy.AGENT_SUSPENDED

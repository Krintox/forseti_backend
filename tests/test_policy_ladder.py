"""
The escalation ladder is the one thing a judge watches climb, so its integrity
matters more than most. It used to live twice: once as `DefensePolicy` in the
backend and once as a hand-written array in the Policy Center page. The copies
drifted - the frontend never listed AGENT_SUSPENDED - so the TOP rung, the state
where the mandate is paused entirely, rendered as no active policy at all.

These tests pin the single-source-of-truth property that replaced it.
"""

import pytest

from app.arena.orchestrator import AUTHORITY_ID, ArenaBattleOrchestrator
from app.models.state import POLICY_LADDER, DefensePolicy, DTLGlobalAuthorityState


class TestLadderCoversTheEnum:
    def test_every_defense_policy_has_exactly_one_rung(self):
        codes = [row["code"] for row in POLICY_LADDER]
        assert sorted(codes) == sorted(p.value for p in DefensePolicy)
        assert len(codes) == len(set(codes)), "a policy appears on two rungs"

    def test_rungs_are_a_dense_ordered_sequence(self):
        rungs = [row["rung"] for row in POLICY_LADDER]
        assert rungs == list(range(len(POLICY_LADDER))), (
            "rungs must be 0..n-1 in order - the UI renders 'passed' by comparing them"
        )

    def test_agent_suspended_is_the_top_rung(self):
        top = max(POLICY_LADDER, key=lambda r: r["rung"])
        assert top["code"] == DefensePolicy.AGENT_SUSPENDED.value

    def test_every_rung_explains_what_it_enforces(self):
        for row in POLICY_LADDER:
            assert row["description"].strip(), f"{row['code']} has no description"
            assert row["enforced_effect"].strip(), f"{row['code']} has no enforced_effect"


class TestLadderQuotesRealNumbers:
    """The prose on each rung must match the constant the engine actually uses."""

    def test_step_up_cap_in_prose_matches_the_enforced_cap(self):
        auth = DTLGlobalAuthorityState(
            authority_id="a", principal="p", agent_id="g",
            active_policy=DefensePolicy.STEP_UP_VERIFICATION,
        )
        row = next(r for r in POLICY_LADDER if r["code"] == "STEP_UP_VERIFICATION")
        assert str(int(auth.effective_per_transaction_cap)) in row["enforced_effect"].replace(",", "")

    def test_quarantine_cap_in_prose_matches_the_enforced_cap(self):
        auth = DTLGlobalAuthorityState(
            authority_id="a", principal="p", agent_id="g",
            active_policy=DefensePolicy.CAPABILITY_QUARANTINED,
        )
        row = next(r for r in POLICY_LADDER if r["code"] == "CAPABILITY_QUARANTINED")
        assert str(int(auth.effective_per_transaction_cap)) in row["enforced_effect"].replace(",", "")

    def test_tightened_headroom_prose_matches_the_withheld_share(self):
        auth = DTLGlobalAuthorityState(
            authority_id="a", principal="p", agent_id="g",
            global_budget_ceiling=10_000.0,
            active_policy=DefensePolicy.TIGHTENED_HEADROOM_V2,
        )
        withheld_pct = round(
            (auth.global_budget_ceiling - auth.effective_ceiling)
            / auth.global_budget_ceiling * 100
        )
        row = next(r for r in POLICY_LADDER if r["code"] == "TIGHTENED_HEADROOM_V2")
        assert f"{withheld_pct}%" in row["enforced_effect"]


class TestLadderIsServedToTheUI:
    def test_state_payload_carries_the_ladder_and_the_live_overlay(self):
        state = ArenaBattleOrchestrator().get_state()
        assert len(state["policy_ladder"]) == len(DefensePolicy)
        assert state["policy_overlay"]["active_policy"] == state["active_policy"]

    @pytest.mark.parametrize("policy", list(DefensePolicy))
    def test_whatever_policy_is_active_the_ladder_contains_it(self, policy):
        orch = ArenaBattleOrchestrator()
        orch.ledger.get_authority(AUTHORITY_ID).active_policy = policy
        state = orch.get_state()
        codes = {row["code"] for row in state["policy_ladder"]}
        assert state["active_policy"] in codes, (
            f"{policy.value} is active but absent from the published ladder - "
            "the Policy Center would highlight nothing"
        )
